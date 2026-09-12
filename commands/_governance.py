"""Shared phase/lifecycle governance state helpers."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from core.constants import *
from core.artifacts import registered_cleanup_protected_paths
from core.errors import CliError
from core.paths import is_within, require_within
from core.workspace import read_text_file, section_value
from ._common import scan_files
from ._runtime import atomic_write, now_iso

def current_phase(upgrade: Path) -> int:
    state = upgrade / "STATE.md"
    if not state.exists():
        return 0
    require_within(state, upgrade, "STATE.md")
    value = section_value(read_text_file(state).text, "current_phase")
    if value is None:
        raise CliError("STATE.md is missing the current_phase section")
    first_line = next((line.strip() for line in value.splitlines() if line.strip()), "")
    match = re.fullmatch(r"(?:Phase\s*)?(\d+)(?::.*)?", first_line, re.IGNORECASE)
    if not match:
        raise CliError(f"STATE.md current_phase is invalid: {first_line or '<empty>'}")
    return int(match.group(1))


def phase_transition_checkpoint(
    upgrade: Path,
    from_phase: int,
    to_phase: int,
    kind: str,
    reason: str,
    *,
    dry_run: bool,
) -> tuple[Path, str]:
    timestamp = now_iso()
    payload = {
        "from_phase": from_phase,
        "to_phase": to_phase,
        "kind": kind,
        "reason": reason,
        "timestamp": timestamp,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    safe_stamp = timestamp.replace("+00:00", "Z").replace("-", "").replace(":", "")
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:8]
    target = require_within(
        upgrade / "checkpoints" / f"phase-{to_phase}.transition-{safe_stamp}-{digest}.json",
        upgrade,
        "phase transition checkpoint",
    )
    counter = 0
    while target.exists():
        counter += 1
        target = require_within(
            upgrade / "checkpoints" / f"phase-{to_phase}.transition-{safe_stamp}-{digest}-{counter}.json",
            upgrade,
            "phase transition checkpoint",
        )
    atomic_write(target, encoded, dry_run=dry_run)
    return target, timestamp


def protected_cleanup_path(relative_upgrade: str, phase: int, registry: dict[str, Any] | None = None) -> bool:
    if relative_upgrade in CORE_UPGRADE_FILES:
        return True
    if registry is not None and relative_upgrade in registered_cleanup_protected_paths(registry):
        return True
    if re.fullmatch(r"(?:decisions/DEC-[0-9A-F]{12}\.json|risks/RISK-[0-9A-F]{12}\.jsonl|exceptions/EXC-[0-9A-F]{12}\.json|delivery/DLV-[0-9A-F]{12}\.json|transactions/TXN-[0-9A-F]{12}\.json|transactions/TXN-[0-9A-F]{12}/.*)", relative_upgrade, re.IGNORECASE):
        return True
    stage_match = re.fullmatch(r"stages/phase-(\d+)\.md", relative_upgrade, re.IGNORECASE)
    legacy_match = re.fullmatch(r"reports/stage(\d+)_report\.md", relative_upgrade, re.IGNORECASE)
    report_match = re.fullmatch(r"reports/phase-(\d+)-report\.md", relative_upgrade, re.IGNORECASE)
    checkpoint_match = re.match(r"checkpoints/phase-(\d+)(?:\.|$)", relative_upgrade, re.IGNORECASE)
    record = stage_match or legacy_match or report_match
    if record and int(record.group(1)) >= max(0, phase - 1):
        return True
    if checkpoint_match and int(checkpoint_match.group(1)) >= max(0, phase - 2):
        return True
    return False


def cleanup_thresholds(upgrade: Path, phase: int) -> dict[str, Any]:
    def metrics(directory: str, *, exclude_trash: bool = False) -> tuple[int, int]:
        path = upgrade / directory
        if path.exists():
            require_within(path, upgrade, f"{directory} directory")
        files = scan_files(path) if path.is_dir() else []
        if exclude_trash:
            trash = upgrade / "tmp" / ".trash"
            files = [item for item in files if not is_within(item, trash)]
        return len(files), sum(item.stat().st_size for item in files)
    tmp_count, tmp_size = metrics("tmp", exclude_trash=True)
    trash_count, trash_size = metrics("tmp/.trash")
    log_count, log_size = metrics("logs")
    old_traces = 0
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - 30 * 86400
    traces = upgrade / "traces"
    if traces.is_dir():
        require_within(traces, upgrade, "traces directory")
        old_traces = sum(1 for path in scan_files(traces) if path.stat().st_mtime < cutoff)
    old_checkpoints = 0
    checkpoints = upgrade / "checkpoints"
    if checkpoints.is_dir():
        require_within(checkpoints, upgrade, "checkpoints directory")
        for path in scan_files(checkpoints):
            match = re.search(r"phase-(\d+)", path.name)
            if match and int(match.group(1)) < phase - 2:
                old_checkpoints += 1
    suggested = tmp_size > 20 * 1024 * 1024 or tmp_count > 10 or log_size > 50 * 1024 * 1024 or log_count > 15 or old_traces > 0 or old_checkpoints > 0
    return {
        "suggested": suggested,
        "tmp": {"files": tmp_count, "bytes": tmp_size},
        "trash": {"files": trash_count, "bytes": trash_size},
        "logs": {"files": log_count, "bytes": log_size},
        "old_traces": old_traces,
        "old_checkpoints": old_checkpoints,
    }
