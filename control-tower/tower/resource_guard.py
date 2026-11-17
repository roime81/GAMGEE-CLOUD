from __future__ import annotations

from typing import Any


def is_gamgee_managed(resource: dict[str, Any], workspace: str) -> bool:
    """Return true only for a resource explicitly owned by GAMGEE and this workspace."""
    labels = resource.get("metadata", {}).get("labels", {})
    return (
        labels.get("gamgee.cloud/managed-by") == "gamgee"
        and labels.get("gamgee.cloud/tenant") == workspace
    )


def is_gamgee_resource(resource: dict[str, Any], resource_id: str) -> bool:
    """Require the unique Resource Engine identity, not just a matching name."""
    labels = resource.get("metadata", {}).get("labels", {})
    return labels.get("gamgee.cloud/resource-id") == resource_id
