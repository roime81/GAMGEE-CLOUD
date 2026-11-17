from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class IdempotencyConflict(ValueError):
    """An idempotency key was reused for a different request."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class ResourceStore:
    """SQLite desired/observed state, operations and idempotency ledger."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    name TEXT NOT NULL,
                    desired_json TEXT NOT NULL,
                    observed_json TEXT NOT NULL DEFAULT '{}',
                    generation INTEGER NOT NULL,
                    observed_generation INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    UNIQUE(kind, workspace, name)
                );
                CREATE INDEX IF NOT EXISTS idx_resources_reconcile
                    ON resources(phase, next_attempt_at, updated_at);
                CREATE INDEX IF NOT EXISTS idx_resources_workspace
                    ON resources(workspace, kind, updated_at);
                CREATE TABLE IF NOT EXISTS resource_operations (
                    id TEXT PRIMARY KEY,
                    resource_id TEXT NOT NULL REFERENCES resources(id),
                    generation INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(resource_id, generation)
                );
                CREATE INDEX IF NOT EXISTS idx_resource_operations_resource
                    ON resource_operations(resource_id, generation);
                CREATE TABLE IF NOT EXISTS resource_idempotency (
                    workspace TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(workspace, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS resource_events (
                    id TEXT PRIMARY KEY,
                    resource_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_resource_events_resource
                    ON resource_events(resource_id, created_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=20000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            if connection.in_transaction:
                connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _resource(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row["id"],
            "kind": row["kind"],
            "workspace": row["workspace"],
            "name": row["name"],
            "desired": json.loads(row["desired_json"]),
            "observed": json.loads(row["observed_json"]),
            "generation": row["generation"],
            "observed_generation": row["observed_generation"],
            "phase": row["phase"],
            "retry_count": row["retry_count"],
            "next_attempt_at": row["next_attempt_at"],
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "deleted_at": row["deleted_at"],
        }

    def _get_in(self, connection: sqlite3.Connection, resource_id: str) -> dict[str, Any] | None:
        row = connection.execute("SELECT * FROM resources WHERE id=?", (resource_id,)).fetchone()
        return self._resource(row)

    def get(self, resource_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            return self._get_in(connection, resource_id)

    def find(self, kind: str, workspace: str, name: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM resources WHERE kind=? AND workspace=? AND name=?",
                (kind, workspace, name),
            ).fetchone()
        return self._resource(row)

    def list(
        self,
        *,
        workspace: str | None = None,
        kind: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if workspace is not None:
            clauses.append("workspace=?")
            values.append(workspace)
        if kind is not None:
            clauses.append("kind=?")
            values.append(kind)
        if not include_deleted:
            clauses.append("phase != 'deleted'")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        values.append(max(1, min(limit, 500)))
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM resources{where} ORDER BY updated_at DESC LIMIT ?",
                values,
            ).fetchall()
        return [self._resource(row) for row in rows]  # type: ignore[misc]

    def _create_operation(
        self,
        connection: sqlite3.Connection,
        *,
        resource_id: str,
        generation: int,
        action: str,
        now: str,
    ) -> str:
        operation_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO resource_operations
            (id, resource_id, generation, action, status, attempts, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)
            """,
            (operation_id, resource_id, generation, action, now, now),
        )
        return operation_id

    def _record_event(
        self,
        connection: sqlite3.Connection,
        resource_id: str,
        generation: int,
        event_type: str,
        payload: dict[str, Any] | None,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO resource_events (id, resource_id, generation, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid4()), resource_id, generation, event_type, _canonical(payload or {}), now),
        )

    def _idempotent_replay(
        self,
        connection: sqlite3.Connection,
        workspace: str,
        key: str | None,
        fingerprint: str,
    ) -> tuple[dict[str, Any], str] | None:
        if not key:
            return None
        row = connection.execute(
            "SELECT request_hash, resource_id, operation_id FROM resource_idempotency WHERE workspace=? AND idempotency_key=?",
            (workspace, key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != fingerprint:
            raise IdempotencyConflict("Idempotency-Key was already used for a different request.")
        resource = self._get_in(connection, row["resource_id"])
        if resource is None:
            raise RuntimeError("Idempotency record references a missing resource.")
        return resource, row["operation_id"]

    def upsert(
        self,
        *,
        kind: str,
        workspace: str,
        name: str,
        desired: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], str, bool]:
        if idempotency_key is not None and not (1 <= len(idempotency_key) <= 128):
            raise ValueError("Idempotency-Key must contain between 1 and 128 characters.")
        fingerprint = request_fingerprint(
            {"action": "apply", "kind": kind, "workspace": workspace, "name": name, "desired": desired}
        )
        desired_json = _canonical(desired)
        now = _stamp()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_replay(connection, workspace, idempotency_key, fingerprint)
            if replay:
                connection.commit()
                return replay[0], replay[1], True

            row = connection.execute(
                "SELECT * FROM resources WHERE kind=? AND workspace=? AND name=?",
                (kind, workspace, name),
            ).fetchone()
            if row is None:
                resource_id = str(uuid4())
                generation = 1
                connection.execute(
                    """
                    INSERT INTO resources
                    (id, kind, workspace, name, desired_json, observed_json, generation,
                     observed_generation, phase, retry_count, next_attempt_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, '{}', ?, 0, 'pending', 0, ?, ?, ?)
                    """,
                    (resource_id, kind, workspace, name, desired_json, generation, now, now, now),
                )
                action = "create"
            else:
                resource_id = row["id"]
                current = self._resource(row)
                if current and current["phase"] != "deleted" and row["desired_json"] == desired_json:
                    operation = connection.execute(
                        "SELECT id FROM resource_operations WHERE resource_id=? AND generation=?",
                        (resource_id, row["generation"]),
                    ).fetchone()
                    operation_id = operation["id"] if operation else ""
                    if idempotency_key:
                        connection.execute(
                            """
                            INSERT INTO resource_idempotency
                            (workspace, idempotency_key, request_hash, resource_id, operation_id, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (workspace, idempotency_key, fingerprint, resource_id, operation_id, now),
                        )
                    connection.commit()
                    return current, operation_id, True
                generation = int(row["generation"]) + 1
                connection.execute(
                    """
                    UPDATE resources SET desired_json=?, generation=?, phase='pending',
                        retry_count=0, next_attempt_at=?, last_error=NULL, deleted_at=NULL, updated_at=?
                    WHERE id=?
                    """,
                    (desired_json, generation, now, now, resource_id),
                )
                action = "update"

            operation_id = self._create_operation(
                connection, resource_id=resource_id, generation=generation, action=action, now=now
            )
            self._record_event(
                connection,
                resource_id,
                generation,
                "desired.created" if action == "create" else "desired.updated",
                {"kind": kind, "workspace": workspace, "name": name},
                now,
            )
            if idempotency_key:
                connection.execute(
                    """
                    INSERT INTO resource_idempotency
                    (workspace, idempotency_key, request_hash, resource_id, operation_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (workspace, idempotency_key, fingerprint, resource_id, operation_id, now),
                )
            result = self._get_in(connection, resource_id)
            connection.commit()
        return result, operation_id, False  # type: ignore[return-value]

    def delete(
        self,
        resource_id: str,
        *,
        workspace: str,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None, bool]:
        if idempotency_key is not None and not (1 <= len(idempotency_key) <= 128):
            raise ValueError("Idempotency-Key must contain between 1 and 128 characters.")
        fingerprint = request_fingerprint({"action": "delete", "resource_id": resource_id, "workspace": workspace})
        now = _stamp()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_replay(connection, workspace, idempotency_key, fingerprint)
            if replay:
                connection.commit()
                return replay[0], replay[1], True
            row = connection.execute(
                "SELECT * FROM resources WHERE id=? AND workspace=?",
                (resource_id, workspace),
            ).fetchone()
            if row is None:
                connection.commit()
                return None, None, False
            current = self._resource(row)
            if current and current["phase"] == "deleted":
                operation = connection.execute(
                    "SELECT id FROM resource_operations WHERE resource_id=? ORDER BY generation DESC LIMIT 1",
                    (resource_id,),
                ).fetchone()
                operation_id = operation["id"] if operation else None
                if idempotency_key and operation_id:
                    connection.execute(
                        """
                        INSERT INTO resource_idempotency
                        (workspace, idempotency_key, request_hash, resource_id, operation_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (workspace, idempotency_key, fingerprint, resource_id, operation_id, now),
                    )
                connection.commit()
                return current, operation_id, True

            generation = int(row["generation"]) + 1
            operation_id = self._create_operation(
                connection, resource_id=resource_id, generation=generation, action="delete", now=now
            )
            connection.execute(
                """
                UPDATE resources SET desired_json=?, generation=?, phase='deleting',
                    retry_count=0, next_attempt_at=?, last_error=NULL, updated_at=?
                WHERE id=?
                """,
                (_canonical({"deleted": True}), generation, now, now, resource_id),
            )
            self._record_event(connection, resource_id, generation, "desired.deleted", {}, now)
            if idempotency_key:
                connection.execute(
                    """
                    INSERT INTO resource_idempotency
                    (workspace, idempotency_key, request_hash, resource_id, operation_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (workspace, idempotency_key, fingerprint, resource_id, operation_id, now),
                )
            result = self._get_in(connection, resource_id)
            connection.commit()
        return result, operation_id, False  # type: ignore[return-value]

    def claim_due(self, *, limit: int = 25, stale_seconds: int = 180) -> list[dict[str, Any]]:
        now_dt = utc_now()
        now = _stamp(now_dt)
        stale = _stamp(now_dt - timedelta(seconds=stale_seconds))
        claimed: list[dict[str, Any]] = []
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE resources SET phase='pending', next_attempt_at=?, updated_at=?
                WHERE phase='reconciling' AND updated_at<?
                """,
                (now, now, stale),
            )
            rows = connection.execute(
                """
                SELECT * FROM resources
                WHERE phase IN ('pending', 'error', 'deleting') AND next_attempt_at<=?
                ORDER BY created_at ASC LIMIT ?
                """,
                (now, max(1, min(limit, 100))),
            ).fetchall()
            for row in rows:
                resource_id = row["id"]
                generation = row["generation"]
                operation = connection.execute(
                    "SELECT * FROM resource_operations WHERE resource_id=? AND generation=?",
                    (resource_id, generation),
                ).fetchone()
                if operation is None:
                    continue
                connection.execute(
                    "UPDATE resources SET phase='reconciling', updated_at=? WHERE id=? AND generation=?",
                    (now, resource_id, generation),
                )
                connection.execute(
                    """
                    UPDATE resource_operations SET status='running', attempts=attempts+1, updated_at=?
                    WHERE id=?
                    """,
                    (now, operation["id"]),
                )
                self._record_event(
                    connection,
                    resource_id,
                    generation,
                    "reconcile.started",
                    {"operation_id": operation["id"], "attempt": operation["attempts"] + 1},
                    now,
                )
                result = self._get_in(connection, resource_id)
                result["operation_id"] = operation["id"]
                claimed.append(result)
            connection.commit()
        return claimed

    def mark_success(
        self,
        *,
        resource_id: str,
        generation: int,
        observed: dict[str, Any],
        deleted: bool = False,
    ) -> None:
        now = _stamp()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT generation FROM resources WHERE id=?", (resource_id,)).fetchone()
            if row is None:
                connection.commit()
                return
            current_generation = int(row["generation"])
            phase = ("deleted" if deleted else "ready") if current_generation == generation else "pending"
            deleted_at = now if deleted and current_generation == generation else None
            connection.execute(
                """
                UPDATE resources SET observed_json=?, observed_generation=?,
                    phase=?, retry_count=0, next_attempt_at=?, last_error=NULL,
                    deleted_at=COALESCE(?, deleted_at), updated_at=? WHERE id=?
                """,
                (_canonical(observed), generation, phase, now, deleted_at, now, resource_id),
            )
            connection.execute(
                """
                UPDATE resource_operations SET status='succeeded', result_json=?, error=NULL, updated_at=?
                WHERE resource_id=? AND generation=?
                """,
                (_canonical(observed), now, resource_id, generation),
            )
            self._record_event(
                connection,
                resource_id,
                generation,
                "reconcile.deleted" if deleted else "reconcile.succeeded",
                observed,
                now,
            )
            connection.commit()

    def mark_failure(self, *, resource_id: str, generation: int, error: str) -> None:
        now_dt = utc_now()
        now = _stamp(now_dt)
        safe_error = (error or "Unknown reconciliation error")[:2000]
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT generation, retry_count FROM resources WHERE id=?",
                (resource_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return
            current_generation = int(row["generation"])
            if current_generation == generation:
                retry_count = int(row["retry_count"]) + 1
                delay = min(5 * (2 ** min(retry_count - 1, 6)), 300)
                phase = "error"
                next_attempt = _stamp(now_dt + timedelta(seconds=delay))
                last_error = safe_error
                operation_status = "retrying"
            else:
                retry_count = 0
                phase = "pending"
                next_attempt = now
                last_error = None
                operation_status = "superseded"
            connection.execute(
                """
                UPDATE resources SET phase=?, retry_count=?, next_attempt_at=?,
                    last_error=?, updated_at=? WHERE id=?
                """,
                (phase, retry_count, next_attempt, last_error, now, resource_id),
            )
            connection.execute(
                """
                UPDATE resource_operations SET status=?, error=?, updated_at=?
                WHERE resource_id=? AND generation=?
                """,
                (operation_status, safe_error, now, resource_id, generation),
            )
            self._record_event(
                connection,
                resource_id,
                generation,
                "reconcile.failed",
                {"error": safe_error, "retry_in_seconds": delay if current_generation == generation else 0},
                now,
            )
            connection.commit()

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM resource_operations WHERE id=?",
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "resource_id": row["resource_id"],
            "generation": row["generation"],
            "action": row["action"],
            "status": row["status"],
            "attempts": row["attempts"],
            "error": row["error"],
            "result": json.loads(row["result_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_operations(self, resource_id: str, *, limit: int = 25) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id FROM resource_operations WHERE resource_id=? ORDER BY generation DESC LIMIT ?",
                (resource_id, max(1, min(limit, 100))),
            ).fetchall()
        return [item for row in rows if (item := self.get_operation(row["id"])) is not None]

    def events(self, resource_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM resource_events WHERE resource_id=?
                ORDER BY created_at DESC LIMIT ?
                """,
                (resource_id, max(1, min(limit, 500))),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "resource_id": row["resource_id"],
                "generation": row["generation"],
                "type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
