from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


BASE_PROFILES = [
    "core-cloud",
    "function-cloud",
    "enterprise-security",
    "federated-observability",
]


def _configured_ai_env(path: Path) -> bool:
    if not path.is_file():
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


def recommend_local_bundle(*, kvm_available: bool, ai_available: bool) -> dict:
    profiles = list(BASE_PROFILES)
    included = [
        {"id": "core-cloud", "name": "Core Cloud", "reason": "Container platform, tenant boundary, ingress and base cost visibility."},
        {"id": "function-cloud", "name": "Function Cloud", "reason": "KEDA event-driven scaling foundation."},
        {"id": "enterprise-security", "name": "Security foundation", "reason": "Kyverno admission-policy foundation."},
        {"id": "federated-observability", "name": "Local observability", "reason": "VictoriaMetrics and Grafana starter stack on this cell."},
    ]
    skipped = []

    if kvm_available:
        profiles.append("private-iaas")
        included.append(
            {"id": "private-iaas", "name": "Private VM hosting", "reason": "KVM is available; Incus/QEMU foundation will be installed."}
        )
    else:
        skipped.append(
            {"id": "private-iaas", "name": "Private VM hosting", "reason": "KVM was not detected. Enable nested/hardware virtualisation and expose /dev/kvm to include VM hosting."}
        )

    if ai_available:
        profiles.append("ai-enterprise")
        included.append(
            {"id": "ai-enterprise", "name": "Private AI Factory", "reason": "Docker Compose and required non-placeholder secrets are ready."}
        )
    else:
        skipped.append(
            {"id": "ai-enterprise", "name": "Private AI Factory", "reason": "Not auto-included. It requires a running Docker Engine, Docker Compose v2 and configured secrets in the repository .env file."}
        )

    skipped.append(
        {"id": "hybrid-cloud", "name": "Hybrid Cloud", "reason": "Not included in a local one-click install. The current provider adapter is a validation scaffold and must not imply that remote cloud resources are provisioned."}
    )

    return {
        "bundle": "gamgee-local-cloud",
        "name": "GAMGEE Local Cloud",
        "profiles": profiles,
        "included": included,
        "skipped": skipped,
        "note": "This composes the local capabilities that the host can support. The plan still checks total CPU, memory, disk, tools and KVM before any installation starts.",
    }
