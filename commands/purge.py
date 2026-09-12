"""Trash purge review/apply commands."""
from __future__ import annotations
import argparse
import shutil
from pathlib import Path
from core.constants import *
from core.audit import Result
from core.errors import CliError
from core.paths import normalize_user_path, relative_posix, require_within, verified_upgrade
from core.plans import assert_file_snapshot_fresh, validate_apply_plan, write_plan_output
from core.transactions import transaction_unlink
from core.workspace import preflight_mutation_workspace, sha256_file
from ._runtime import build_review_plan, load_plan, finalize_mutation
from ._common import scan_files, summarize_actions

def purge_review(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = verified_upgrade(root)
    trash = require_within(upgrade / "tmp" / ".trash", upgrade, "trash directory")
    entries: list[dict[str, Any]] = []
    if trash.is_dir():
        for path in scan_files(trash):
            if path.name == ".gitkeep":
                continue
            stat = path.stat()
            entries.append({
                "source": relative_posix(path, root),
                "action": "purge",
                "classification": "permanent-delete",
                "reason": "previous cleanup moved this artifact to recoverable trash",
                "sha256": sha256_file(path),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "confirmation_required": True,
            })
    plan = build_review_plan(root, "purge", entries)
    result.plan_id = plan["plan_id"]
    result.result = {"plan": plan, "summary": summarize_actions(entries)}
    write_plan_output(root, args.plan_out, plan, result)


def purge_apply(root: Path, args: argparse.Namespace, result: Result) -> None:
    preflight_mutation_workspace(root, require_config=True)
    plan = load_plan(Path(args.plan).resolve())
    entries = validate_apply_plan(root, plan, args.approve, "purge")
    upgrade = verified_upgrade(root)
    trash = require_within(upgrade / "tmp" / ".trash", upgrade, "trash directory")
    validated: list[tuple[dict[str, Any], Path]] = []
    for entry in entries:
        if entry.get("action") != "purge":
            raise CliError("purge plan contains a non-purge action")
        source = require_within(root / normalize_user_path(str(entry.get("source", ""))), trash, "purge source")
        assert_file_snapshot_fresh(
            source, entry, message=f"purge source changed or disappeared: {entry.get('source')}"
        )
        validated.append((entry, source))
    runtime_failed = False
    for entry, source in validated:
        if args.dry_run:
            result.change(entry["source"], "would_purge")
            continue
        try:
            transaction_unlink(source)
            result.change(entry["source"], "purged")
        except OSError as exc:
            runtime_failed = True
            result.errors.append(f"{entry['source']}: {exc}")
            break
    if runtime_failed:
        result.status = "partial"
    if not args.dry_run:
        finalize_mutation(root, result)
