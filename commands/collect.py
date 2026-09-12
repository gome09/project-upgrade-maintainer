"""Collect review/apply commands."""
from __future__ import annotations
import argparse
import json
import os
import hashlib
import re
import shutil
from core.transactions import transaction_copy, transaction_move
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from core.constants import *
from core.artifacts import empty_artifact_registry, load_artifact_registry, registered_artifact_paths
from core.audit import Result
from core.errors import CliError, StalePlanError
from core.paths import is_within, normalize_focuses, normalize_user_path, normalized, relative_posix, require_within, verified_upgrade
from core.plans import assert_file_snapshot_fresh, validate_apply_plan, write_plan_output
from core.workspace import activate_workspace_modules, preflight_mutation_workspace, read_text_file, sha256_file
from ._runtime import atomic_write, build_review_plan, load_plan, now_iso, finalize_mutation
from ._common import parse_assignments, summarize_actions
from ._inventory import git_inventory_paths, git_repo_root, ignored_collect_pathspecs, project_inventory, sensitive_reason
from ._workspace import module_for_upgrade_destination

def protected_path(relative: str) -> bool:
    path = Path(relative)
    parts = {part.casefold() for part in path.parts}
    return path.name.casefold() in PROTECTED_NAMES or bool(parts & PROTECTED_PARTS)


def choose_collect_destination(relative: str, action: str) -> str:
    path = Path(relative)
    name = path.name
    lower = relative.casefold()
    if action == "archive":
        directory = ".upgrade/archive/scripts" if path.suffix.casefold() in SCRIPT_EXTENSIONS else ".upgrade/archive"
    elif "requirement" in lower or "plan" in lower or "需求" in lower or "计划" in lower:
        directory = ".upgrade/docs"
    elif "report" in lower or "报告" in lower or "phase" in lower or "stage" in lower:
        directory = ".upgrade/reports"
    elif "risk" in lower or "风险" in lower:
        directory = ".upgrade/risks"
    elif "exception" in lower or "waiver" in lower or "例外" in lower or "豁免" in lower:
        directory = ".upgrade/exceptions"
    elif "delivery" in lower or "release" in lower or "acceptance" in lower or "交付" in lower or "发布" in lower:
        directory = ".upgrade/delivery"
    elif "decision" in lower or "决定" in lower:
        directory = ".upgrade/decisions"
    elif "review" in lower or "审查" in lower:
        directory = ".upgrade/reviews"
    elif "prompt" in lower or "提示词" in lower:
        directory = ".upgrade/prompts"
    elif path.suffix.casefold() == ".log":
        directory = ".upgrade/logs"
    elif path.suffix.casefold() in SCRIPT_EXTENSIONS:
        directory = ".upgrade/tmp/scripts"
    else:
        directory = ".upgrade/tmp"
    return f"{directory}/{name}"


def referenced_candidates(root: Path, candidates: list[Path], inventory: list[Path] | None = None) -> set[str]:
    names: dict[str, list[str]] = {}
    for path in candidates:
        names.setdefault(path.name, []).append(relative_posix(path, root))
    referenced: set[str] = set()
    if not names:
        return referenced
    rg = shutil.which("rg")
    if rg:
        pattern_file: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False) as stream:
                pattern_file = stream.name
                stream.write("\n".join(names) + "\n")
            rg_command = [rg, "--json", "--fixed-strings", "--file", pattern_file]
            for name in sorted(EXCLUDED_DIRS, key=str.casefold):
                rg_command.extend(["--glob", f"!**/{name}/**"])
            rg_command.append(".")
            completed = subprocess.run(
                rg_command,
                cwd=root, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False,
            )
            if completed.returncode in {0, 1}:
                candidate_sources = {relative_posix(path, root): path for path in candidates}
                for line in completed.stdout.splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") != "match":
                        continue
                    data = event.get("data", {})
                    hit_path = normalize_user_path(data.get("path", {}).get("text", ""))
                    for submatch in data.get("submatches", []):
                        name = submatch.get("match", {}).get("text", "")
                        for relative in names.get(name, []):
                            if hit_path != relative and relative in candidate_sources:
                                referenced.add(relative)
                return referenced
        except OSError:
            pass
        finally:
            if pattern_file:
                try:
                    os.unlink(pattern_file)
                except OSError:
                    pass
    # Ripgrep searches the repository directly. Only build a full inventory
    # for the standard-library fallback; this keeps focused reviews focused in
    # the common case without weakening cross-repository reference detection.
    if inventory is None:
        inventory = project_inventory(root)
    candidate_set = {path.resolve() for path in candidates}
    for path in inventory:
        if path.resolve() in candidate_set or path.suffix.casefold() not in TEXT_EXTENSIONS:
            continue
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            text = read_text_file(path).text
        except (OSError, CliError):
            continue
        for name, relatives in names.items():
            if name in text:
                referenced.update(relatives)
    return referenced


def collect_review(root: Path, args: argparse.Namespace, result: Result) -> None:
    decisions = parse_assignments(args.decision, {"move", "copy", "archive", "ignore"})
    allowed = {normalize_user_path(value) for value in (args.allow_protected or [])}
    allowed_sensitive = {normalize_user_path(value) for value in (args.allow_sensitive or [])}
    focuses = normalize_focuses(root, args.focus)
    files = project_inventory(root, focuses)
    candidates: list[Path] = []
    for path in files:
        relative = relative_posix(path, root)
        if SIGNALS.search(relative):
            candidates.append(path)
            continue
        # Content discovery is deliberately limited to shallow, non-protected
        # text documents to avoid turning normal source code into cleanup noise.
        if len(Path(relative).parts) <= 2 and not protected_path(relative) and path.suffix.casefold() in COLLECT_CONTENT_EXTENSIONS:
            try:
                if path.stat().st_size <= 2 * 1024 * 1024 and SIGNALS.search(read_text_file(path).text):
                    candidates.append(path)
            except (OSError, CliError):
                pass
    referenced = referenced_candidates(root, candidates, files if not focuses else None)
    entries: list[dict[str, Any]] = []
    upgrade = root / ".upgrade"
    registry = load_artifact_registry(upgrade, allow_missing=True) if upgrade.is_dir() and not upgrade.is_symlink() else empty_artifact_registry()
    registered_destinations = {path.casefold() for path in registered_artifact_paths(registry)}
    reserved_destinations: set[str] = set(registered_destinations)
    for path in candidates:
        relative = relative_posix(path, root)
        protected = protected_path(relative)
        sensitive = sensitive_reason(path)
        explicit = decisions.get(relative)
        is_referenced = relative in referenced
        if explicit == "ignore":
            action, classification, reason = "ignore", "ignore", "user decision"
        elif sensitive and (relative not in allowed_sensitive or not explicit):
            action, classification, reason = "forbidden", "sensitive", sensitive
        elif protected and (relative not in allowed or not explicit):
            action, classification, reason = "forbidden", "forbidden", "protected project material"
        elif explicit:
            action, classification, reason = explicit, explicit, "exact user decision"
        elif is_referenced:
            action, classification, reason = "confirm", "confirm", "reference signal found"
        elif path.suffix.casefold() in SCRIPT_EXTENSIONS:
            action, classification, reason = "confirm", "confirm", "temporary script requires a decision"
        elif any(token in relative.casefold() for token in ("tmp", "temp", "scratch", "debug", ".log")):
            action, classification, reason = "move", "move", "unreferenced temporary artifact"
        else:
            action, classification, reason = "copy", "copy", "upgrade artifact retained at source by default"
        destination = choose_collect_destination(relative, action) if action in {"move", "copy", "archive"} else None
        if destination:
            candidate_destination = destination
            destination_path = Path(destination)
            suffix = destination_path.suffix
            stem = destination_path.name[:-len(suffix)] if suffix else destination_path.name
            counter = 0
            reserved_canonical = {f".upgrade/{target}".casefold() for target in ALL_TEMPLATE_TARGETS.values()}
            while (
                candidate_destination.casefold() in reserved_destinations
                or candidate_destination.casefold() in reserved_canonical
                or (root / candidate_destination).exists()
            ):
                counter += 1
                discriminator = hashlib.sha256(f"{relative}:{counter}".encode("utf-8")).hexdigest()[:8]
                candidate_destination = (destination_path.parent / f"{stem}-{discriminator}{suffix}").as_posix()
            destination = candidate_destination
            reserved_destinations.add(destination.casefold())
        stat = path.stat()
        entries.append({
            "source": relative,
            "destination": destination,
            "action": action,
            "classification": classification,
            "reason": reason,
            "references_checked": True,
            "referenced": is_referenced,
            "protected": protected,
            "protected_authorized": bool(protected and relative in allowed and explicit),
            "sensitive": bool(sensitive),
            "sensitive_authorized": bool(sensitive and relative in allowed_sensitive and explicit),
            "confirmation_required": action in {"confirm", "forbidden"} or protected,
            "sha256": sha256_file(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        })
    plan = build_review_plan(root, "collect", entries)
    result.plan_id = plan["plan_id"]
    result.result = {"plan": plan, "summary": summarize_actions(entries)}
    write_plan_output(root, args.plan_out, plan, result)


def collect_apply(root: Path, args: argparse.Namespace, result: Result) -> None:
    preflight_mutation_workspace(root, require_config=True)
    plan = load_plan(Path(args.plan).resolve())
    entries = validate_apply_plan(root, plan, args.approve, "collect")
    allowed_protected = {normalize_user_path(value) for value in (args.allow_protected or [])}
    allowed_sensitive = {normalize_user_path(value) for value in (args.allow_sensitive or [])}
    registry = load_artifact_registry(verified_upgrade(root))
    registered_destinations = {path.casefold() for path in registered_artifact_paths(registry)}
    executable = [entry for entry in entries if entry.get("action") in {"move", "copy", "archive"}]
    seen_sources: set[str] = set()
    seen_destinations: set[str] = set()
    for entry in executable:
        source_relative = normalize_user_path(str(entry.get("source", "")))
        destination_relative = normalize_user_path(str(entry.get("destination", "")))
        if not source_relative or not destination_relative:
            raise CliError("collect plan contains an empty source or destination")
        if not destination_relative.startswith(COLLECT_DESTINATION_PREFIXES):
            raise CliError(f"collect destination is outside supported workspace collection areas: {destination_relative}")
        if destination_relative in MANAGED_CANONICAL_DESTINATIONS:
            raise CliError(f"collect destination cannot replace a managed canonical workspace file: {destination_relative}")
        if destination_relative.casefold() in registered_destinations:
            raise CliError(f"collect destination cannot replace a registered artifact policy target: {destination_relative}")
        source = require_within(root / source_relative, root, "collect source")
        if is_within(source, root / ".upgrade"):
            raise CliError(f"collect source must be outside .upgrade: {entry['source']}")
        actual_sensitive = sensitive_reason(source) if source.is_file() else None
        actual_protected = protected_path(source_relative)
        if bool(entry.get("sensitive")) != bool(actual_sensitive):
            raise StalePlanError(f"sensitive classification changed or is inconsistent with the plan: {entry.get('source')}")
        if bool(entry.get("protected")) != actual_protected:
            raise StalePlanError(f"protected classification is inconsistent with the plan: {entry.get('source')}")
        if actual_sensitive:
            if not entry.get("sensitive_authorized"):
                raise CliError(f"sensitive collect entry lacks review-time exact-path authorization: {entry.get('source')}")
            if source_relative not in allowed_sensitive:
                raise CliError(f"sensitive collect entry lacks apply-time exact-path authorization: {entry.get('source')}")
        if actual_protected:
            if not entry.get("protected_authorized"):
                raise CliError(f"protected collect entry lacks review-time exact-path authorization: {entry.get('source')}")
            if source_relative not in allowed_protected:
                raise CliError(f"protected collect entry lacks apply-time exact-path authorization: {entry.get('source')}")
        destination = require_within(root / destination_relative, verified_upgrade(root), "collect destination")
        source_key, destination_key = normalized(source), normalized(destination)
        if source_key in seen_sources or destination_key in seen_destinations:
            raise CliError("collect plan contains duplicate sources or destinations")
        seen_sources.add(source_key)
        seen_destinations.add(destination_key)
        assert_file_snapshot_fresh(
            source, entry, message=f"collect source changed or disappeared: {entry['source']}"
        )
        if destination.exists():
            raise StalePlanError(f"collect destination already exists: {entry['destination']}")
    required_modules = {
        module_for_upgrade_destination(str(entry.get("destination", "")))
        for entry in executable
        if entry.get("destination")
    }
    if required_modules:
        activate_workspace_modules(root, result, required_modules, dry_run=args.dry_run, clock=now_iso, writer=atomic_write)
    applied_records: list[dict[str, Any]] = []
    runtime_failed = False
    for entry in entries:
        action = entry.get("action")
        if action not in {"move", "copy", "archive"}:
            result.skip(entry.get("source", "?"), f"non-executable action: {action}")
            continue
        if args.dry_run:
            result.change(entry["source"], "would_" + action, destination=entry["destination"])
            continue
        source = root / entry["source"]
        destination = root / entry["destination"]
        try:
            if action == "copy":
                transaction_copy(source, destination)
            else:
                transaction_move(source, destination)
            result.change(entry["source"], action, destination=entry["destination"])
            applied_records.append(entry)
        except OSError as exc:
            runtime_failed = True
            result.errors.append(f"{entry['source']}: {exc}")
            break
    if runtime_failed:
        result.status = "partial"
    if not args.dry_run:
        finalize_mutation(root, result, collected=applied_records)
