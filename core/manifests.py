"""Deterministic workspace manifest rebuilding primitives."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

from .artifacts import load_artifact_registry
from .config import load_workspace_config, module_resources
from .constants import (
    ARTIFACT_REGISTRY_NAME,
    MANIFEST_END,
    MANIFEST_START,
    PROFILE_MODULES,
    WORKSPACE_CONFIG_NAME,
)
from .paths import is_within, relative_posix, require_within, verified_upgrade
from .workspace import TextFile, atomic_write, read_text_file, section_value, update_marker


def extract_collected_rows(text: str) -> list[str]:
    section = section_value(text, "Collected Files") or ""
    rows = []
    for line in section.splitlines():
        if line.startswith("|") and not re.match(r"^\|\s*(?:---|Original Path)", line):
            rows.append(line)
    return rows


def _scan_files(base: Path) -> list[Path]:
    """Walk safe regular files without depending on command-layer traversal."""
    if base.is_symlink() or not base.is_dir():
        return []
    output: list[Path] = []
    for current, dirnames, filenames in os.walk(base, followlinks=False):
        current_path = Path(current)
        dirnames[:] = [
            name
            for name in dirnames
            if not (current_path / name).is_symlink() and is_within(current_path / name, base)
        ]
        for name in filenames:
            path = current_path / name
            if path.is_symlink() or not is_within(path, base):
                continue
            output.append(path)
    return sorted(output, key=lambda item: relative_posix(item, base).casefold())


def build_manifest(
    root: Path,
    old_text: str,
    action: str,
    changes: list[dict[str, Any]],
    collected: list[dict[str, Any]],
    *,
    clock: Callable[[], str] | None = None,
) -> str:
    if clock is None:
        from .audit import now_iso

        clock = now_iso

    upgrade = root / ".upgrade"
    rows = extract_collected_rows(old_text)
    timestamp = clock()
    for entry in collected:
        values = [
            entry["source"],
            entry.get("destination") or "",
            entry["action"],
            entry["classification"],
            entry["reason"],
            str(entry.get("references_checked", False)).lower(),
            str(entry.get("confirmation_required", False)).lower(),
            timestamp,
        ]
        rows.append("| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |")

    stage_paths: list[str] = []
    stages = upgrade / "stages"
    if stages.is_dir():
        require_within(stages, upgrade, "stages directory")
        stage_paths = [f"- `.upgrade/stages/{path.name}`" for path in _scan_files(stages)]

    last = section_value(old_text, "Last Maintenance Report") or "- No completed maintenance."
    # The last managed section extends to EOF, so exclude the closing marker from
    # the reusable section value before rebuilding the block.
    last = last.split(MANIFEST_END, 1)[0].rstrip()
    if changes:
        last = f"- Action type: {action}\n- Timestamp: {timestamp}\n- Changed: {len(changes)}\n- Skipped: 0"

    config = load_workspace_config(upgrade, allow_missing=True)
    active_modules = list(config["active_modules"]) if config else list(PROFILE_MODULES["governed"])
    _, active_templates, _ = module_resources(active_modules)
    registry = load_artifact_registry(upgrade, allow_missing=True)
    permanent_paths = ["AGENTS.md", WORKSPACE_CONFIG_NAME, ARTIFACT_REGISTRY_NAME, "README.md", "STATE.md", "MANIFEST.md"]
    permanent_paths.extend(active_templates.values())
    permanent_paths.extend(
        entry["path"].removeprefix(".upgrade/")
        for entry in registry["entries"]
        if entry["lifecycle"] == "permanent"
    )
    if "validation" in active_modules:
        permanent_paths.append("validation/")
    if "review" in active_modules:
        permanent_paths.extend(("decisions/", "risks/", "exceptions/", "delivery/", "reviews/"))
    permanent_lines = "\n".join(f"- `.upgrade/{relative}`" for relative in dict.fromkeys(permanent_paths))
    archive_line = "- `.upgrade/archive/`" if "lifecycle" in active_modules else "- Not enabled (lifecycle module inactive)."
    trash_line = "- `.upgrade/tmp/.trash/`" if "lifecycle" in active_modules else "- Not enabled (lifecycle module inactive)."
    artifact_policy_lines = [
        f"- `{entry['path']}` — kind={entry['kind']}; module={entry['module']}; lifecycle={entry['lifecycle']}; git={entry['git_policy']}; required={str(entry['required']).lower()}"
        for entry in registry["entries"]
    ]
    block = f"""{MANIFEST_START}
## Active Modules

{', '.join(active_modules)}

## Permanent

{permanent_lines}

## Registered Artifact Policies

{chr(10).join(artifact_policy_lines) if artifact_policy_lines else '- None.'}

## Stage Retained

{chr(10).join(stage_paths) if stage_paths else '- None.'}

## Archive

{archive_line}

## Recoverable Trash

{trash_line}

## Collected Files

| Original Path | New Path | Action | Classification | Reason | References Checked | User Confirmation Required | Timestamp |
| --- | --- | --- | --- | --- | --- | --- | --- |
{chr(10).join(rows)}

## Last Maintenance Report

{last}
{MANIFEST_END}"""
    if MANIFEST_START in old_text or MANIFEST_END in old_text:
        return update_marker(old_text, MANIFEST_START, MANIFEST_END, block)
    return old_text.rstrip() + "\n\n" + block + "\n"


def refresh_manifest(
    root: Path,
    result: Any,
    collected: list[dict[str, Any]] | None = None,
    *,
    clock: Callable[[], str] | None = None,
    writer: Callable[..., bool] = atomic_write,
) -> bool:
    upgrade = verified_upgrade(root)
    path = require_within(upgrade / "MANIFEST.md", upgrade, "MANIFEST.md")
    source = read_text_file(path) if path.exists() else TextFile("# Upgrade Manifest\n", False, os.linesep)
    updated = build_manifest(root, source.text, result.command, result.changed, collected or [], clock=clock)
    return writer(path, updated, source=source)
