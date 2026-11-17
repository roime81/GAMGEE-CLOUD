from __future__ import annotations

from typing import Any


def resume_replicas(deployment: dict[str, Any], *, maximum: int = 20) -> int:
    """Restore the last positive GAMGEE replica count, safely defaulting to one."""
    annotations = deployment.get("metadata", {}).get("annotations", {})
    raw = annotations.get("gamgee.cloud/previous-replicas", "1")
    try:
        replicas = int(raw)
    except (TypeError, ValueError):
        return 1
    if replicas < 1:
        return 1
    return min(replicas, maximum)
