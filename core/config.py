"""Deterministic workspace configuration and module-resource primitives."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .artifacts import artifact_registry_digest, empty_artifact_registry
from .authority import authority_registry_digest, empty_authority_registry
from .audit import now_iso
from .constants import (
    MODULE_SPECS,
    POLICY_VERSION,
    SKILL_VERSION,
    WORKSPACE_CONFIG_NAME,
    WORKSPACE_CONFIG_SCHEMA_VERSION,
)
from .errors import CliError
from .paths import require_within
from .schemas import ordered_modules, validate_workspace_config
from .workspace import atomic_write, read_text_file, write_managed_json


def workspace_config_path(upgrade: Path) -> Path:
    return require_within(upgrade / WORKSPACE_CONFIG_NAME, upgrade, "workspace config")


def new_workspace_config(
    *,
    initial_profile: str,
    active_modules: list[str],
    selection_source: str,
    bootstrap_recommendation: str | None = None,
    clock: Callable[[], str] = now_iso,
) -> dict[str, Any]:
    timestamp = clock()
    return {
        "schema_version": WORKSPACE_CONFIG_SCHEMA_VERSION,
        "managed_by": "project-upgrade-maintainer",
        "initial_profile": initial_profile,
        "active_modules": ordered_modules(active_modules),
        "selection_source": selection_source,
        "bootstrap_recommendation": bootstrap_recommendation,
        "created_at": timestamp,
        "updated_at": timestamp,
        "last_synced_skill_version": SKILL_VERSION,
        "last_synced_policy_version": POLICY_VERSION,
        "artifact_registry_sha256": artifact_registry_digest(empty_artifact_registry()),
        "authority_registry_sha256": authority_registry_digest(empty_authority_registry()),
    }


def load_workspace_config(upgrade: Path, *, allow_missing: bool = False, allow_previous: bool = False) -> dict[str, Any] | None:
    path = workspace_config_path(upgrade)
    if path.is_symlink():
        raise CliError(f"{WORKSPACE_CONFIG_NAME} must not be a symlink")
    if not path.exists():
        if allow_missing:
            return None
        raise CliError(f"{WORKSPACE_CONFIG_NAME} is missing; run sync to register/repair the existing workspace")
    if not path.is_file():
        raise CliError(f"{WORKSPACE_CONFIG_NAME} is not a file")
    try:
        payload = json.loads(read_text_file(path).text)
    except json.JSONDecodeError as exc:
        raise CliError(f"{WORKSPACE_CONFIG_NAME} is invalid JSON: {exc}") from exc
    return validate_workspace_config(payload, allow_previous=allow_previous)


def write_workspace_config(
    upgrade: Path,
    config: dict[str, Any],
    result: Any,
    *,
    dry_run: bool,
    writer: Callable[..., bool] = atomic_write,
) -> bool:
    return write_managed_json(
        workspace_config_path(upgrade),
        validate_workspace_config(config),
        result,
        f".upgrade/{WORKSPACE_CONFIG_NAME}",
        dry_run=dry_run,
        writer=writer,
    )


def module_resources(modules: list[str] | set[str] | tuple[str, ...]) -> tuple[list[str], dict[str, str], list[str]]:
    dirs: list[str] = []
    templates: dict[str, str] = {}
    placeholders: list[str] = []
    for module in ordered_modules(modules):
        spec = MODULE_SPECS[module]
        dirs.extend(spec["dirs"])  # type: ignore[arg-type]
        templates.update(spec["templates"])  # type: ignore[arg-type]
        placeholders.extend(spec["placeholders"])  # type: ignore[arg-type]
    return list(dict.fromkeys(dirs)), templates, list(dict.fromkeys(placeholders))
