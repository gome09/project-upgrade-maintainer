"""Artifact policy review/apply commands."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
from typing import Any
from core.constants import *
from core.audit import Result
from core.errors import CliError, StalePlanError
from core.artifacts import artifact_registry_digest, load_artifact_registry, registry_entry_map, write_artifact_registry
from core.config import load_workspace_config, write_workspace_config
from core.paths import require_within
from core.plans import assert_file_snapshot_fresh, validate_apply_plan, write_plan_output
from core.schemas import normalize_registered_artifact_path, ordered_modules, validate_artifact_policy_entry, validate_artifact_registry
from core.workspace import ensure_workspace_structure, preflight_mutation_workspace, sha256_file
from ._runtime import atomic_write, build_review_plan, load_plan, now_iso, finalize_mutation
from ._common import summarize_actions
from ._artifacts import *
from ._bridges import plan_governance_contract_update, plan_workspace_readme_update, plan_guardrail_updates, sync_guardrails, sync_planned_texts

def artifact_policy_review(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root, require_config=True)
    config = load_workspace_config(upgrade)
    assert config is not None
    registry = load_artifact_registry(upgrade)
    registry_digest = artifact_registry_digest(registry)
    recorded_digest = config.get("artifact_registry_sha256")
    if recorded_digest not in {None, registry_digest}:
        raise CliError(
            f"{ARTIFACT_REGISTRY_NAME} changed outside the approved artifact-policy lifecycle; "
            "restore/reconcile it before generating a new policy plan"
        )
    path = normalize_registered_artifact_path(args.path)
    target = require_within(root / path, upgrade, "registered artifact")
    if target.is_symlink():
        raise CliError(f"registered artifact must not be a symlink: {path}")
    existing_map = registry_entry_map(registry)
    existing_policy = existing_map.get(path)

    if args.remove:
        if args.create:
            raise CliError("--remove cannot be combined with --create")
        if existing_policy is None:
            entry = {
                "path": path,
                "action": "keep",
                "reason": "artifact has no registered policy",
                "registry_sha256": registry_digest,
            }
        else:
            entry = {
                "path": path,
                "action": "unregister",
                "reason": "exact user request to remove policy linkage; artifact file itself is retained",
                "previous_policy": existing_policy,
                "registry_sha256": registry_digest,
            }
        plan = build_review_plan(root, "artifact-policy", [entry])
        result.plan_id = plan["plan_id"]
        result.result = {"plan": plan, "summary": summarize_actions(plan["entries"]), "classification": None}
        write_plan_output(root, args.plan_out, plan, result)
        return

    classification = artifact_policy_classification(path, args.purpose or "")
    explicit_kind = args.kind not in {None, "auto"}
    policy = build_artifact_policy_entry(args, classification)
    exists = target.exists()
    if exists and not target.is_file():
        raise CliError(f"registered artifact target is not a file: {path}")
    if args.create and exists:
        raise CliError(f"--create cannot overwrite an existing artifact: {path}")

    if classification["ambiguous"] and not explicit_kind:
        action = "confirm"
        reason = "classifier found conflicting governance/runtime signals; rerun with explicit --kind"
    elif not exists and not args.create:
        action = "confirm"
        reason = "artifact does not exist; create it first or rerun review with --create"
    elif existing_policy is None:
        action = "create-and-register" if args.create else "register-existing"
        reason = "new extension artifact policy"
    elif existing_policy == policy:
        action = "keep"
        reason = "registered artifact policy already matches the reviewed policy"
    else:
        action = "update-policy"
        reason = "registered artifact policy differs from the reviewed policy"

    entry: dict[str, Any] = {
        "path": path,
        "action": action,
        "reason": reason,
        "policy": policy,
        "classification": classification,
        "create_file": bool(args.create),
        "registry_sha256": registry_digest,
        "previous_policy": existing_policy,
    }
    if exists:
        stat = target.stat()
        entry.update({"sha256": sha256_file(target), "mtime_ns": stat.st_mtime_ns, "size": stat.st_size})
    elif args.create:
        initial = artifact_stub_text(policy)
        entry["initial_content_sha256"] = hashlib.sha256(initial.encode("utf-8")).hexdigest()

    plan = build_review_plan(root, "artifact-policy", [entry])
    result.plan_id = plan["plan_id"]
    result.result = {
        "plan": plan,
        "summary": summarize_actions(plan["entries"]),
        "classification": classification,
        "policy_preview": policy,
    }
    if action == "confirm":
        result.warnings.append(reason)
    write_plan_output(root, args.plan_out, plan, result)


def artifact_policy_apply(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root, require_config=True)
    plan = load_plan(Path(args.plan).resolve())
    entries = validate_apply_plan(root, plan, args.approve, "artifact-policy")
    config = load_workspace_config(upgrade)
    assert config is not None
    registry = load_artifact_registry(upgrade)
    current_digest = artifact_registry_digest(registry)
    recorded_digest = config.get("artifact_registry_sha256")
    if recorded_digest not in {None, current_digest}:
        raise StalePlanError(f"{ARTIFACT_REGISTRY_NAME} no longer matches the managed workspace config")

    executable = [entry for entry in entries if entry.get("action") in {"create-and-register", "register-existing", "update-policy", "unregister"}]
    if not executable:
        for entry in entries:
            result.skip(entry.get("path", "?"), f"non-executable action: {entry.get('action')}")
        return
    for entry in executable:
        if entry.get("registry_sha256") != current_digest:
            raise StalePlanError(f"artifact policy registry changed since review: {entry.get('path', '?')}")

    current_map = registry_entry_map(registry)
    new_map = dict(current_map)
    target_checks: list[tuple[dict[str, Any], Path]] = []
    required_modules: set[str] = set()
    for entry in executable:
        path = normalize_registered_artifact_path(str(entry.get("path", "")))
        target = require_within(root / path, upgrade, "registered artifact")
        if target.is_symlink():
            raise CliError(f"registered artifact must not be a symlink: {path}")
        action = entry.get("action")
        if action == "unregister":
            if path not in current_map:
                raise StalePlanError(f"artifact policy is no longer registered: {path}")
            if entry.get("previous_policy") != current_map[path]:
                raise StalePlanError(f"artifact policy changed since review: {path}")
            new_map.pop(path, None)
            continue

        policy = validate_artifact_policy_entry(entry.get("policy"))
        if policy["path"] != path:
            raise CliError(f"artifact policy path does not match plan entry: {path}")
        if action == "create-and-register":
            if target.exists():
                raise StalePlanError(f"artifact appeared after review: {path}")
            initial = artifact_stub_text(policy)
            if hashlib.sha256(initial.encode("utf-8")).hexdigest() != entry.get("initial_content_sha256"):
                raise StalePlanError(f"artifact initial content policy changed since review: {path}")
        else:
            if not target.is_file():
                raise StalePlanError(f"registered artifact changed or disappeared: {path}")
            assert_file_snapshot_fresh(
                target, entry, message=f"registered artifact changed since review: {path}"
            )
        if action == "update-policy" and entry.get("previous_policy") != current_map.get(path):
            raise StalePlanError(f"artifact policy changed since review: {path}")
        if action in {"create-and-register", "register-existing"} and path in current_map:
            raise StalePlanError(f"artifact policy was registered after review: {path}")
        new_map[path] = policy
        required_modules.add(policy["module"])
        target_checks.append((entry, target))

    new_registry = validate_artifact_registry({
        "schema_version": ARTIFACT_REGISTRY_SCHEMA_VERSION,
        "managed_by": "project-upgrade-maintainer",
        "entries": list(new_map.values()),
    })
    new_digest = artifact_registry_digest(new_registry)

    # Preflight all policy-linked text mutations before changing registry/artifact state.
    planned_contract = plan_governance_contract_update(upgrade, new_registry)
    planned_readme = plan_workspace_readme_update(upgrade, new_registry)
    planned_guardrails = plan_guardrail_updates(root, registry=new_registry)

    active_modules = ordered_modules(set(config["active_modules"]) | required_modules)
    ensure_workspace_structure(upgrade, result, args.dry_run, active_modules, writer=atomic_write)
    for entry, target in target_checks:
        if entry.get("action") != "create-and-register":
            continue
        policy = validate_artifact_policy_entry(entry["policy"])
        content = artifact_stub_text(policy)
        if atomic_write(target, content, dry_run=args.dry_run):
            result.change(entry["path"], "would_create" if args.dry_run else "created")

    write_artifact_registry(upgrade, new_registry, result, dry_run=args.dry_run, writer=atomic_write)
    sync_planned_texts(root, result, args.dry_run, planned_contract, planned_readme)
    sync_guardrails(root, result, args.dry_run, planned_guardrails)

    updated_config = dict(config)
    if active_modules != config["active_modules"]:
        updated_config["active_modules"] = active_modules
    updated_config["artifact_registry_sha256"] = new_digest
    updated_config["last_synced_skill_version"] = SKILL_VERSION
    updated_config["last_synced_policy_version"] = POLICY_VERSION
    if updated_config != config:
        updated_config["updated_at"] = now_iso()
    write_workspace_config(upgrade, updated_config, result, dry_run=args.dry_run, writer=atomic_write)

    for entry in entries:
        if entry.get("action") not in {"create-and-register", "register-existing", "update-policy", "unregister"}:
            result.skip(entry.get("path", "?"), f"non-executable action: {entry.get('action')}")
    result.result["artifact_policy"] = {
        "registered": len(new_registry["entries"]),
        "paths": [entry["path"] for entry in new_registry["entries"]],
        "active_modules": active_modules,
    }
    if not args.dry_run:
        finalize_mutation(root, result)
