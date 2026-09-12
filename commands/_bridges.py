"""Managed governance text and Agent bridge planning/synchronization."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any
from core.constants import *
from core.artifacts import empty_artifact_registry
from core.audit import Result
from core.errors import CliError
from core.paths import is_within, normalized, relative_posix, require_within
from core.workspace import TextFile, read_text_file, update_marker, validate_marker_pair
from core.schemas import validate_artifact_registry
from ._runtime import atomic_write
from ._artifacts import artifact_policy_readme_block, governance_contract_text, render_dockerignore_block, render_gitignore_block

def plan_governance_contract_update(upgrade: Path, registry: dict[str, Any] | None = None) -> tuple[Path, TextFile, str]:
    target = upgrade / "AGENTS.md"
    require_within(target, upgrade, "governance contract")
    if target.is_symlink():
        raise CliError("managed governance contract must not be a symlink: .upgrade/AGENTS.md")
    if target.exists() and not target.is_file():
        raise CliError("managed governance contract target is not a file: .upgrade/AGENTS.md")
    source = read_text_file(target) if target.exists() else TextFile("", False, os.linesep)
    if target.exists() and not source.text.startswith(GOVERNANCE_CONTRACT_MARKER + "\n"):
        raise CliError(
            "unmanaged .upgrade/AGENTS.md already exists; refusing to overwrite it. "
            "Move or reconcile that file before running initialize/sync"
        )
    return target, source, governance_contract_text(registry)


def plan_workspace_readme_update(upgrade: Path, registry: dict[str, Any] | None = None) -> tuple[Path, TextFile, str]:
    target = require_within(upgrade / "README.md", upgrade, "workspace README")
    if target.is_symlink():
        raise CliError("managed workspace README must not be a symlink: .upgrade/README.md")
    if target.exists() and not target.is_file():
        raise CliError("managed workspace README target is not a file: .upgrade/README.md")
    source = read_text_file(target) if target.exists() else TextFile(read_text_file(TEMPLATES / "README.md").text, False, os.linesep)
    validate_marker_pair(source.text, README_ARTIFACT_START, README_ARTIFACT_END)
    registry = validate_artifact_registry(registry or empty_artifact_registry())
    updated = update_marker(source.text, README_ARTIFACT_START, README_ARTIFACT_END, artifact_policy_readme_block(registry))
    return target, source, updated


def root_agent_instruction_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for name in AGENT_INSTRUCTION_NAMES:
        path = root / name
        if path.is_symlink() or path.is_file():
            found.append(path)
    return found


def agent_bridge_status(path: Path) -> tuple[str, str | None]:
    if path.is_symlink():
        return "unsafe", "Agent instruction bridge target must not be a symlink"
    if not path.is_file():
        return "missing", None
    try:
        source = read_text_file(path)
        validate_marker_pair(source.text, GUARD_START, GUARD_END)
    except CliError as exc:
        return "conflict", str(exc)
    if GUARD_START not in source.text:
        return "missing", None
    expected = update_marker(source.text, GUARD_START, GUARD_END, GUARD_BLOCK)
    if expected != source.text:
        return "drifted", None
    return "current", None


def nested_agent_instruction_files(root: Path) -> list[Path]:
    names = {name.casefold() for name in AGENT_INSTRUCTION_NAMES}
    excluded = {item.casefold() for item in EXCLUDED_DIRS}
    found: list[Path] = []
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            name for name in dirs
            if name.casefold() not in excluded and not (current_path / name).is_symlink()
        ]
        if current_path == root:
            continue
        for name in files:
            if name.casefold() not in names:
                continue
            path = current_path / name
            if path.is_symlink() or path.is_file():
                found.append(path)
    return sorted(found, key=lambda path: path.relative_to(root).as_posix().casefold())


def plan_guardrail_updates(
    root: Path,
    *,
    create_root_bridge: bool = False,
    sync_nested_bridges: bool = False,
    registry: dict[str, Any] | None = None,
) -> list[tuple[Path, TextFile, str]]:
    targets: list[tuple[Path, str, str, str]] = []
    existing_root_instructions: list[Path] = []
    for name in AGENT_INSTRUCTION_NAMES:
        path = root / name
        if path.is_symlink():
            existing_root_instructions.append(path)
            targets.append((path, GUARD_START, GUARD_END, GUARD_BLOCK))
        elif path.is_file():
            existing_root_instructions.append(path)
            targets.append((path, GUARD_START, GUARD_END, GUARD_BLOCK))
    if create_root_bridge and not existing_root_instructions:
        targets.append((root / "AGENTS.md", GUARD_START, GUARD_END, GUARD_BLOCK))
    if sync_nested_bridges:
        for path in nested_agent_instruction_files(root):
            targets.append((path, GUARD_START, GUARD_END, GUARD_BLOCK))
    registry = validate_artifact_registry(registry or empty_artifact_registry())
    targets.append((root / ".gitignore", IGNORE_START, IGNORE_END, render_gitignore_block(registry)))
    docker_evidence = (root / ".dockerignore").exists() or any(
        (root / name).exists() for name in ("Dockerfile", "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
    )
    if docker_evidence:
        targets.append((root / ".dockerignore", IGNORE_START, IGNORE_END, render_dockerignore_block(registry)))
    planned: list[tuple[Path, TextFile, str]] = []
    seen: set[str] = set()
    for path, start, end, block in targets:
        require_within(path, root)
        key = normalized(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_symlink():
            raise CliError(f"managed file must not be a symlink: {path}")
        if path.exists() and not path.is_file():
            raise CliError(f"managed file target is not a file: {path}")
        source = read_text_file(path) if path.exists() else TextFile("", False, os.linesep)
        updated = update_marker(source.text, start, end, block)
        planned.append((path, source, updated))
    return planned


def sync_guardrails(root: Path, result: Result, dry_run: bool, planned: list[tuple[Path, TextFile, str]] | None = None) -> None:
    for path, source, updated in planned if planned is not None else plan_guardrail_updates(root, registry=empty_artifact_registry()):
        if atomic_write(path, updated, source=source, dry_run=dry_run):
            result.change(relative_posix(path, root), "would_update" if dry_run else "updated")


def sync_planned_texts(
    root: Path,
    result: Result,
    dry_run: bool,
    *planned_updates: tuple[Path, TextFile, str],
) -> None:
    for path, source, updated in planned_updates:
        if atomic_write(path, updated, source=source, dry_run=dry_run):
            result.change(relative_posix(path, root), "would_update" if dry_run else "updated")
