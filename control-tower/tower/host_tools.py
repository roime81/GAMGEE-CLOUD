from __future__ import annotations

import os
import shutil
from pathlib import Path

from tower.config import ROOT


def resolve_tool(name: str, *, root: Path = ROOT) -> str | None:
    """Resolve a host command, including tools installed in GAMGEE's own venv."""
    if not name or Path(name).name != name:
        return None
    system_path = shutil.which(name)
    if system_path:
        return system_path
    bundled_path = root / "control-tower" / ".venv" / "bin" / name
    if bundled_path.is_file() and os.access(bundled_path, os.X_OK):
        return str(bundled_path)
    return None
