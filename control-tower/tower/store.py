from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    profiles_json TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    logs TEXT NOT NULL DEFAULT ''
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def create(self, profiles: list[str], plan_hash: str, details: dict[str, Any], status: str = "pending") -> dict[str, Any]:
        job_id = str(uuid4())
        now = _now()
        with _lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs (id, created_at, updated_at, status, profiles_json, plan_hash, details_json, logs) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, now, now, status, json.dumps(profiles), plan_hash, json.dumps(details), ""),
            )
        return self.get(job_id)  # type: ignore[return-value]

    def update(self, job_id: str, *, status: str | None = None, details: dict[str, Any] | None = None, append_log: str | None = None) -> None:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        next_status = status or current["status"]
        next_details = details if details is not None else current["details"]
        next_logs = current["logs"] + (append_log or "")
        with _lock, self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET updated_at=?, status=?, details_json=?, logs=? WHERE id=?",
                (_now(), next_status, json.dumps(next_details), next_logs[-64000:], job_id),
            )

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._decode(row) if row else None

    def list(self, limit: int = 25) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)
            ).fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "status": row["status"],
            "profiles": json.loads(row["profiles_json"]),
            "plan_hash": row["plan_hash"],
            "details": json.loads(row["details_json"]),
            "logs": row["logs"],
        }
