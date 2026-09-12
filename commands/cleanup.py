"""Cleanup review/apply commands."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import re
import shutil
from pathlib import Path
from typing import Any
from core.constants import *
from core.artifacts import load_artifact_registry
from core.audit import Result
from core.errors import CliError, StalePlanError
from core.paths import normalize_user_path, normalized, relative_posix, require_within, verified_upgrade
from core.plans import assert_file_snapshot_fresh, validate_apply_plan, write_plan_output
from core.transactions import transaction_move
from core.workspace import activate_workspace_modules, preflight_mutation_workspace, read_text_file, sha256_file
from ._runtime import atomic_write, build_review_plan, load_plan, now_iso, finalize_mutation
from ._common import parse_assignments, parse_replacements, scan_files, summarize_actions
from ._governance import cleanup_thresholds, current_phase, protected_cleanup_path

def reserve_collision_safe_destination(root: Path, candidate: str, reserved: set[str], seed: str) -> str:
    destination = candidate
    counter = 0
    while destination.casefold() in reserved or (root / destination).exists():
        counter += 1
        discriminator = hashlib.sha256(f"{seed}:{counter}".encode("utf-8")).hexdigest()[:8]
        destination = f"{candidate}-{discriminator}"
    reserved.add(destination.casefold())
    return destination


def cleanup_review(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = verified_upgrade(root)
    decisions = parse_assignments(args.decision, {"archive", "delete", "keep", "update", "merge"})
    replacements = parse_replacements(args.replacement, root)
    phase = current_phase(upgrade)
    registry = load_artifact_registry(upgrade, allow_missing=True)
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - 30 * 86400
    entries: list[dict[str, Any]] = []
    reserved_destinations: set[str] = set()
    for path in scan_files(upgrade):
        relative_upgrade = relative_posix(path, upgrade)
        relative = f".upgrade/{relative_upgrade}"
        if protected_cleanup_path(relative_upgrade, phase, registry) or path.name == ".gitkeep" or relative_upgrade.startswith("archive/") or relative_upgrade.startswith("tmp/.trash/"):
            continue
        explicit = decisions.get(relative) or decisions.get(relative_upgrade)
        if explicit:
            action, reason = explicit, "exact user decision"
        elif relative_upgrade.startswith("tmp/"):
            action, reason = "delete", "disposable temporary artifact"
        elif relative_upgrade.startswith("logs/"):
            action, reason = "confirm", "raw log requires a decision"
        elif relative_upgrade.startswith("traces/") and path.stat().st_mtime < cutoff:
            action, reason = "archive", "trace is older than 30 days"
        elif relative_upgrade.startswith("checkpoints/"):
            match = re.search(r"phase-(\d+)", path.name)
            if match and int(match.group(1)) < phase - 2:
                action, reason = "archive", "checkpoint is older than current phase minus two"
            else:
                continue
        elif re.search(r"draft|old|obsolete|superseded|草稿|旧", path.name, re.IGNORECASE):
            action, reason = "archive", "historical or superseded artifact"
        else:
            continue
        replacement = replacements.get(relative) or replacements.get(relative_upgrade)
        if action in {"update", "merge"} and replacement is None:
            action, reason = "confirm", f"{action} requires --replacement PATH=FILE"
        if action == "archive":
            digest = hashlib.sha256(relative_upgrade.encode("utf-8")).hexdigest()[:8]
            archive_name = f"{Path(relative_upgrade).name}-{digest}"
            destination = reserve_collision_safe_destination(
                root, f".upgrade/archive/{archive_name}", reserved_destinations, relative_upgrade
            )
        elif action == "delete":
            digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:10]
            trash_name = f"{digest}-{path.name}"
            destination = reserve_collision_safe_destination(
                root, f".upgrade/tmp/.trash/{trash_name}", reserved_destinations, relative
            )
        else:
            destination = None
        stat = path.stat()
        entries.append({
            "source": relative,
            "destination": destination,
            "action": action,
            "classification": action,
            "reason": reason,
            "confirmation_required": action == "confirm",
            "sha256": sha256_file(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        })
        if replacement is not None and action in {"update", "merge"}:
            entries[-1]["replacement_text"] = replacement
    plan = build_review_plan(root, "cleanup", entries)
    result.plan_id = plan["plan_id"]
    result.result = {"plan": plan, "summary": summarize_actions(entries), "thresholds": cleanup_thresholds(upgrade, phase)}
    write_plan_output(root, args.plan_out, plan, result)


def cleanup_apply(root: Path, args: argparse.Namespace, result: Result) -> None:
    preflight_mutation_workspace(root, require_config=True)
    plan = load_plan(Path(args.plan).resolve())
    entries = validate_apply_plan(root, plan, args.approve, "cleanup")
    executable = [entry for entry in entries if entry.get("action") in {"delete", "archive", "update", "merge"}]
    upgrade = verified_upgrade(root)
    phase = current_phase(upgrade)
    registry = load_artifact_registry(upgrade)
    seen_sources: set[str] = set()
    seen_destinations: set[str] = set()
    for entry in executable:
        source_relative = normalize_user_path(str(entry.get("source", "")))
        if not source_relative.startswith(".upgrade/"):
            raise CliError(f"cleanup source must be inside .upgrade: {source_relative}")
        source = require_within(root / source_relative, upgrade, "cleanup source")
        resolved_relative = relative_posix(source, upgrade)
        if protected_cleanup_path(resolved_relative, phase, registry):
            raise CliError(f"cleanup cannot mutate a retained workspace file: {source_relative}")
        source_key = normalized(source)
        if source_key in seen_sources:
            raise CliError("cleanup plan contains duplicate sources")
        seen_sources.add(source_key)
        assert_file_snapshot_fresh(
            source, entry, message=f"cleanup source changed or disappeared: {entry['source']}"
        )
        if entry["action"] in {"update", "merge"} and not isinstance(entry.get("replacement_text"), str):
            raise CliError(f"cleanup {entry['action']} entry lacks replacement_text: {entry['source']}")
        if entry["action"] in {"archive", "delete"}:
            destination_relative = normalize_user_path(str(entry.get("destination", "")))
            destination_base = upgrade / "archive" if entry["action"] == "archive" else upgrade / "tmp" / ".trash"
            destination_label = "archive destination" if entry["action"] == "archive" else "trash destination"
            destination = require_within(root / destination_relative, destination_base, destination_label)
            destination_key = normalized(destination)
            if destination_key in seen_destinations:
                raise CliError("cleanup plan contains duplicate destinations")
            seen_destinations.add(destination_key)
            if destination.exists():
                raise StalePlanError(f"cleanup destination exists: {entry['destination']}")
    if any(entry.get("action") in {"archive", "delete"} for entry in executable):
        activate_workspace_modules(root, result, {"lifecycle"}, dry_run=args.dry_run, clock=now_iso, writer=atomic_write)
        upgrade = verified_upgrade(root)
    runtime_failed = False
    for entry in entries:
        action = entry.get("action")
        if action not in {"delete", "archive", "update", "merge"}:
            result.skip(entry.get("source", "?"), f"non-executable action: {action}")
            continue
        if args.dry_run:
            result.change(entry["source"], "would_" + action, destination=entry.get("destination"))
            continue
        source = root / entry["source"]
        try:
            if action == "delete":
                trash = require_within(root / entry["destination"], upgrade / "tmp" / ".trash", "trash destination")
                transaction_move(source, trash)
            elif action == "archive":
                destination = root / entry["destination"]
                transaction_move(source, destination)
            else:
                atomic_write(source, entry["replacement_text"], source=read_text_file(source))
            result.change(entry["source"], "trashed" if action == "delete" else action, destination=entry.get("destination"))
        except OSError as exc:
            runtime_failed = True
            result.errors.append(f"{entry['source']}: {exc}")
            break
    if runtime_failed:
        result.status = "partial"
    if not args.dry_run:
        finalize_mutation(root, result)
