"""Token-efficient semantic projection for agent-facing CLI output.

The full Result/Plan remains the execution evidence.  This module derives a
compact decision surface that preserves safety-significant facts while
collapsing routine bulk data into counts.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .audit import Result
from .constants import SCHEMA_VERSION
from .paths import relative_posix

_SAFE_STATUSES = {"ok", "current", "healthy", "passed", "resolved", "revoked", "closed"}
_NON_EXECUTABLE_ACTIONS = {"keep", "ignore", "confirm", "forbidden"}


def _counts(items: Iterable[dict[str, Any]], field: str, default: str) -> dict[str, int]:
    counts = Counter(str(item.get(field, default)) for item in items)
    return dict(sorted(counts.items()))


def _plan_path(result: Result) -> str | None:
    for item in result.changed:
        if item.get("action") == "created_plan" and item.get("path"):
            return str(item["path"])
    return None


def _subject(entry: dict[str, Any]) -> tuple[str, str] | tuple[None, None]:
    for key in ("path", "source", "target", "delivery_id", "transaction_id"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return key, value
    record = entry.get("record")
    if isinstance(record, dict):
        for key in ("decision_id", "risk_id", "exception_id", "delivery_id"):
            value = record.get(key)
            if isinstance(value, str) and value:
                return key, value
    return None, None


def attention_reasons(entry: dict[str, Any], kind: str | None = None) -> list[dict[str, str]]:
    """Return semantic reasons that make a plan entry part of the decision surface."""
    reasons: list[dict[str, str]] = []

    def add(code: str, severity: str = "decision") -> None:
        if not any(item["code"] == code for item in reasons):
            reasons.append({"code": code, "severity": severity})

    action = str(entry.get("action", ""))
    classification = entry.get("classification")

    blockers = entry.get("blockers")
    if isinstance(blockers, list) and blockers:
        add("blocker", "blocker")
    conflicts = entry.get("conflicts")
    if isinstance(conflicts, list) and conflicts:
        add("recovery-conflict", "blocker")
    nested_warnings = entry.get("warnings")
    if isinstance(nested_warnings, list) and nested_warnings:
        add("entry-warning", "warning")

    if action == "forbidden":
        add("forbidden-action", "blocker")
    elif action == "confirm":
        add("confirmation-required", "blocker")
    elif entry.get("confirmation_required"):
        add("confirmation-required")

    if entry.get("protected"):
        add("protected-path", "blocker" if not entry.get("protected_authorized") else "decision")
    if entry.get("sensitive"):
        add("sensitive-material", "blocker" if not entry.get("sensitive_authorized") else "decision")

    if isinstance(classification, dict) and classification.get("ambiguous"):
        add("ambiguous-classification", "blocker")
    if classification == "sensitive":
        add("sensitive-material", "warning")

    if kind == "purge" or action == "purge" or classification == "permanent-delete":
        add("permanent-delete")
    if kind == "cleanup" and action == "delete":
        add("recoverable-delete")
    if kind == "cleanup" and action in {"update", "merge"}:
        add("content-replacement")
    if kind == "git-policy" and action == "add-ignore":
        add("git-ignore-change")
    if kind == "authority" and action in {"register", "rebind", "unregister"}:
        add("authority-change")
    if kind == "artifact-policy" and action not in {"keep", "confirm"}:
        add("artifact-policy-change")
    if kind in {"decision", "risk", "exception", "delivery", "delivery-policy", "recovery"} and action not in _NON_EXECUTABLE_ACTIONS:
        add("governance-decision")

    return reasons


def _compact_attention_entry(
    entry: dict[str, Any],
    *,
    index: int,
    kind: str | None,
    detail_available: bool,
) -> dict[str, Any]:
    reasons = attention_reasons(entry, kind)
    key, value = _subject(entry)
    item: dict[str, Any] = {
        "index": index,
        "codes": [reason["code"] for reason in reasons],
        "severity": "blocker" if any(reason["severity"] == "blocker" for reason in reasons) else "warning" if any(reason["severity"] == "warning" for reason in reasons) else "decision",
    }
    if entry.get("action"):
        item["action"] = entry["action"]
    if key and value:
        item[key] = value
    if entry.get("reason"):
        item["reason"] = entry["reason"]
    for flag in ("protected", "protected_authorized", "sensitive", "sensitive_authorized", "recoverable", "eligible"):
        if flag in entry:
            item[flag] = entry[flag]
    for field in ("blockers", "conflicts", "warnings"):
        value = entry.get(field)
        if isinstance(value, list) and value:
            item[field] = value
    item["detail_ref"] = f"entry:{index}" if detail_available else "full-view"
    return item


def _abnormal_result_attention(value: Any, *, path: str = "result") -> list[dict[str, Any]]:
    """Extract compact abnormal findings before bulk result lists are collapsed."""
    attention: list[dict[str, Any]] = []
    if isinstance(value, dict):
        status = value.get("status")
        code: str | None = None
        severity = "warning"
        if isinstance(status, str) and status.casefold() not in _SAFE_STATUSES:
            if status.casefold() in {"unsafe", "missing", "drifted", "failed", "error"}:
                code, severity = f"status-{status.casefold()}", "blocker"
            elif status.casefold() in {"warning", "unverified"}:
                code = f"status-{status.casefold()}"
        if value.get("git_mismatch") is True:
            code = "git-lifecycle-mismatch"
        if value.get("expired") is True:
            code = "expired-governance-record"
        if value.get("eligible") is False:
            code, severity = "ineligible", "blocker"
        if value.get("recoverable") is False:
            code, severity = "not-recoverable", "blocker"
        blockers = value.get("blockers")
        conflicts = value.get("conflicts")
        if isinstance(blockers, list) and blockers:
            code, severity = "blocker", "blocker"
        if isinstance(conflicts, list) and conflicts:
            code, severity = "conflict", "blocker"
        if code:
            key, subject = _subject(value)
            item: dict[str, Any] = {"code": code, "severity": severity, "source": path, "detail_ref": "full-view"}
            if key and subject:
                item[key] = subject
            if isinstance(status, str):
                item["status"] = status
            if isinstance(blockers, list) and blockers:
                item["blockers"] = blockers
            if isinstance(conflicts, list) and conflicts:
                item["conflicts"] = conflicts
            attention.append(item)
        for key, nested in value.items():
            if key == "plan":
                continue
            attention.extend(_abnormal_result_attention(nested, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            attention.extend(_abnormal_result_attention(nested, path=f"{path}[{index}]"))
    return attention


def _compact_value(value: Any, *, key: str = "") -> Any:
    """Compact routine bulk values without hiding known warning/error surfaces."""
    if isinstance(value, dict):
        return {name: _compact_value(nested, key=name) for name, nested in value.items() if name != "plan"}
    if isinstance(value, list):
        critical = key.casefold() in {"warnings", "errors", "blockers", "conflicts", "警告项", "错误项", "建议动作"}
        if critical or (all(not isinstance(item, (dict, list)) for item in value) and len(value) <= 12):
            return value
        if not value:
            return []
        summary: dict[str, Any] = {"count": len(value), "detail_ref": "full-view"}
        dict_items = [item for item in value if isinstance(item, dict)]
        if len(dict_items) == len(value):
            actions = _counts(dict_items, "action", "unknown")
            statuses = _counts(dict_items, "status", "unknown")
            classifications = _counts(dict_items, "classification", "unknown")
            if set(actions) != {"unknown"}:
                summary["by_action"] = actions
            if set(statuses) != {"unknown"}:
                summary["by_status"] = statuses
            # Only include scalar classification counts; dict classifications are noisy.
            if all(not isinstance(item.get("classification"), dict) for item in dict_items) and set(classifications) != {"unknown"}:
                summary["by_classification"] = classifications
        return summary
    return value


def _change_summary(items: list[dict[str, Any]], field: str) -> dict[str, Any]:
    if not items:
        return {"count": 0}
    return {"count": len(items), f"by_{field}": _counts(items, field, "unknown")}


def _plan_projection(result: Result, plan: dict[str, Any], path: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    entries = plan.get("entries") if isinstance(plan.get("entries"), list) else []
    kind = str(plan.get("kind", "")) or None
    attention: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, dict) and attention_reasons(entry, kind):
            attention.append(_compact_attention_entry(entry, index=index, kind=kind, detail_available=bool(path)))
    projection = {
        "kind": kind,
        "plan_id": plan.get("plan_id") or result.plan_id,
        "entry_count": len(entries),
        "action_counts": _counts([entry for entry in entries if isinstance(entry, dict)], "action", "unknown"),
        "path": path,
        "persisted": bool(path),
    }
    return projection, attention


def agent_payload(result: Result) -> dict[str, Any]:
    """Return the compact agent-facing projection of a completed Result."""
    full = result.as_dict()
    if result.command == "inspect":
        return {
            "schema_version": SCHEMA_VERSION,
            "view": "agent",
            "command": result.command,
            "status": result.status,
            "root": str(result.root),
            "warnings": list(result.warnings),
            "errors": list(result.errors),
            "result": result.result,
        }

    raw_result = result.result if isinstance(result.result, dict) else {}
    plan = raw_result.get("plan") if isinstance(raw_result.get("plan"), dict) else None
    path = _plan_path(result)
    plan_projection: dict[str, Any] | None = None
    plan_attention: list[dict[str, Any]] = []
    if plan is not None:
        plan_projection, plan_attention = _plan_projection(result, plan, path)

    result_attention = _abnormal_result_attention({key: value for key, value in raw_result.items() if key != "plan"})
    # De-duplicate exact recursive findings without disturbing deterministic order.
    seen: set[str] = set()
    unique_result_attention: list[dict[str, Any]] = []
    for item in result_attention:
        marker = repr(sorted(item.items(), key=lambda pair: pair[0]))
        if marker not in seen:
            seen.add(marker)
            unique_result_attention.append(item)
    attention = plan_attention + unique_result_attention

    blockers = sum(1 for item in attention if item.get("severity") == "blocker") + len(result.errors)
    executable = 0
    if plan is not None:
        entries = plan.get("entries") if isinstance(plan.get("entries"), list) else []
        executable = sum(1 for entry in entries if isinstance(entry, dict) and str(entry.get("action", "")) not in _NON_EXECUTABLE_ACTIONS)

    if result.errors:
        next_action = "resolve-errors"
    elif result.status == "partial":
        next_action = "inspect-partial-execution"
    elif blockers:
        next_action = "resolve-blockers-and-regenerate-plan" if plan is not None else "inspect-blockers"
    elif plan is not None and not path:
        next_action = "persist-plan-before-apply" if executable else "no-apply-actions"
    elif plan_attention:
        next_action = "review-attention-before-approval"
    elif plan is not None and executable:
        next_action = "review-summary-before-approval"
    elif result.warnings:
        next_action = "review-warnings"
    else:
        next_action = "complete"

    context = _compact_value({key: value for key, value in raw_result.items() if key != "plan"})
    payload: dict[str, Any] = {
        "schema_version": full["schema_version"],
        "view": "agent",
        "command": result.command,
        "status": result.status,
        "root": str(result.root),
        "decision": {
            "required": bool(executable or plan_attention),
            "blocker_count": blockers,
            "warning_count": len(result.warnings) + sum(1 for item in attention if item.get("severity") == "warning"),
            "approval_scope": "exact-plan-id" if executable else None,
        },
        "changes": _change_summary(result.changed, "action"),
        "skipped": _change_summary(result.skipped, "reason"),
        "warnings": list(result.warnings),
        "errors": list(result.errors),
        "attention_summary": dict(sorted(Counter(code for item in attention for code in item.get("codes", [item.get("code")]) if code).items())),
        "attention": attention,
        "context": context,
        "next": {"action": next_action},
    }
    if plan_projection is not None:
        payload["plan"] = plan_projection
    return payload
