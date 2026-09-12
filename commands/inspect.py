"""Read-only progressive disclosure for saved review plans."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from core.agent_view import attention_reasons
from core.audit import Result
from core.errors import CliError
from core.paths import normalize_user_path, relative_posix, require_within
from ._runtime import load_plan


def _plan_path(root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return require_within(path, root, "inspect plan")


def _classification(entry: dict[str, Any]) -> str | None:
    value = entry.get("classification")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("kind", "classification", "type"):
            nested = value.get(key)
            if isinstance(nested, str):
                return nested
    return None


def _status(entry: dict[str, Any]) -> str | None:
    value = entry.get("status")
    if isinstance(value, str):
        return value
    record = entry.get("record")
    if isinstance(record, dict) and isinstance(record.get("status"), str):
        return record["status"]
    return None


def _matches_path(entry: dict[str, Any], requested: set[str]) -> bool:
    if not requested:
        return True
    values: set[str] = set()
    for key in ("path", "source", "target", "destination", "delivery_id", "transaction_id"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            values.add(normalize_user_path(value))
    record = entry.get("record")
    if isinstance(record, dict):
        for key in ("decision_id", "risk_id", "exception_id", "delivery_id"):
            value = record.get(key)
            if isinstance(value, str) and value:
                values.add(normalize_user_path(value))
    return bool(values & requested)


def inspect_plan(root: Path, args: argparse.Namespace, result: Result) -> None:
    path = _plan_path(root, args.plan)
    plan = load_plan(path)
    if plan.get("root") != str(root):
        raise CliError("plan repository root does not match --root")
    entries = plan.get("entries")
    if not isinstance(entries, list):
        raise CliError("plan entries must be a list")

    indexed = [(index, entry) for index, entry in enumerate(entries) if isinstance(entry, dict)]
    action_filter = set(args.action or [])
    classification_filter = set(args.classification or [])
    status_filter = set(args.status or [])
    path_filter = {normalize_user_path(value) for value in (args.path or [])}
    index_filter = set(args.index or [])

    selected: list[tuple[int, dict[str, Any]]] = []
    for index, entry in indexed:
        if args.section == "attention" and not attention_reasons(entry, str(plan.get("kind", ""))):
            continue
        if index_filter and index not in index_filter:
            continue
        if action_filter and str(entry.get("action", "")) not in action_filter:
            continue
        if classification_filter and (_classification(entry) not in classification_filter):
            continue
        if status_filter and (_status(entry) not in status_filter):
            continue
        if not _matches_path(entry, path_filter):
            continue
        selected.append((index, entry))

    total = len(selected)
    if args.offset < 0:
        raise CliError("--offset must be >= 0")
    if args.limit < 1 or args.limit > 200:
        raise CliError("--limit must be between 1 and 200")
    page = selected[args.offset: args.offset + args.limit]

    rendered: list[dict[str, Any]] = []
    if args.section != "summary":
        for index, entry in page:
            item = {"index": index, **entry}
            reasons = attention_reasons(entry, str(plan.get("kind", "")))
            if reasons:
                item["_attention"] = reasons
            rendered.append(item)

    result.plan_id = str(plan.get("plan_id") or "") or None
    result.result = {
        "plan": {
            "path": relative_posix(path, root),
            "kind": plan.get("kind"),
            "plan_id": plan.get("plan_id"),
            "entry_count": len(entries),
        },
        "section": args.section,
        "filters": {
            "action": sorted(action_filter),
            "classification": sorted(classification_filter),
            "status": sorted(status_filter),
            "path": sorted(path_filter),
            "index": sorted(index_filter),
        },
        "total": total,
        "offset": args.offset,
        "limit": args.limit,
        "returned": len(rendered),
        "has_more": args.offset + len(rendered) < total,
        "next_offset": args.offset + len(rendered) if args.offset + len(rendered) < total else None,
        "entries": rendered,
    }
