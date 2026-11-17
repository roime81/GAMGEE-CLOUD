from __future__ import annotations

from typing import Any


def is_gamgee_managed(resource: dict[str, Any], workspace: str) -> bool:
    """Return true only for a resource explicitly owned by GAMGEE and this workspace."""
    labels = resource.get("metadata", {}).get("labels", {})
    return (
        labels.get("gamgee.cloud/managed-by") == "gamgee"
        and labels.get("gamgee.cloud/tenant") == workspace
    )
