from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from tower.catalog import load_catalog, resolve_profiles
from tower.config import ROOT


def _available_memory_gib() -> float | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    for line in meminfo.read_text(encoding="ascii", errors="ignore").splitlines():
        if line.startswith("MemAvailable:"):
            return round(int(line.split()[1]) / 1024 / 1024, 1)
    return None


def build_plan(selected: list[str]) -> dict[str, Any]:
    catalog = load_catalog()
    ordered = resolve_profiles(selected, catalog)
    items = [catalog[name] for name in ordered]
    required = {
        "vcpu": sum(item["resources"]["minimum_vcpu"] for item in items),
        "ram_gib": sum(item["resources"]["minimum_ram_gib"] for item in items),
        "disk_gib": sum(item["resources"]["minimum_disk_gib"] for item in items),
    }
    tools = sorted({tool for item in items for tool in item["required_tools"]})
    missing_tools = [tool for tool in tools if shutil.which(tool) is None]
    warnings: list[str] = []
    blockers: list[str] = []

    if os.name != "posix":
        blockers.append("The installation runner requires a Linux/POSIX host.")
    elif not Path("/etc/os-release").exists():
        blockers.append("Cannot identify the Linux distribution.")
    else:
        release = Path("/etc/os-release").read_text(encoding="utf-8", errors="ignore").lower()
        if not any(name in release for name in ("id=ubuntu", "id=debian", "id_like=debian")):
            warnings.append("The bundled host playbooks currently target Debian/Ubuntu. Review before applying on another distribution.")

    if "private-iaas" in ordered and not Path("/dev/kvm").exists():
        blockers.append("Private IaaS requires /dev/kvm. Enable hardware or nested virtualisation before applying.")

    available_cpu = os.cpu_count() or 1
    available_ram = _available_memory_gib()
    available_disk = round(shutil.disk_usage(ROOT).free / (1024**3), 1)
    if available_cpu < required["vcpu"]:
        blockers.append(f"CPU: profiles request at least {required['vcpu']} vCPU; host reports {available_cpu}.")
    if available_ram is not None and available_ram < required["ram_gib"]:
        blockers.append(f"Memory: profiles request at least {required['ram_gib']} GiB; host has {available_ram} GiB available.")
    if available_disk < required["disk_gib"]:
        blockers.append(f"Disk: profiles request at least {required['disk_gib']} GiB; filesystem has {available_disk} GiB free.")
    if missing_tools:
        warnings.append("Missing tools: " + ", ".join(missing_tools) + ". Install prerequisites before applying.")

    source_checksums: dict[str, str] = {}
    selected_items = []
    for item in items:
        selected_items.append(item)
        source_paths = [step["playbook"] for step in item["steps"]]
        source_paths.extend(item.get("artifacts", []))
        for source_path in source_paths:
            candidate = (ROOT / source_path).resolve()
            if ROOT not in candidate.parents or not candidate.is_file():
                blockers.append(f"Bundle source is missing or outside the repository: {source_path}")
                continue
            source_checksums[source_path] = hashlib.sha256(candidate.read_bytes()).hexdigest()

    fingerprint_input = json.dumps(
        {
            "profiles": ordered,
            "items": selected_items,
            "source_checksums": source_checksums,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    plan_hash = hashlib.sha256(fingerprint_input).hexdigest()
    return {
        "plan_hash": plan_hash,
        "selected_profiles": selected,
        "ordered_profiles": ordered,
        "profiles": selected_items,
        "required_resources": required,
        "host_resources": {
            "vcpu": available_cpu,
            "available_ram_gib": available_ram,
            "free_disk_gib": available_disk,
        },
        "required_tools": tools,
        "missing_tools": missing_tools,
        "source_checksums": source_checksums,
        "blockers": blockers,
        "warnings": warnings,
        "apply_ready": not blockers and not missing_tools,
        "execution_model": "Repository-owned Ansible playbooks; no user-supplied shell commands.",
    }
