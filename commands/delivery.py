"""Subject-bound Delivery Gate review, apply, and audit commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.audit import Result
from core.constants import DELIVERY_SCHEMA_VERSION
from core.errors import CliError, StalePlanError
from core.paths import relative_posix, require_within, verified_upgrade
from core.plans import validate_apply_plan, write_plan_output
from core.schemas import governance_record_digest, normalize_governance_id, validate_delivery_record
from core.subjects import refresh_subject, resolve_subject
from core.workspace import activate_workspace_modules, preflight_mutation_workspace
from ._delivery_state import delivery_evidence_state
from ._delivery_policy import load_delivery_policy
from ._governance_records import governance_record_id, read_json_object
from ._runtime import atomic_write, build_review_plan, finalize_mutation, load_plan, now_iso


def _path(upgrade: Path, delivery_id: str) -> Path:
    return require_within(upgrade / "delivery" / f"{delivery_id}.json", upgrade, "delivery record")


def delivery_review(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root, require_config=True)
    explicit_required_checks = list(dict.fromkeys(args.require_check or []))
    policy = load_delivery_policy(upgrade)
    policy_required_checks = list(policy["gates"].get(args.gate, [])) if policy else []
    required_checks = list(dict.fromkeys([*policy_required_checks, *explicit_required_checks]))
    subject = resolve_subject(
        root,
        subject_path=args.subject_path,
        subject_digest=args.subject_digest,
        subject_ref=args.subject_ref,
        fallback_ref=args.target,
    )
    state = delivery_evidence_state(
        root,
        subject=subject,
        required_checks=required_checks,
        scope=args.scope or "",
        environment=args.environment or "",
        gate=args.gate,
        evidence_controls=policy.get("controls") if policy else None,
    )
    semantic = {
        "gate": args.gate,
        "target": args.target,
        "scope": args.scope or "",
        "environment": args.environment or "",
        "acceptance_note": args.acceptance_note or "",
        "subject": subject,
        "required_checks": required_checks,
        "policy_required_checks": policy_required_checks,
        "delivery_policy_id": policy.get("policy_id") if policy else None,
        "delivery_policy_digest": policy.get("record_digest") if policy else None,
        "evidence_digest": state["evidence_digest"],
        "evaluated_at": state["evaluated_at"],
    }
    delivery_id = governance_record_id("DLV", semantic)
    entry = {
        "action": "accept-delivery-gate",
        "delivery_id": delivery_id,
        "gate": args.gate,
        "target": args.target,
        "scope": args.scope or "",
        "environment": args.environment or "",
        "subject": subject,
        "required_checks": required_checks,
        "policy_required_checks": policy_required_checks,
        "delivery_policy_id": policy.get("policy_id") if policy else None,
        "delivery_policy_digest": policy.get("record_digest") if policy else None,
        "acceptance_note": args.acceptance_note or "",
        "evaluated_at": state["evaluated_at"],
        "evidence_digest": state["evidence_digest"],
        "evidence_summary": state["summary"],
        "blockers": state["blockers"],
        "warnings": state["warnings"],
        "confirmation_required": True,
    }
    plan = build_review_plan(root, "delivery", [entry])
    result.plan_id = plan["plan_id"]
    result.result = {
        "plan": plan,
        "delivery_id": delivery_id,
        "gate": args.gate,
        "target": args.target,
        "subject": subject,
        "required_checks": required_checks,
        "policy_required_checks": policy_required_checks,
        "delivery_policy": {"policy_id": policy.get("policy_id"), "profile": policy.get("profile"), "record_digest": policy.get("record_digest")} if policy else None,
        "eligible": state["eligible"],
        "blockers": state["blockers"],
        "warnings": state["warnings"],
        "evidence_summary": state["summary"],
    }
    if state["blockers"]:
        result.status = "warning"
        result.warnings.append("delivery gate is blocked; resolve blockers and regenerate the delivery plan")
    result.warnings.extend(state["warnings"])
    write_plan_output(root, args.plan_out, plan, result)


def delivery_apply(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root, require_config=True)
    plan = load_plan(Path(args.plan).resolve())
    entries = validate_apply_plan(root, plan, args.approve, "delivery")
    if len(entries) != 1:
        raise CliError("delivery plan must contain exactly one entry")
    entry = entries[0]
    if entry.get("action") != "accept-delivery-gate":
        raise CliError("delivery plan contains an invalid action")
    delivery_id = normalize_governance_id(entry.get("delivery_id", ""), "DLV")
    reviewed_subject = entry.get("subject")
    if not isinstance(reviewed_subject, dict):
        raise CliError("delivery plan subject is invalid")
    current_subject = refresh_subject(root, reviewed_subject)
    if current_subject != reviewed_subject:
        raise StalePlanError("delivery subject changed since review; regenerate validation evidence and the delivery plan")
    required_checks = entry.get("required_checks")
    if not isinstance(required_checks, list) or not all(isinstance(item, str) for item in required_checks):
        raise CliError("delivery plan required_checks are invalid")
    scope = entry.get("scope", "")
    environment = entry.get("environment", "")
    if not isinstance(scope, str) or not isinstance(environment, str):
        raise CliError("delivery plan scope/environment is invalid")
    gate = entry.get("gate")
    if not isinstance(gate, str):
        raise CliError("delivery plan gate is invalid")
    policy_required_checks = entry.get("policy_required_checks")
    if not isinstance(policy_required_checks, list) or not all(isinstance(item, str) for item in policy_required_checks):
        raise CliError("delivery plan policy_required_checks are invalid")
    current_policy = load_delivery_policy(upgrade)
    current_policy_id = current_policy.get("policy_id") if current_policy else None
    current_policy_digest = current_policy.get("record_digest") if current_policy else None
    if current_policy_id != entry.get("delivery_policy_id") or current_policy_digest != entry.get("delivery_policy_digest"):
        raise StalePlanError("delivery policy changed since review; regenerate the delivery plan")
    if current_policy is not None:
        expected_policy_checks = list(current_policy["gates"].get(gate, []))
        if expected_policy_checks != policy_required_checks or not set(expected_policy_checks) <= set(required_checks):
            raise StalePlanError("delivery policy requirements changed since review; regenerate the delivery plan")
    elif policy_required_checks:
        raise StalePlanError("delivery policy requirements changed since review; regenerate the delivery plan")

    current = delivery_evidence_state(
        root,
        subject=current_subject,
        required_checks=required_checks,
        scope=scope,
        environment=environment,
        gate=gate,
        evidence_controls=current_policy.get("controls") if current_policy else None,
    )
    if current["evidence_digest"] != entry.get("evidence_digest"):
        raise StalePlanError("delivery governance evidence changed since review; regenerate the delivery plan")
    for key, value in (("evidence_summary", current["summary"]), ("blockers", current["blockers"]), ("warnings", current["warnings"])):
        if entry.get(key) != value:
            raise StalePlanError("delivery readiness state changed since review; regenerate the delivery plan")
    blockers = entry.get("blockers")
    if not isinstance(blockers, list):
        raise CliError("delivery plan blockers are invalid")
    if blockers:
        raise CliError("delivery gate remains blocked; resolve all blockers and run delivery-review again")
    if not isinstance(entry.get("warnings"), list) or not isinstance(entry.get("evidence_summary"), dict):
        raise CliError("delivery plan evidence summary is invalid")

    record = {
        "schema_version": DELIVERY_SCHEMA_VERSION,
        "kind": "delivery",
        "delivery_id": delivery_id,
        "recorded_at": now_iso(),
        "gate": gate,
        "target": entry.get("target"),
        "scope": scope,
        "environment": environment,
        "subject": current_subject,
        "required_checks": required_checks,
        "policy_required_checks": policy_required_checks,
        "delivery_policy_id": entry.get("delivery_policy_id"),
        "delivery_policy_digest": entry.get("delivery_policy_digest"),
        "evidence_digest": entry.get("evidence_digest"),
        "evidence_summary": entry.get("evidence_summary"),
        "blockers": [],
        "warnings": entry.get("warnings"),
        "acceptance_note": entry.get("acceptance_note", ""),
        "outcome": "accepted",
        "authority": "user",
        "approval_basis": "exact-plan-id",
        "approval_plan_id": plan["plan_id"],
    }
    record["record_digest"] = governance_record_digest(record)
    record = validate_delivery_record(record)
    target = _path(upgrade, delivery_id)
    if target.exists() or target.is_symlink():
        raise CliError(f"delivery record already exists: {relative_posix(target, root)}")
    activate_workspace_modules(root, result, {"review"}, dry_run=args.dry_run, clock=now_iso, writer=atomic_write)
    if atomic_write(target, json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", dry_run=args.dry_run):
        result.change(relative_posix(target, root), "would_create" if args.dry_run else "created")
    result.result = {"delivery": record}
    result.warnings.extend(record["warnings"])
    if not args.dry_run:
        finalize_mutation(root, result)


def delivery_audit(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = verified_upgrade(root)
    raw = upgrade / "delivery"
    if raw.is_symlink():
        raise CliError("delivery directory must not be a symlink")
    directory = require_within(raw, upgrade, "delivery directory")
    requested = normalize_governance_id(args.delivery_id, "DLV") if args.delivery_id else None
    gates = set(args.gate or [])
    if not directory.exists():
        result.result = {"count": 0, "counts_by_gate": {}, "deliveries": []}
        return
    if not directory.is_dir():
        raise CliError("delivery directory is not a directory")
    records: list[dict] = []
    counts: dict[str, int] = {}
    for path in sorted(directory.glob("DLV-*.json")):
        record = validate_delivery_record(read_json_object(path, upgrade, "delivery record"))
        if path.name != f"{record['delivery_id']}.json":
            raise CliError(f"delivery filename does not match record id: {path.name}")
        counts[record["gate"]] = counts.get(record["gate"], 0) + 1
        if requested and record["delivery_id"] != requested:
            continue
        if gates and record["gate"] not in gates:
            continue
        records.append(record)
    if requested and not records:
        raise CliError(f"delivery record not found for requested filter: {requested}")
    result.result = {
        "count": len(records),
        "counts_by_gate": counts,
        "deliveries": sorted(records, key=lambda item: (item["recorded_at"], item["delivery_id"])),
    }
