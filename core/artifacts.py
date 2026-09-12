"""Deterministic artifact-registry persistence and protection primitives."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .constants import ARTIFACT_REGISTRY_NAME, ARTIFACT_REGISTRY_SCHEMA_VERSION
from .errors import CliError
from .paths import require_within
from .schemas import validate_artifact_registry
from .workspace import atomic_write, read_text_file, write_managed_json


def empty_artifact_registry() -> dict[str, Any]:
    return {
        "schema_version": ARTIFACT_REGISTRY_SCHEMA_VERSION,
        "managed_by": "project-upgrade-maintainer",
        "entries": [],
    }


def artifact_registry_path(upgrade: Path) -> Path:
    return require_within(upgrade / ARTIFACT_REGISTRY_NAME, upgrade, "artifact policy registry")


def load_artifact_registry(upgrade: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    path = artifact_registry_path(upgrade)
    if path.is_symlink():
        raise CliError(f"{ARTIFACT_REGISTRY_NAME} must not be a symlink")
    if not path.exists():
        if allow_missing:
            return empty_artifact_registry()
        raise CliError(f"{ARTIFACT_REGISTRY_NAME} is missing; run sync to repair the workspace")
    if not path.is_file():
        raise CliError(f"{ARTIFACT_REGISTRY_NAME} is not a file")
    try:
        payload = json.loads(read_text_file(path).text)
    except json.JSONDecodeError as exc:
        raise CliError(f"{ARTIFACT_REGISTRY_NAME} is invalid JSON: {exc}") from exc
    return validate_artifact_registry(payload)


def artifact_registry_digest(registry: dict[str, Any]) -> str:
    validated = validate_artifact_registry(registry)
    canonical = json.dumps(validated, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def write_artifact_registry(
    upgrade: Path,
    registry: dict[str, Any],
    result: Any,
    *,
    dry_run: bool,
    writer: Callable[..., bool] = atomic_write,
) -> bool:
    return write_managed_json(
        artifact_registry_path(upgrade),
        validate_artifact_registry(registry),
        result,
        f".upgrade/{ARTIFACT_REGISTRY_NAME}",
        dry_run=dry_run,
        writer=writer,
    )


def registry_entry_map(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["path"]: entry for entry in validate_artifact_registry(registry)["entries"]}


def registered_artifact_paths(registry: dict[str, Any]) -> set[str]:
    return set(registry_entry_map(registry))


def registered_cleanup_protected_paths(registry: dict[str, Any]) -> set[str]:
    protected: set[str] = set()
    for entry in validate_artifact_registry(registry)["entries"]:
        if entry["required"] or entry["kind"] == "governance" or entry["lifecycle"] == "permanent":
            protected.add(entry["path"].removeprefix(".upgrade/"))
    return protected
