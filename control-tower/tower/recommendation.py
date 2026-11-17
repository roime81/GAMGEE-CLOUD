from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tower.catalog import load_catalog, resolve_profiles


def detect_host_capacity(repository_root: Path) -> dict[str, float | int | None]:
    """Report local capacity using the same conservative signals as plan preflight."""
    available_ram: float | None = None
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        try:
            for line in meminfo.read_text(encoding="ascii", errors="ignore").splitlines():
                if line.startswith("MemAvailable:"):
                    available_ram = round(int(line.split()[1]) / 1024 / 1024, 1)
                    break
        except (OSError, ValueError, IndexError):
            available_ram = None

    try:
        free_disk = round(shutil.disk_usage(repository_root).free / (1024**3), 1)
    except OSError:
        free_disk = 0.0

    return {
        "vcpu": os.cpu_count() or 1,
        "available_ram_gib": available_ram,
        "free_disk_gib": free_disk,
    }


def _configured_ai_env(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        # Do not auto-start a secrets-bearing stack from a group/world-readable env file.
        if path.stat().st_mode & 0o077:
            return False
    except OSError:
        return False

    values: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return False

    required = (
        "AUTHENTIK_POSTGRES_PASSWORD",
        "AUTHENTIK_SECRET_KEY",
        "GAMGEE_API_TOKEN",
        "FACTORY_POSTGRES_PASSWORD",
    )
    return all(
        values.get(key)
        and len(values[key]) >= 24
        and not values[key].lower().startswith(("replace-", "changeme", "change-me"))
        for key in required
    )


def ai_factory_ready(repository_root: Path) -> bool:
    """Only auto-include AI when Docker Compose, daemon and non-placeholder secrets exist."""
    docker = shutil.which("docker")
    if not docker or not _configured_ai_env(repository_root / ".env"):
        return False
    try:
        compose = subprocess.run(
            [docker, "compose", "version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
            check=False,
        )
        daemon = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return compose.returncode == 0 and daemon.returncode == 0 and bool(daemon.stdout.strip())


def _resource_total(profile_ids: list[str], catalog: dict[str, dict[str, Any]]) -> dict[str, int]:
    ordered = resolve_profiles(profile_ids, catalog)
    return {
        "vcpu": sum(catalog[name]["resources"]["minimum_vcpu"] for name in ordered),
        "ram_gib": sum(catalog[name]["resources"]["minimum_ram_gib"] for name in ordered),
        "disk_gib": sum(catalog[name]["resources"]["minimum_disk_gib"] for name in ordered),
    }


def _capacity_gaps(required: dict[str, int], capacity: dict[str, float | int | None]) -> list[str]:
    gaps = []
    if int(capacity.get("vcpu") or 0) < required["vcpu"]:
        gaps.append(f"CPU needs {required['vcpu']} vCPU; host reports {capacity.get('vcpu')}.")
    available_ram = capacity.get("available_ram_gib")
    if available_ram is None:
        gaps.append("Available memory could not be measured safely.")
    elif float(available_ram) < required["ram_gib"]:
        gaps.append(f"Memory needs {required['ram_gib']} GiB available; host reports {available_ram} GiB.")
    if float(capacity.get("free_disk_gib") or 0) < required["disk_gib"]:
        gaps.append(f"Disk needs {required['disk_gib']} GiB free; host reports {capacity.get('free_disk_gib')} GiB.")
    return gaps


def recommend_local_bundle(
    *,
    kvm_available: bool,
    ai_available: bool,
    host_capacity: dict[str, float | int | None],
) -> dict:
    """Select the richest local bundle that fits this host, without blocking Core Cloud."""
    catalog = load_catalog()
    selected = ["core-cloud"]
    included = [
        {
            "id": "core-cloud",
            "name": "Core Cloud",
            "reason": "The smallest required GAMGEE cell: container runtime, tenancy, ingress and base cost visibility.",
        }
    ]
    skipped = []

    candidates = [
        (
            "enterprise-security",
            "Security foundation",
            "Kyverno admission-policy foundation.",
            True,
        ),
        (
            "function-cloud",
            "Function Cloud foundation",
            "KEDA event-driven scaling foundation.",
            True,
        ),
    ]

    if kvm_available:
        candidates.append(
            (
                "private-iaas",
                "Private IaaS foundation",
                "KVM is available; Incus/QEMU packages and host initialization will be installed. GAMGEE VM lifecycle APIs are a later milestone.",
                True,
            )
        )
    else:
        skipped.append(
            {
                "id": "private-iaas",
                "name": "Private IaaS foundation",
                "reason": "KVM was not detected. Enable nested/hardware virtualisation and expose /dev/kvm to install the Incus/QEMU foundation.",
            }
        )

    candidates.append(
        (
            "federated-observability",
            "Local observability",
            "VictoriaMetrics and Grafana starter stack on this cell.",
            True,
        )
    )

    if ai_available:
        candidates.append(
            (
                "ai-enterprise",
                "Private AI Factory",
                "Docker Compose and required owner-only, non-placeholder secrets are ready.",
                True,
            )
        )
    else:
        skipped.append(
            {
                "id": "ai-enterprise",
                "name": "Private AI Factory",
                "reason": "Not auto-included. It requires a running Docker Engine, Docker Compose v2 and configured mode-0600 secrets in the repository .env file.",
            }
        )

    for profile_id, name, reason, _enabled in candidates:
        trial = selected + [profile_id]
        required = _resource_total(trial, catalog)
        gaps = _capacity_gaps(required, host_capacity)
        if gaps:
            skipped.append(
                {
                    "id": profile_id,
                    "name": name,
                    "reason": "Not added to this host's one-click bundle to preserve headroom: " + " ".join(gaps),
                    "required_resources": required,
                }
            )
            continue
        selected.append(profile_id)
        included.append({"id": profile_id, "name": name, "reason": reason})

    skipped.append(
        {
            "id": "hybrid-cloud",
            "name": "Hybrid Cloud",
            "reason": "Not included in a local one-click install. The current provider adapter is a validation scaffold and does not provision remote cloud resources.",
        }
    )

    return {
        "bundle": "gamgee-local-cloud",
        "name": "GAMGEE Local Cloud",
        "profiles": selected,
        "included": included,
        "skipped": skipped,
        "host_capacity": host_capacity,
        "required_resources": _resource_total(selected, catalog),
        "note": "Core Cloud remains the stable minimum. Optional capabilities are added only when measured CPU, available memory and free disk can support them. The full plan performs a second preflight before any host changes.",
    }
