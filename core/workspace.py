"""Deterministic text I/O and managed-text helpers."""

from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import stat as statlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .constants import (
    ALL_MANAGED_DIRS,
    ALL_PLACEHOLDERS,
    ALL_TEMPLATE_TARGETS,
    ARTIFACT_REGISTRY_NAME,
    MANIFEST_END,
    MANIFEST_START,
    POLICY_VERSION,
    README_ARTIFACT_END,
    README_ARTIFACT_START,
    SKILL_VERSION,
    TEMPLATES,
    WORKSPACE_CONFIG_NAME,
)
from .errors import CliError
from .durability import atomic_write_bytes, durable_mkdir
from .paths import require_within, verified_upgrade


@dataclass
class TextFile:
    text: str
    bom: bool
    newline: str


def read_text_file(path: Path) -> TextFile:
    raw = path.read_bytes()
    bom = raw.startswith(codecs.BOM_UTF8)
    if bom:
        raw = raw[len(codecs.BOM_UTF8):]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CliError(f"not a UTF-8 text file: {path}: {exc}") from exc
    newline = "\r\n" if b"\r\n" in raw else "\n"
    return TextFile(text=text.replace("\r\n", "\n"), bom=bom, newline=newline)


def encoded_text(text: str, bom: bool, newline: str) -> bytes:
    payload = text.replace("\r\n", "\n").replace("\n", newline).encode("utf-8")
    return (codecs.BOM_UTF8 + payload) if bom else payload


def atomic_write(path: Path, text: str, *, source: TextFile | None = None, dry_run: bool = False) -> bool:
    source = source or (read_text_file(path) if path.exists() else TextFile("", False, os.linesep))
    data = encoded_text(text, source.bom, source.newline)
    if path.exists() and path.read_bytes() == data:
        return False
    if dry_run:
        return True
    transaction = None
    operation_index = None
    try:
        from .transactions import current_transaction
        transaction = current_transaction()
    except ImportError:
        transaction = None
    if transaction is not None and transaction.should_track(path):
        operation_index = transaction.prepare_write(path, data)
    existing_mode: int | None = None
    if path.exists() and not path.is_symlink():
        existing_mode = statlib.S_IMODE(path.stat().st_mode)
    atomic_write_bytes(path, data, mode=existing_mode)
    if transaction is not None and operation_index is not None:
        transaction.mark_applied(operation_index)
    return True


def update_marker(text: str, start: str, end: str, block: str) -> str:
    start_count, end_count = text.count(start), text.count(end)
    if start_count != end_count or start_count > 1:
        raise CliError(f"managed marker conflict: expected zero or one paired block for {start}")
    if start_count == 0:
        return text.rstrip() + ("\n\n" if text.strip() else "") + block.rstrip() + "\n"
    begin = text.index(start)
    finish = text.index(end, begin) + len(end)
    return text[:begin] + block.rstrip() + text[finish:]


def validate_marker_pair(text: str, start: str, end: str) -> None:
    start_count, end_count = text.count(start), text.count(end)
    if start_count != end_count or start_count > 1:
        raise CliError(f"managed marker conflict: expected zero or one paired block for {start}")


def section_value(text: str, heading: str) -> str | None:
    pattern = re.compile(rf"(?ms)^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)")
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def replace_section(text: str, heading: str, value: str) -> str:
    replacement = f"## {heading}\n\n{value.strip()}\n\n"
    pattern = re.compile(rf"(?ms)^## {re.escape(heading)}\s*\n.*?(?=^## |\Z)")
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1).rstrip() + "\n"
    return text.rstrip() + "\n\n" + replacement


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_managed_json(
    path: Path,
    payload: dict[str, Any],
    result: Any,
    relative_path: str,
    *,
    dry_run: bool,
    writer: Callable[..., bool] = atomic_write,
) -> bool:
    """Persist canonical managed JSON and record the externally visible change."""
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    source = read_text_file(path) if path.exists() else None
    changed = writer(path, text, source=source, dry_run=dry_run)
    if changed:
        action = "would_update" if dry_run and source else "would_create" if dry_run else "updated" if source else "created"
        result.change(relative_path, action)
    return changed


def ensure_workspace_structure(
    upgrade: Path,
    result: Any,
    dry_run: bool,
    modules: list[str] | set[str] | tuple[str, ...],
    *,
    writer: Callable[..., bool] = atomic_write,
) -> None:
    """Materialize only the deterministic directories/templates for active modules."""
    from .config import module_resources

    dirs, templates, placeholders = module_resources(modules)
    for relative in dirs:
        target = upgrade / relative
        require_within(target, upgrade)
        if target.is_symlink():
            raise CliError(f"managed workspace directory must not be a symlink: .upgrade/{relative}")
        if target.exists() and not target.is_dir():
            raise CliError(f"managed workspace directory target is not a directory: .upgrade/{relative}")
        if not target.exists():
            if not dry_run:
                durable_mkdir(target)
            result.change(f".upgrade/{relative}", "would_create" if dry_run else "created")
    for template_name, target_name in templates.items():
        target = upgrade / target_name
        require_within(target, upgrade, "template target")
        if target.is_symlink():
            raise CliError(f"managed template target must not be a symlink: .upgrade/{target_name}")
        if target.exists() and not target.is_file():
            raise CliError(f"managed template target is not a file: .upgrade/{target_name}")
        if not target.exists():
            template = read_text_file(TEMPLATES / template_name).text
            writer(target, template, dry_run=dry_run)
            result.change(f".upgrade/{target_name}", "would_create" if dry_run else "created")
    for relative in placeholders:
        target = upgrade / relative
        require_within(target, upgrade, "placeholder target")
        if target.is_symlink():
            raise CliError(f"managed placeholder must not be a symlink: .upgrade/{relative}")
        if not target.exists():
            writer(target, "", dry_run=dry_run)
            result.change(f".upgrade/{relative}", "would_create" if dry_run else "created")


def activate_workspace_modules(
    root: Path,
    result: Any,
    required_modules: set[str] | list[str] | tuple[str, ...],
    *,
    dry_run: bool,
    clock: Callable[[], str] | None = None,
    writer: Callable[..., bool] = atomic_write,
) -> dict[str, Any]:
    """Activate explicit module requirements without inferring user intent."""
    from .audit import now_iso
    from .config import load_workspace_config, write_workspace_config
    from .schemas import ordered_modules

    effective_clock = clock or now_iso
    upgrade = verified_upgrade(root)
    config = load_workspace_config(upgrade, allow_missing=True)
    if config is None:
        raise CliError(
            f"{WORKSPACE_CONFIG_NAME} is missing; run sync first so an existing fixed-footprint workspace can be registered "
            "or a managed workspace can be repaired before further mutation"
        )
    active = set(config["active_modules"])
    requested = set(ordered_modules(required_modules))
    missing = requested - active
    if not missing:
        ensure_workspace_structure(upgrade, result, dry_run, requested, writer=writer)
        return config
    ensure_workspace_structure(upgrade, result, dry_run, missing, writer=writer)
    config = dict(config)
    config["active_modules"] = ordered_modules(active | missing)
    config["updated_at"] = effective_clock()
    config["last_synced_skill_version"] = SKILL_VERSION
    config["last_synced_policy_version"] = POLICY_VERSION
    write_workspace_config(upgrade, config, result, dry_run=dry_run, writer=writer)
    return config


def preflight_mutation_workspace(root: Path, *, allow_missing: bool = False, require_config: bool = False) -> Path:
    """Validate managed workspace boundaries/provenance before any mutation."""
    from .artifacts import artifact_registry_digest, load_artifact_registry
    from .config import load_workspace_config

    upgrade = verified_upgrade(root, must_exist=not allow_missing)
    if upgrade.exists():
        managed_paths = list(ALL_MANAGED_DIRS)
        managed_paths.extend(ALL_TEMPLATE_TARGETS.values())
        managed_paths.extend(ALL_PLACEHOLDERS)
        managed_paths.extend((
            "AGENTS.md",
            WORKSPACE_CONFIG_NAME,
            ARTIFACT_REGISTRY_NAME,
            "README.md",
            "MANIFEST.md",
            "traces/last-maintenance.md",
        ))
        for relative in dict.fromkeys(managed_paths):
            target = upgrade / relative
            if target.exists() or target.is_symlink():
                require_within(target, upgrade, f"managed target {relative}")
        manifest = upgrade / "MANIFEST.md"
        if manifest.is_file():
            validate_marker_pair(read_text_file(manifest).text, MANIFEST_START, MANIFEST_END)
        readme = upgrade / "README.md"
        if readme.is_file():
            validate_marker_pair(read_text_file(readme).text, README_ARTIFACT_START, README_ARTIFACT_END)
        config = load_workspace_config(upgrade, allow_missing=True, allow_previous=not require_config)
        if require_config and config is None:
            raise CliError(f"{WORKSPACE_CONFIG_NAME} is missing; run sync before applying further workspace mutations")
        if require_config and config is not None:
            if config.get("artifact_registry_sha256") is None:
                raise CliError(f"workspace predates managed artifact-policy provenance; run sync before further mutations")
            registry = load_artifact_registry(upgrade)
            if artifact_registry_digest(registry) != config.get("artifact_registry_sha256"):
                raise CliError(
                    f"{ARTIFACT_REGISTRY_NAME} changed outside the approved artifact-policy lifecycle; "
                    "restore/reconcile it before further mutations"
                )
            from .authority import authority_registry_digest, load_authority_registry
            authority = load_authority_registry(upgrade)
            if authority_registry_digest(authority) != config.get("authority_registry_sha256"):
                raise CliError(
                    f"{AUTHORITY_REGISTRY_NAME} changed outside the approved authority lifecycle; "
                    "restore/reconcile it before further mutations"
                )
    return upgrade

