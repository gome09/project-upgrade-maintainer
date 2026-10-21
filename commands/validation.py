"""Validation evidence recording command with subject-bound integrity evidence."""
from __future__ import annotations

import argparse
import json
import hashlib
import subprocess
import tempfile
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

    execute = getattr(args, "execute", None)
    execution = None
    if execute is not None:
        if not execute or args.status is not None or args.exit_code is not None:
            raise CliError("--execute requires argv and cannot be combined with --status/--exit-code")
        if args.dry_run:
            raise CliError("--execute cannot be used with --dry-run")
        if args.timeout_seconds <= 0:
            raise CliError("timeout-seconds must be positive")
        request_id = getattr(args, "request_id", None)
        for existing in existing_raw:
            if request_id is None or existing.get("request_id") != request_id:
                continue
            expected = {"check": args.check, "scope": args.scope or "", "environment": args.environment or "",
                        "note": args.note or "", "subject_type": subject["type"], "subject_ref": subject["ref"],
                        "subject_digest": subject["digest"]}
            if any(existing.get(key) != value for key, value in expected.items()) or existing.get("execution", {}).get("argv") != execute:
                raise CliError("validation request_id already exists with different evidence")
            result.result = {"record": existing, "subject": subject, "replayed": True}
            return
        started_at = now_iso()
        timed_out = False
        # Spool output to private files instead of retaining unbounded output
        # or persisting potentially sensitive command output in the workspace.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                completed = subprocess.run(execute, cwd=root, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, timeout=args.timeout_seconds,
                                           shell=False, check=False)
                exit_code = completed.returncode
            except subprocess.TimeoutExpired:
                timed_out, exit_code = True, None
            except OSError as exc:
                raise CliError(f"cannot execute validation command: {exc}") from exc
            def output_digest(stream):
                stream.seek(0)
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                return digest.hexdigest()
            execution = {"argv": execute, "started_at": started_at, "finished_at": now_iso(),
                         "stdout_sha256": output_digest(stdout), "stderr_sha256": output_digest(stderr),
                         "timed_out": timed_out,
                         "subject_unchanged": resolve_subject(root, subject_path=args.subject_path,
                            subject_digest=args.subject_digest, subject_ref=args.subject_ref,
                            fallback_ref="workspace") == subject}
        args.exit_code = exit_code
        args.status = "passed" if exit_code == 0 and not timed_out and execution["subject_unchanged"] else "failed"
    elif args.status is None:
        raise CliError("validation requires --status for manual evidence or --execute for local execution")

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
    if execution is not None:
        record["execution"] = execution
    request_id = getattr(args, "request_id", None)
    if request_id is not None:
        record["request_id"] = request_id
        for existing in existing_raw:
            if existing.get("request_id") != request_id:
                continue
            semantic_fields = (set(record) | set(existing)) - {"timestamp", "previous_record_digest", "record_digest"}
            if any(existing.get(key) != record.get(key) for key in semantic_fields):
                raise CliError("validation request_id already exists with different evidence")
            result.result = {"record": existing, "subject": subject, "replayed": True}
            return
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
