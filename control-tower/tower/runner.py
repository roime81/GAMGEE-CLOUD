from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from tower.catalog import load_catalog
from tower.config import ROOT, settings
from tower.store import JobStore
from tower.host_tools import resolve_tool


def _run_playbook(playbook: str, timeout: int) -> tuple[int, str]:
    candidate = (ROOT / playbook).resolve()
    allowed_root = (ROOT / "automation" / "ansible").resolve()
    if allowed_root not in candidate.parents or not candidate.is_file():
        raise ValueError(f"Playbook is not an approved repository file: {playbook}")

    executable = resolve_tool("ansible-playbook")
    if not executable:
        raise RuntimeError("ansible-playbook was not found. Install Control Tower requirements first.")

    command = [
        executable,
        "-i",
        "localhost,",
        "--connection",
        "local",
        str(candidate),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
        env={**os.environ, "ANSIBLE_NOCOLOR": "1"},
    )
    return result.returncode, (result.stdout or "")[-16000:]


_execution_lock = threading.Lock()


def execute_job(job_id: str, plan: dict[str, Any]) -> None:
    with _execution_lock:
        _execute_job_serial(job_id, plan)


def _execute_job_serial(job_id: str, plan: dict[str, Any]) -> None:
    store = JobStore(settings.database_path)
    details = {"profiles": [], "current_profile": None}
    store.update(job_id, status="running", details=details, append_log="GAMGEE Control Tower execution started.\n")
    catalog = load_catalog()
    try:
        for profile_id in plan["ordered_profiles"]:
            profile = catalog[profile_id]
            details["current_profile"] = profile_id
            details["profiles"].append({"id": profile_id, "status": "running"})
            store.update(
                job_id,
                status="running",
                details=details,
                append_log=f"\n[{profile_id}] Starting {profile['name']}.\n",
            )
            for step in profile["steps"]:
                timeout = min(
                    int(step.get("timeout_seconds", settings.job_timeout_seconds)),
                    settings.job_timeout_seconds,
                )
                return_code, output = _run_playbook(step["playbook"], timeout)
                store.update(job_id, append_log=f"[{profile_id}] {output}\n")
                if return_code != 0:
                    raise RuntimeError(
                        f"{profile_id} failed in {step['playbook']} (exit code {return_code})."
                    )
            details["profiles"][-1]["status"] = "succeeded"
            store.update(job_id, details=details, append_log=f"[{profile_id}] Completed.\n")
        details["current_profile"] = None
        store.update(
            job_id,
            status="succeeded",
            details=details,
            append_log="\nAll selected profiles completed successfully.\n",
        )
    except subprocess.TimeoutExpired:
        store.update(
            job_id,
            status="failed",
            details=details,
            append_log="\nExecution timed out. Inspect host state before retrying.\n",
        )
    except Exception as exc:
        store.update(
            job_id,
            status="failed",
            details=details,
            append_log=f"\nExecution failed: {type(exc).__name__}: {exc}\n",
        )
