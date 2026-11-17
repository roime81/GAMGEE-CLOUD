from __future__ import annotations

import logging
from typing import Any, Protocol

from tower.resource_store import ResourceStore

logger = logging.getLogger("gamgee.resource-engine")


class ResourceDriver(Protocol):
    kind: str

    def reconcile(self, resource: dict[str, Any]) -> dict[str, Any]:
        """Converge a resource's observed infrastructure to its desired state."""


class ResourceEngine:
    """Small driver registry and desired-state reconciliation coordinator."""

    def __init__(self, store: ResourceStore, drivers: list[ResourceDriver]):
        self.store = store
        self.drivers = {driver.kind: driver for driver in drivers}

    def submit(
        self,
        *,
        kind: str,
        workspace: str,
        name: str,
        desired: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], str, bool]:
        if kind not in self.drivers:
            raise ValueError(f"No resource driver is registered for kind '{kind}'.")
        return self.store.upsert(
            kind=kind,
            workspace=workspace,
            name=name,
            desired=desired,
            idempotency_key=idempotency_key,
        )

    def delete(
        self,
        resource_id: str,
        *,
        workspace: str,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None, bool]:
        resource = self.store.get(resource_id)
        if resource is None or resource["workspace"] != workspace:
            return None, None, False
        if resource["kind"] not in self.drivers:
            raise ValueError(f"No resource driver is registered for kind '{resource['kind']}'.")
        return self.store.delete(
            resource_id,
            workspace=workspace,
            idempotency_key=idempotency_key,
        )

    def reconcile_due(self, *, limit: int = 25) -> dict[str, int]:
        claimed = self.store.claim_due(limit=limit)
        succeeded = failed = 0
        for resource in claimed:
            driver = self.drivers.get(resource["kind"])
            try:
                if driver is None:
                    raise RuntimeError(f"No driver is registered for kind '{resource['kind']}'.")
                if resource.get("claimed_from_phase") == "progressing" and hasattr(driver, "observe"):
                    observed = driver.observe(resource)
                else:
                    observed = driver.reconcile(resource)
                deleted = bool(resource["desired"].get("deleted"))
                if deleted and observed.get("state") != "deleted":
                    self.store.mark_progress(
                        resource_id=resource["id"],
                        generation=resource["generation"],
                        observed=observed,
                        phase="deleting",
                    )
                elif not deleted and observed.get("state") == "progressing":
                    self.store.mark_progress(
                        resource_id=resource["id"],
                        generation=resource["generation"],
                        observed=observed,
                    )
                else:
                    self.store.mark_success(
                        resource_id=resource["id"],
                        generation=resource["generation"],
                        observed=observed,
                        deleted=deleted,
                    )
                succeeded += 1
            except Exception as exc:
                logger.exception(
                    "Resource reconciliation failed: id=%s kind=%s generation=%s",
                    resource["id"],
                    resource["kind"],
                    resource["generation"],
                )
                self.store.mark_failure(
                    resource_id=resource["id"],
                    generation=resource["generation"],
                    error=f"{type(exc).__name__}: {exc}",
                )
                failed += 1
        return {"claimed": len(claimed), "succeeded": succeeded, "failed": failed}
