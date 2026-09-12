"""Project authority-source review, apply, and audit commands."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from core.audit import Result
from core.authority import (
    authority_entry_map,
    authority_registry_digest,
    build_authority_entry,
    classify_authority_candidate,
    discover_authority_candidates,
    empty_authority_registry,
    load_authority_registry,
    normalize_authority_source_path,
    plan_authority_view_update,
    write_authority_registry,
)
from core.config import load_workspace_config, write_workspace_config
from core.constants import AUTHORITY_REGISTRY_NAME
from core.errors import CliError, StalePlanError
from core.paths import require_within
from core.plans import assert_file_snapshot_fresh, validate_apply_plan, write_plan_output
from core.schemas import validate_authority_registry
from core.workspace import preflight_mutation_workspace, sha256_file

from ._common import summarize_actions
from ._inventory import project_inventory
from ._runtime import atomic_write, build_review_plan, finalize_mutation, load_plan, now_iso
from ._bridges import sync_planned_texts


def _parse_path_values(values: list[str] | None, label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw in values or []:
        if "=" not in raw:
            raise CliError(f"{label} must use PATH=VALUE")
        path, value = raw.split("=", 1)
        path = normalize_authority_source_path(path.strip())
        if not value.strip():
            raise CliError(f"{label} value must not be empty for {path}")
        parsed[path] = value.strip()
    return parsed


def _bool_override(value: str | None, path: str) -> bool | None:
    if value is None:
        return None
    normalized = value.casefold()
    if normalized in {"yes", "true", "1"}:
        return True
    if normalized in {"no", "false", "0"}:
        return False
    raise CliError(f"--required for {path} must be yes or no")


def _candidate_map(root: Path) -> dict[str, dict[str, Any]]:
    return {item["path"]: item for item in discover_authority_candidates(root, project_inventory(root))}


def _manual_classification(path: str) -> dict[str, Any]:
    classification = classify_authority_candidate(path)
    if classification is None:
        return {
            "path": path,
            "recommended_kind": "reference",
            "recommended_scope": str(Path(path).parent).replace("\\", "/") if Path(path).parent != Path(".") else ".",
            "recommended_authority": "reference",
            "recommended_required": False,
            "confidence": "low",
            "reason": "user-selected path has no built-in authority classifier signal",
        }
    return classification


def authority_review(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root, require_config=True)
    config = load_workspace_config(upgrade)
    assert config is not None
    registry = load_authority_registry(upgrade)
    digest = authority_registry_digest(registry)
    if config.get("authority_registry_sha256") != digest:
        raise CliError(
            f"{AUTHORITY_REGISTRY_NAME} changed outside the approved authority lifecycle; "
            "restore/reconcile it before generating a new authority plan"
        )

    existing = authority_entry_map(registry)
    candidates = _candidate_map(root)
    kinds = _parse_path_values(args.kind, "--kind")
    scopes = _parse_path_values(args.scope, "--scope")
    authorities = _parse_path_values(args.authority, "--authority")
    required = _parse_path_values(args.required, "--required")
    accepted = [normalize_authority_source_path(path) for path in (args.accept or [])]
    removed = [normalize_authority_source_path(path) for path in (args.remove or [])]
    ignored = [normalize_authority_source_path(path) for path in (args.ignore or [])]
    selections = accepted + removed + ignored
    if len({path.casefold() for path in selections}) != len(selections):
        raise CliError("an authority path may be selected by only one of --accept, --remove, or --ignore")

    entries: list[dict[str, Any]] = []
    if not selections:
        for path, candidate in sorted(candidates.items(), key=lambda item: item[0].casefold()):
            previous = existing.get(path)
            raw_target = root / path
            target = require_within(raw_target, root, "authority candidate") if not raw_target.is_symlink() else raw_target
            current_sha = sha256_file(target) if target.is_file() and not raw_target.is_symlink() else None
            action = "keep" if previous and previous.get("sha256") == current_sha else "confirm"
            reason = "registered authority source is current" if action == "keep" else "candidate requires explicit --accept before registration or rebind"
            entries.append({
                "path": path,
                "action": action,
                "reason": reason,
                "classification": candidate,
                "previous_entry": previous,
                "registry_sha256": digest,
            })
        for path, previous in sorted(existing.items(), key=lambda item: item[0].casefold()):
            if path in candidates:
                continue
            raw_target = root / path
            target = require_within(raw_target, root, "registered authority source") if not raw_target.is_symlink() else raw_target
            current_sha = sha256_file(target) if target.is_file() and not raw_target.is_symlink() else None
            action = "keep" if current_sha == previous.get("sha256") else "confirm"
            entries.append({
                "path": path,
                "action": action,
                "reason": "registered authority source is current" if action == "keep" else "registered authority source is missing or drifted; explicit --accept or --remove is required",
                "classification": _manual_classification(path),
                "previous_entry": previous,
                "registry_sha256": digest,
            })
    else:
        for path in accepted:
            classification = candidates.get(path) or _manual_classification(path)
            entry = build_authority_entry(
                root,
                classification,
                kind=kinds.get(path),
                scope=scopes.get(path),
                authority=authorities.get(path),
                required=_bool_override(required.get(path), path),
            )
            raw_target = root / path
            if raw_target.is_symlink():
                raise CliError(f"authority source must not be a symlink: {path}")
            target = require_within(raw_target, root, "authority source")
            stat = target.stat()
            previous = existing.get(path)
            action = "register" if previous is None else "keep" if previous == entry else "rebind"
            entries.append({
                "path": path,
                "action": action,
                "reason": "new approved authority source" if action == "register" else "registered authority source already matches reviewed content/policy" if action == "keep" else "registered authority source content or policy changed and requires explicit rebind approval",
                "classification": classification,
                "entry": entry,
                "previous_entry": previous,
                "registry_sha256": digest,
                "sha256": entry["sha256"],
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
            })
        for path in removed:
            previous = existing.get(path)
            entries.append({
                "path": path,
                "action": "unregister" if previous else "keep",
                "reason": "exact user request to remove authority linkage; project file itself is retained" if previous else "authority source is not registered",
                "previous_entry": previous,
                "registry_sha256": digest,
            })
        for path in ignored:
            entries.append({
                "path": path,
                "action": "ignore",
                "reason": "candidate intentionally left unregistered; ignore is not persisted",
                "classification": candidates.get(path) or _manual_classification(path),
                "registry_sha256": digest,
            })

    plan = build_review_plan(root, "authority", entries)
    result.plan_id = plan["plan_id"]
    result.result = {
        "plan": plan,
        "summary": summarize_actions(entries),
        "registered": len(existing),
        "discovered_candidates": len(candidates),
        "note": "Discovery is advisory. Only register/rebind/unregister actions become executable after exact plan approval.",
    }
    if any(entry.get("action") == "confirm" for entry in entries):
        result.warnings.append("authority candidates or drift require explicit --accept/--remove review before any registry mutation")
    write_plan_output(root, args.plan_out, plan, result)


def authority_apply(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root, require_config=True)
    plan = load_plan(Path(args.plan).resolve())
    entries = validate_apply_plan(root, plan, args.approve, "authority")
    config = load_workspace_config(upgrade)
    assert config is not None
    registry = load_authority_registry(upgrade)
    current_digest = authority_registry_digest(registry)
    if config.get("authority_registry_sha256") != current_digest:
        raise StalePlanError(f"{AUTHORITY_REGISTRY_NAME} no longer matches the managed workspace config")

    executable = [entry for entry in entries if entry.get("action") in {"register", "rebind", "unregister"}]
    if not executable:
        for entry in entries:
            result.skip(entry.get("path", "?"), f"non-executable action: {entry.get('action')}")
        return
    for entry in executable:
        if entry.get("registry_sha256") != current_digest:
            raise StalePlanError(f"authority registry changed since review: {entry.get('path', '?')}")

    current = authority_entry_map(registry)
    updated = dict(current)
    for plan_entry in executable:
        path = normalize_authority_source_path(str(plan_entry.get("path", "")))
        action = plan_entry["action"]
        if action == "unregister":
            if current.get(path) != plan_entry.get("previous_entry"):
                raise StalePlanError(f"authority registration changed since review: {path}")
            updated.pop(path, None)
            continue
        proposed = plan_entry.get("entry")
        candidate_registry = validate_authority_registry({
            "schema_version": registry["schema_version"],
            "managed_by": registry["managed_by"],
            "entries": [proposed],
        })
        proposed = candidate_registry["entries"][0]
        raw_target = root / path
        if raw_target.is_symlink():
            raise CliError(f"authority source must not be a symlink: {path}")
        target = require_within(raw_target, root, "authority source")
        assert_file_snapshot_fresh(target, plan_entry, message=f"authority source changed since review: {path}")
        if proposed["sha256"] != sha256_file(target):
            raise StalePlanError(f"authority source digest changed since review: {path}")
        if action == "register" and path in current:
            raise StalePlanError(f"authority source was registered after review: {path}")
        if action == "rebind" and current.get(path) != plan_entry.get("previous_entry"):
            raise StalePlanError(f"authority registration changed since review: {path}")
        updated[path] = proposed

    new_registry = validate_authority_registry({
        "schema_version": registry["schema_version"],
        "managed_by": registry["managed_by"],
        "entries": list(updated.values()),
    })
    new_digest = authority_registry_digest(new_registry)
    planned_view = plan_authority_view_update(upgrade, new_registry)
    write_authority_registry(upgrade, new_registry, result, dry_run=args.dry_run, writer=atomic_write)
    sync_planned_texts(root, result, args.dry_run, planned_view)
    config = dict(config)
    config["authority_registry_sha256"] = new_digest
    config["updated_at"] = now_iso()
    write_workspace_config(upgrade, config, result, dry_run=args.dry_run, writer=atomic_write)
    result.result = {"registered": len(new_registry["entries"]), "authority_registry_sha256": new_digest}
    if not args.dry_run:
        finalize_mutation(root, result)


def authority_audit(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root, require_config=True)
    registry = load_authority_registry(upgrade)
    registered = authority_entry_map(registry)
    findings: list[dict[str, Any]] = []
    for path, entry in sorted(registered.items(), key=lambda item: item[0].casefold()):
        raw_target = root / path
        target = require_within(raw_target, root, "authority source") if not raw_target.is_symlink() else raw_target
        if raw_target.is_symlink():
            status = "unsafe"
        elif not target.is_file():
            status = "missing"
        elif sha256_file(target) != entry["sha256"]:
            status = "drifted"
        else:
            status = "current"
        findings.append({"path": path, "status": status, "entry": entry})
    candidates = [item for item in discover_authority_candidates(root, project_inventory(root)) if item["path"] not in registered]
    result.result = {
        "registered": findings,
        "unregistered_candidates": candidates,
        "registry_sha256": authority_registry_digest(registry),
    }
    if any(item["status"] in {"unsafe", "missing", "drifted"} for item in findings):
        result.status = "warning"
        result.warnings.append("registered authority sources require review")
