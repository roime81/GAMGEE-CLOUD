from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml


class ClusterError(RuntimeError):
    """A safe, user-displayable Kubernetes operation failure."""


class ClusterNotFound(ClusterError):
    """A Kubernetes resource does not exist."""


def run_kubectl(args: list[str], *, input_text: str | None = None, timeout: int = 45) -> str:
    """Run a fixed kubectl argv; never invoke a shell or accept a command string."""
    executable = shutil.which("kubectl")
    if not executable:
        raise ClusterError("kubectl is not installed. Install the GAMGEE Core Cloud first.")

    if not args or any(not isinstance(value, str) or not value for value in args):
        raise ValueError("kubectl arguments must be non-empty strings.")

    env = os.environ.copy()
    default_kubeconfig = Path("/etc/rancher/k3s/k3s.yaml")
    if not env.get("KUBECONFIG") and default_kubeconfig.is_file():
        env["KUBECONFIG"] = str(default_kubeconfig)

    try:
        result = subprocess.run(
            [executable, *args],
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClusterError(f"Kubernetes operation timed out after {timeout} seconds.") from exc
    except OSError as exc:
        raise ClusterError(f"Unable to execute kubectl: {type(exc).__name__}.") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Unknown Kubernetes error").strip()
        if "NotFound" in detail or "not found" in detail.lower():
            raise ClusterNotFound(detail[:700])
        raise ClusterError(detail[:1000])
    return result.stdout or ""


def get_json(
    resource: str,
    name: str | None = None,
    *,
    namespace: str | None = None,
    label_selector: str | None = None,
) -> dict[str, Any]:
    args = ["get", resource]
    if name:
        args.append(name)
    if namespace:
        args.extend(["--namespace", namespace])
    if label_selector:
        args.extend(["--selector", label_selector])
    args.extend(["--output", "json"])
    try:
        return json.loads(run_kubectl(args))
    except json.JSONDecodeError as exc:
        raise ClusterError("Kubernetes returned an invalid JSON response.") from exc


def apply_objects(objects: list[dict[str, Any]]) -> str:
    if not objects:
        raise ValueError("At least one Kubernetes object is required.")
    manifest = yaml.safe_dump_all(objects, sort_keys=False)
    return run_kubectl(["apply", "--filename", "-"], input_text=manifest)


def apply_object(obj: dict[str, Any]) -> str:
    return apply_objects([obj])


def delete_gamgee_app(name: str, namespace: str) -> str:
    # Resource kinds and flags are fixed. Names and namespace have been DNS-validated.
    return run_kubectl(
        [
            "delete",
            f"deployment/{name}",
            f"service/{name}",
            f"ingress/{name}",
            "--namespace",
            namespace,
            "--ignore-not-found=true",
            "--wait=false",
        ]
    )
