from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tower.config import PROFILE_DIR


class CatalogError(ValueError):
    pass


_REQUIRED = {"id", "name", "description", "depends_on", "resources", "required_tools", "steps"}


def load_catalog(directory: Path = PROFILE_DIR) -> dict[str, dict[str, Any]]:
    if not directory.is_dir():
        raise CatalogError(f"Profile directory does not exist: {directory}")
    catalog: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            item = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise CatalogError(f"Cannot read profile {path.name}: {exc}") from exc
        if not isinstance(item, dict) or not _REQUIRED.issubset(item):
            raise CatalogError(f"Profile {path.name} is missing required fields.")
        profile_id = item["id"]
        if not isinstance(profile_id, str) or profile_id != path.stem:
            raise CatalogError(f"Profile id in {path.name} must match its filename.")
        if profile_id in catalog:
            raise CatalogError(f"Duplicate profile id: {profile_id}")
        if not isinstance(item["depends_on"], list) or not all(
            isinstance(value, str) for value in item["depends_on"]
        ):
            raise CatalogError(f"{profile_id}: depends_on must be a list of profile ids.")
        if not isinstance(item["required_tools"], list):
            raise CatalogError(f"{profile_id}: required_tools must be a list.")
        if not isinstance(item["steps"], list) or not item["steps"]:
            raise CatalogError(f"{profile_id}: at least one step is required.")
        resources = item["resources"]
        if not isinstance(resources, dict):
            raise CatalogError(f"{profile_id}: resources must be a mapping.")
        for key in ("minimum_vcpu", "minimum_ram_gib", "minimum_disk_gib"):
            value = resources.get(key)
            if not isinstance(value, int) or value < 0:
                raise CatalogError(f"{profile_id}: resources.{key} must be a non-negative integer.")
        for step in item["steps"]:
            if not isinstance(step, dict) or step.get("executor") != "ansible-playbook":
                raise CatalogError(f"{profile_id}: only ansible-playbook steps are supported.")
            playbook = step.get("playbook")
            if not isinstance(playbook, str) or not playbook.startswith("automation/ansible/"):
                raise CatalogError(f"{profile_id}: playbooks must be under automation/ansible/.")
            if ".." in Path(playbook).parts:
                raise CatalogError(f"{profile_id}: path traversal is not allowed.")
        catalog[profile_id] = item

    for profile_id, item in catalog.items():
        for dependency in item["depends_on"]:
            if dependency not in catalog:
                raise CatalogError(f"{profile_id}: unknown dependency {dependency}.")
            if dependency == profile_id:
                raise CatalogError(f"{profile_id}: a profile cannot depend on itself.")
    return catalog


def resolve_profiles(selected: list[str], catalog: dict[str, dict[str, Any]]) -> list[str]:
    if not selected:
        raise CatalogError("Select at least one installation profile.")
    unknown = sorted(set(selected) - set(catalog))
    if unknown:
        raise CatalogError(f"Unknown profile(s): {', '.join(unknown)}")

    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(profile_id: str) -> None:
        if profile_id in visiting:
            raise CatalogError(f"Dependency cycle detected at {profile_id}.")
        if profile_id in visited:
            return
        visiting.add(profile_id)
        for dependency in catalog[profile_id]["depends_on"]:
            visit(dependency)
        visiting.remove(profile_id)
        visited.add(profile_id)
        ordered.append(profile_id)

    for profile_id in selected:
        visit(profile_id)
    return ordered
