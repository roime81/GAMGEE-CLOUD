from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = ROOT / "control-tower" / "profiles"
STATIC_DIR = ROOT / "control-tower" / "tower" / "static"


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """Runtime settings. Mutating operations are disabled unless explicitly enabled."""

    api_token: str = os.getenv("GAMGEE_TOWER_API_TOKEN", "")
    allow_apply: bool = _truthy("GAMGEE_TOWER_ALLOW_APPLY")
    host: str = os.getenv("GAMGEE_TOWER_HOST", "127.0.0.1")
    port: int = int(os.getenv("GAMGEE_TOWER_PORT", "8091"))
    database_path: Path = Path(
        os.getenv(
            "GAMGEE_TOWER_DB_PATH",
            str(ROOT / "control-tower" / "var" / "jobs.sqlite3"),
        )
    )
    job_timeout_seconds: int = int(os.getenv("GAMGEE_TOWER_JOB_TIMEOUT", "3600"))


settings = Settings()
