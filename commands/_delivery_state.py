"""Deterministic Delivery-domain evidence aggregation.

Delivery consumes Core-validated governance/evidence records but does not
import Governance command handlers. Evidence aggregation binds subject,
scope/environment, and the explicit validation matrix.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from core.constants import DECISION_SCHEMA_VERSION, EXCEPTION_SCHEMA_VERSION, RISK_SCHEMA_VERSION
from core.errors import CliError
from core.paths import relative_posix, require_within, verified_upgrade
from core.schemas import (
    validate_decision_record,
    validate_exception_record,
    validate_risk_history,
    validate_validation_history,
)
from core.transactions import current_transaction, unresolved_transactions
from core.workspace import read_text_file, section_value, sha256_file
from ._governance_records import read_json_object, read_jsonl_objects
from ._runtime import now_iso


def _record_directory(upgrade: Path, name: str, label: str) -> Path:
    raw = upgrade / name
    if raw.is_symlink():
        raise CliError(f"{label} must not be a symlink")
    directory = require_within(raw, upgrade, label)
    if directory.exists() and not directory.is_dir():
        raise CliError(f"{label} is not a directory")
    return directory


def load_decision_records(upgrade: Path) -> list[dict[str, Any]]:
    directory = _record_directory(upgrade, "decisions", "decision directory")
    if not directory.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(directory.glob("DEC-*.json"), key=lambda item: item.name.casefold()):
        record = validate_decision_record(read_json_object(path, upgrade, "decision record"))
        if path.name != f"{record['decision_id']}.json":
            raise CliError(f"decision filename does not match record id: {path.name}")
        out.append(record)
    ids = {record["decision_id"] for record in out}
    for record in out:
        if record["supersedes"] and record["supersedes"] not in ids:
            raise CliError(f"decision supersedes a missing record: {record['supersedes']}")
    return out


def active_decisions(upgrade: Path) -> list[dict[str, Any]]:
    records = load_decision_records(upgrade)
    superseded = {record["supersedes"] for record in records if record["supersedes"]}
    return sorted(
        (record for record in records if record["decision_id"] not in superseded),
        key=lambda record: (record["recorded_at"], record["decision_id"]),
    )


def latest_risks(upgrade: Path) -> list[dict[str, Any]]:
    directory = _record_directory(upgrade, "risks", "risk directory")
    if not directory.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(directory.glob("RISK-*.jsonl"), key=lambda item: item.name.casefold()):
        history = validate_risk_history(read_jsonl_objects(path, upgrade, "risk history"))
        record = history[-1]
        if path.name != f"{record['risk_id']}.jsonl":
            raise CliError(f"risk filename does not match record id: {path.name}")
        out.append({**record, "history_length": len(history)})
    return sorted(out, key=lambda record: (record["recorded_at"], record["risk_id"]))


def load_exception_records(upgrade: Path) -> list[dict[str, Any]]:
    directory = _record_directory(upgrade, "exceptions", "exception directory")
    if not directory.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(directory.glob("EXC-*.json"), key=lambda item: item.name.casefold()):
        record = validate_exception_record(read_json_object(path, upgrade, "exception record"))
        if path.name != f"{record['exception_id']}.json":
            raise CliError(f"exception filename does not match record id: {path.name}")
        out.append(record)
    by_id = {record["exception_id"]: record for record in out}
    children: dict[str, list[str]] = {}
    for record in out:
        supersedes = record["supersedes"]
        if not supersedes:
            continue
        if supersedes not in by_id:
            raise CliError(f"exception supersedes a missing record: {supersedes}")
        children.setdefault(supersedes, []).append(record["exception_id"])
    branches = {key: value for key, value in children.items() if len(value) > 1}
    if branches:
        parent = sorted(branches)[0]
        raise CliError(f"exception history branches from {parent}; reconcile duplicate superseding records")
    return out


def current_exceptions(upgrade: Path) -> list[dict[str, Any]]:
    records = load_exception_records(upgrade)
    superseded = {record["supersedes"] for record in records if record["supersedes"]}
    return sorted(
        (record for record in records if record["exception_id"] not in superseded),
        key=lambda record: (record["recorded_at"], record["exception_id"]),
    )


def _parse_timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def exception_expired(record: dict[str, Any], at: str) -> bool:
    expires = record.get("expires_at")
    return bool(expires and _parse_timestamp(expires) <= _parse_timestamp(at))


def validation_records(upgrade: Path) -> list[dict[str, Any]]:
    path = require_within(upgrade / "validation" / "records.jsonl", upgrade, "validation records")
    if path.is_symlink():
        raise CliError("validation records must not be a symlink")
    if not path.exists():
        return []
    if not path.is_file():
        raise CliError("validation records are not a file")
    raw = read_jsonl_objects(path, upgrade, "validation records")
    return validate_validation_history(raw)


def latest_validation_records(upgrade: Path) -> dict[tuple[str, str | None, str, str], dict[str, Any]]:
    latest: dict[tuple[str, str | None, str, str], dict[str, Any]] = {}
    for record in validation_records(upgrade):
        key = (
            record["check"],
            record.get("subject_digest"),
            record.get("scope") or "",
            record.get("environment") or "",
        )
        latest[key] = record
    return latest


def _evidence_file_descriptors(upgrade: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for dirname, pattern in (("decisions", "DEC-*.json"), ("risks", "RISK-*.jsonl"), ("exceptions", "EXC-*.json")):
        directory = _record_directory(upgrade, dirname, f"{dirname} directory")
        if not directory.exists():
            out.append({"path": f"{dirname}/", "state": "missing"})
            continue
        matches = sorted(directory.glob(pattern), key=lambda item: item.name.casefold())
        if not matches:
            out.append({"path": f"{dirname}/", "state": "empty"})
        for path in matches:
            require_within(path, upgrade, f"{dirname} evidence")
            if path.is_symlink() or not path.is_file():
                raise CliError(f"{dirname} evidence must be a regular file: {path.name}")
            out.append({"path": relative_posix(path, upgrade), "state": "file", "sha256": sha256_file(path), "size": path.stat().st_size})
    for relative, label in (("validation/records.jsonl", "validation records"), ("STATE.md", "workspace state")):
        path = require_within(upgrade / relative, upgrade, label)
        if path.is_symlink():
            raise CliError(f"{label} must not be a symlink")
        if path.exists():
            if not path.is_file():
                raise CliError(f"{label} is not a file")
            out.append({"path": relative, "state": "file", "sha256": sha256_file(path), "size": path.stat().st_size})
        else:
            out.append({"path": relative, "state": "missing"})
    return out


def delivery_evidence_digest(upgrade: Path) -> str:
    encoded = json.dumps(_evidence_file_descriptors(upgrade), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _scope_matches(record_scope: str, requested_scope: str) -> bool:
    if not requested_scope:
        return True
    normalized = (record_scope or "").strip()
    return normalized in {"", "*", "global", requested_scope}


def _meaningful_state_value(value: str | None) -> bool:
    normalized = (value or "").strip().casefold()
    return normalized not in {"", "none", "none.", "not specified", "not specified."}


def _workspace_delivery_state(upgrade: Path, gate: str) -> tuple[dict[str, Any], list[str], list[str]]:
    path = require_within(upgrade / "STATE.md", upgrade, "STATE.md")
    if path.is_symlink() or not path.is_file():
        raise CliError("STATE.md must be a regular file")
    text = read_text_file(path).text
    current_blockers = section_value(text, "current_blockers") or ""
    plan_drift = section_value(text, "plan_drift") or ""
    unverified = section_value(text, "unverified_items") or ""
    blockers: list[str] = []
    warnings: list[str] = []
    for label, value in (("current_blockers", current_blockers), ("unverified_items", unverified)):
        if _meaningful_state_value(value):
            message = f"STATE.md {label} is not cleared"
            (blockers if gate == "final-acceptance" else warnings).append(message)
    if _meaningful_state_value(plan_drift):
        message = "STATE.md plan_drift remains unresolved"
        (blockers if gate == "final-acceptance" else warnings).append(message)
    return {
        "current_blockers": current_blockers.strip(),
        "plan_drift": plan_drift.strip(),
        "unverified_items": unverified.strip(),
    }, blockers, warnings


def delivery_evidence_state(
    root: Path,
    *,
    subject: dict[str, Any],
    required_checks: list[str],
    scope: str = "",
    environment: str = "",
    gate: str = "readiness",
    clock: Callable[[], str] = now_iso,
) -> dict[str, Any]:
    upgrade = verified_upgrade(root)
    at = clock()
    requested_scope = scope or ""
    requested_environment = environment or ""
    decisions = [record for record in active_decisions(upgrade) if _scope_matches(record.get("scope", ""), requested_scope)]
    risks = [record for record in latest_risks(upgrade) if _scope_matches(record.get("scope", ""), requested_scope)]
    exceptions = [record for record in current_exceptions(upgrade) if _scope_matches(record.get("scope", ""), requested_scope)]
    validations = latest_validation_records(upgrade)

    current = current_transaction()
    current_id = current.transaction_id if current else None
    unresolved = [item for item in unresolved_transactions(root) if item["transaction_id"] != current_id]
    blockers: list[str] = []
    warnings: list[str] = []

    subject_digest = subject.get("digest")
    if not subject_digest:
        blockers.append("delivery subject is unbound; use --subject-path/--subject-digest or run from a Git repository root")

    required = list(dict.fromkeys(check.strip() for check in required_checks if check and check.strip()))
    if not required:
        blockers.append("delivery gate requires at least one explicit required validation check")

    if not validations:
        blockers.append("no validation evidence is recorded")

    selected_validation: dict[str, dict[str, Any]] = {}
    for check in required:
        key = (check, subject_digest, requested_scope, requested_environment)
        record = validations.get(key)
        if record is None:
            blockers.append(
                f"required validation {check!r} has no evidence for subject/scope/environment"
            )
            continue
        selected_validation[check] = record
        if record["status"] != "passed":
            blockers.append(f"required validation {check!r} latest status is {record['status']}")

    for (check, digest, record_scope, record_environment), record in sorted(validations.items(), key=lambda item: item[0]):
        if digest != subject_digest or record_scope != requested_scope or record_environment != requested_environment or check in required:
            continue
        if record["status"] != "passed":
            warnings.append(f"non-required validation {check!r} latest status is {record['status']}")

    risk_counts = {status: 0 for status in ("open", "accepted", "mitigated", "closed")}
    for record in risks:
        risk_counts[record["status"]] += 1
        if record.get("schema_version") != RISK_SCHEMA_VERSION:
            message = f"risk {record['risk_id']} uses an earlier unsealed governance schema"
            (blockers if gate == "final-acceptance" else warnings).append(message)
        if record["status"] == "open":
            blockers.append(f"risk {record['risk_id']} remains open")
        elif record["status"] == "accepted":
            warnings.append(f"risk {record['risk_id']} remains accepted by the user")
        elif record["status"] == "mitigated":
            warnings.append(f"risk {record['risk_id']} is mitigated but remains governed evidence")

    for record in decisions:
        if record.get("schema_version") != DECISION_SCHEMA_VERSION:
            message = f"decision {record['decision_id']} uses an earlier unsealed governance schema"
            (blockers if gate == "final-acceptance" else warnings).append(message)

    exception_counts = {status: 0 for status in ("active", "resolved", "revoked")}
    active: list[str] = []
    expired: list[str] = []
    for record in exceptions:
        exception_counts[record["status"]] += 1
        if record.get("schema_version") != EXCEPTION_SCHEMA_VERSION:
            message = f"exception {record['exception_id']} uses an earlier unsealed governance schema"
            (blockers if gate == "final-acceptance" else warnings).append(message)
        if record["status"] != "active":
            continue
        active.append(record["exception_id"])
        if exception_expired(record, at):
            expired.append(record["exception_id"])
            blockers.append(f"exception {record['exception_id']} is expired")
        else:
            warnings.append(f"{record['exception_type']} {record['exception_id']} remains active for delivery scope")

    for transaction in unresolved:
        blockers.append(f"transaction {transaction['transaction_id']} remains unresolved ({transaction['state']})")

    workspace_summary, workspace_blockers, workspace_warnings = _workspace_delivery_state(upgrade, gate)
    blockers.extend(workspace_blockers)
    warnings.extend(workspace_warnings)

    summary = {
        "decisions": {"active_count": len(decisions), "ids": [record["decision_id"] for record in decisions]},
        "risks": {
            "count": len(risks),
            "counts_by_status": risk_counts,
            "outstanding_ids": [record["risk_id"] for record in risks if record["status"] != "closed"],
        },
        "exceptions": {
            "current_count": len(exceptions),
            "counts_by_status": exception_counts,
            "active_ids": active,
            "expired_ids": expired,
        },
        "validation": {
            "required_checks": required,
            "scope": requested_scope,
            "environment": requested_environment,
            "subject_digest": subject_digest,
            "latest_status_by_required_check": {key: value["status"] for key, value in sorted(selected_validation.items())},
        },
        "recovery": {
            "unresolved_count": len(unresolved),
            "transaction_ids": [item["transaction_id"] for item in unresolved],
        },
        "workspace": workspace_summary,
    }
    return {
        "evaluated_at": at,
        "evidence_digest": delivery_evidence_digest(upgrade),
        "summary": summary,
        "blockers": blockers,
        "warnings": warnings,
        "eligible": not blockers,
    }
