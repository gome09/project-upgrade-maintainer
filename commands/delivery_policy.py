"""Project-level Delivery Policy review, apply, and audit commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.audit import Result
from core.constants import DELIVERY_GATES, DELIVERY_POLICY_SCHEMA_VERSION
from core.errors import CliError, StalePlanError
from core.paths import relative_posix
from core.plans import validate_apply_plan, write_plan_output
from core.schemas import governance_identity_id, governance_record_digest, validate_delivery_policy
from core.workspace import activate_workspace_modules, ensure_workspace_structure, preflight_mutation_workspace
from ._delivery_policy import delivery_policy_path, load_delivery_policy
from ._runtime import atomic_write, build_review_plan, finalize_mutation, load_plan, now_iso


def _checks(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        value = raw.strip()
        if not value:
            continue
        if any(character in value for character in ("\x00", "\n", "\r")) or len(value) > 500:
            raise CliError("delivery policy validation checks must be single-line values up to 500 characters")
        if value not in out:
            out.append(value)
    return out


def _proposal(args: argparse.Namespace) -> dict:
    profile = (args.profile or "project").strip()
    if not profile or any(character in profile for character in ("\x00", "\n", "\r")) or len(profile) > 200:
        raise CliError("delivery policy profile must be a non-empty single-line value up to 200 characters")
    note = args.note or ""
    if "\x00" in note or len(note) > 8000:
        raise CliError("delivery policy note is invalid or too long")
    gates = {
        "readiness": _checks(args.readiness_check),
        "final-acceptance": _checks(args.final_check),
        "rollback-readiness": _checks(args.rollback_check),
    }
    if not any(gates.values()):
        raise CliError("delivery policy must require at least one validation check across its gates")
    semantic = {"profile": profile, "gates": gates, "note": note}
    return {
        "schema_version": DELIVERY_POLICY_SCHEMA_VERSION,
        "kind": "delivery-policy",
        "policy_id": governance_identity_id("DLP", semantic),
        **semantic,
    }


def delivery_policy_review(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root, require_config=True)
    current = load_delivery_policy(upgrade)
    proposal = _proposal(args)
    entry = {
        "action": "set-delivery-policy",
        "policy": proposal,
        "replaces_record_digest": current.get("record_digest") if current else None,
        "confirmation_required": True,
    }
    plan = build_review_plan(root, "delivery-policy", [entry])
    result.plan_id = plan["plan_id"]
    result.result = {
        "plan": plan,
        "policy_id": proposal["policy_id"],
        "profile": proposal["profile"],
        "gates": proposal["gates"],
        "replaces_record_digest": entry["replaces_record_digest"],
    }
    write_plan_output(root, args.plan_out, plan, result)


def delivery_policy_apply(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root, require_config=True)
    plan = load_plan(Path(args.plan).resolve())
    entries = validate_apply_plan(root, plan, args.approve, "delivery-policy")
    if len(entries) != 1 or entries[0].get("action") != "set-delivery-policy":
        raise CliError("delivery-policy plan must contain exactly one set-delivery-policy action")
    entry = entries[0]
    proposal = entry.get("policy")
    if not isinstance(proposal, dict):
        raise CliError("delivery-policy plan policy is invalid")
    current = load_delivery_policy(upgrade)
    current_digest = current.get("record_digest") if current else None
    if current_digest != entry.get("replaces_record_digest"):
        raise StalePlanError("delivery policy changed since review; regenerate the policy plan")
    record = {
        **proposal,
        "recorded_at": now_iso(),
        "authority": "user",
        "approval_basis": "exact-plan-id",
        "approval_plan_id": plan["plan_id"],
    }
    record["record_digest"] = governance_record_digest(record)
    record = validate_delivery_policy(record)
    activate_workspace_modules(root, result, {"review"}, dry_run=args.dry_run, clock=now_iso, writer=atomic_write)
    ensure_workspace_structure(upgrade, result, args.dry_run, {"review"}, writer=atomic_write)
    target = delivery_policy_path(upgrade)
    source = None
    if target.exists():
        from core.workspace import read_text_file
        source = read_text_file(target)
    if atomic_write(target, json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", source=source, dry_run=args.dry_run):
        result.change(relative_posix(target, root), "would_update" if args.dry_run and target.exists() else ("would_create" if args.dry_run else ("updated" if source else "created")))
    result.result = {"delivery_policy": record}
    if not args.dry_run:
        finalize_mutation(root, result)


def delivery_policy_audit(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root, require_config=True)
    policy = load_delivery_policy(upgrade)
    result.result = {"configured": policy is not None, "delivery_policy": policy}
