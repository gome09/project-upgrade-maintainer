"""Validation evidence recording command with subject-bound integrity evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.audit import Result
from core.constants import VALIDATION_SCHEMA_VERSION
from core.errors import CliError
from core.paths import require_within, verified_upgrade
from core.schemas import governance_record_digest, validate_validation_history, validate_validation_record
from core.subjects import resolve_subject
from core.workspace import TextFile, activate_workspace_modules, preflight_mutation_workspace, read_text_file, replace_section
from ._governance_records import read_jsonl_objects
from ._runtime import atomic_write, finalize_mutation, now_iso


def validation_record(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root, require_config=True)
    if args.status == "passed" and args.exit_code not in {None, 0}:
        raise CliError("passed validation cannot have a non-zero exit code")
    if args.status == "failed" and args.exit_code == 0:
        raise CliError("failed validation cannot have exit code 0")

    subject = resolve_subject(
        root,
        subject_path=args.subject_path,
        subject_digest=args.subject_digest,
        subject_ref=args.subject_ref,
        fallback_ref="workspace",
    )
    target = require_within(upgrade / "validation" / "records.jsonl", upgrade, "validation record")
    existing_raw = read_jsonl_objects(target, upgrade, "validation records") if target.exists() else []
    validate_validation_history(existing_raw)
    previous_digest = governance_record_digest(existing_raw[-1]) if existing_raw else None

    record = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "timestamp": now_iso(),
        "check": args.check,
        "status": args.status,
        "exit_code": args.exit_code,
        "environment": args.environment or "",
        "scope": args.scope or "",
        "note": args.note or "",
        "subject_type": subject["type"],
        "subject_ref": subject["ref"],
        "subject_digest": subject["digest"],
        "previous_record_digest": previous_digest,
    }
    record["record_digest"] = governance_record_digest(record)
    record = validate_validation_record(record)
    validate_validation_history([*existing_raw, record])

    activate_workspace_modules(root, result, {"validation"}, dry_run=args.dry_run, clock=now_iso, writer=atomic_write)
    upgrade = verified_upgrade(root)
    target = require_within(upgrade / "validation" / "records.jsonl", upgrade, "validation record")
    source = read_text_file(target) if target.exists() else TextFile("", False, "\n")
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    updated = source.text + ("" if not source.text or source.text.endswith(("\n", "\r")) else source.newline) + line + source.newline
    if atomic_write(target, updated, source=source, dry_run=args.dry_run):
        result.change(".upgrade/validation/records.jsonl", "would_append" if args.dry_run else "appended")

    state_path = require_within(upgrade / "STATE.md", upgrade, "STATE.md")
    state_source = read_text_file(state_path)
    summary = (
        f"- Status: {args.status}\n"
        f"- Check: `{args.check}`\n"
        f"- Environment: {args.environment or 'unspecified'}\n"
        f"- Scope: {args.scope or 'unspecified'}\n"
        f"- Subject: {subject['type']} `{subject['ref']}`\n"
        f"- Subject digest: `{subject['digest'] or 'unbound'}`\n"
        f"- Recorded: {record['timestamp']}"
    )
    state_text = replace_section(state_source.text, "last_validation", summary)
    if args.status == "unverified":
        state_text = replace_section(state_text, "unverified_items", args.note or args.check)
    if atomic_write(state_path, state_text, source=state_source, dry_run=args.dry_run):
        result.change(".upgrade/STATE.md", "would_update" if args.dry_run else "updated")
    result.result = {"record": record, "subject": subject}
    if subject["type"] == "unbound":
        result.warnings.append("validation evidence is unbound because no explicit subject was provided and the repository root is not a readable Git worktree")
    if not args.dry_run:
        finalize_mutation(root, result)
