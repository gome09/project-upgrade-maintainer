"""Git policy review/apply commands."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
from core.constants import *
from core.audit import Result
from core.errors import CliError, StalePlanError
from core.paths import normalize_focuses, relative_posix, require_within
from core.plans import assert_file_snapshot_fresh, validate_apply_plan, write_plan_output
from core.workspace import TextFile, preflight_mutation_workspace, read_text_file, sha256_file, update_marker, validate_marker_pair
from ._runtime import atomic_write, build_review_plan, load_plan, finalize_mutation
from ._common import parse_assignments, summarize_actions
from ._inventory import *

def git_policy_review(root: Path, args: argparse.Namespace, result: Result) -> None:
    decisions = parse_assignments(args.decision, {"add-ignore", "keep"})
    entries, snapshot = audit_entries(root, args.focus)
    if not snapshot["available"]:
        raise CliError("git-policy-review requires a Git work tree")
    plan_entries: list[dict[str, Any]] = []
    reserved_rules: set[str] = set()
    for entry in entries:
        relative = entry["path"]
        explicit = decisions.get(relative)
        suggested = entry.get("ignore_suggestion")
        actual = entry["actual_git_state"]
        expected = entry["expected_git_state"]
        if explicit == "keep":
            action, reason = "keep", "exact user decision"
        elif explicit == "add-ignore":
            if not suggested:
                raise CliError(f"path has no safe ignore suggestion: {relative}")
            action, reason = "add-ignore", "exact user decision"
        elif expected == "ignored" and actual == "untracked" and suggested:
            action, reason = "add-ignore", "untracked generated, temporary, or sensitive artifact"
        elif expected == "ignored" and actual == "tracked":
            action, reason = "confirm", "tracked artifact cannot be safely untracked automatically"
        elif expected == "tracked" and actual == "ignored":
            action, reason = "confirm", "permanent artifact is ignored"
        else:
            continue
        rule = suggested if action == "add-ignore" else None
        if rule and rule in reserved_rules:
            continue
        if rule:
            reserved_rules.add(rule)
        plan_entry = {
            **entry,
            "action": action,
            "reason": reason,
            "ignore_rule": rule,
            "confirmation_required": action == "confirm",
        }
        if action == "add-ignore":
            source = require_within(root / relative, root, "git policy source")
            stat = source.stat()
            plan_entry.update({"sha256": sha256_file(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
        plan_entries.append(plan_entry)
    plan = build_review_plan(root, "git-policy", plan_entries)
    result.plan_id = plan["plan_id"]
    result.result = {"plan": plan, "summary": summarize_actions(plan_entries), "repo_root": snapshot["repo_root"]}
    write_plan_output(root, args.plan_out, plan, result)


def git_policy_apply(root: Path, args: argparse.Namespace, result: Result) -> None:
    preflight_mutation_workspace(root, require_config=True)
    plan = load_plan(Path(args.plan).resolve())
    entries = validate_apply_plan(root, plan, args.approve, "git-policy")
    executable = [entry for entry in entries if entry.get("action") == "add-ignore"]
    snapshot = git_snapshot(root, [normalize_user_path(str(entry.get("path", ""))) for entry in executable])
    if not snapshot["available"]:
        raise CliError("git-policy-apply requires a Git work tree")
    rules: list[str] = []
    for entry in executable:
        relative = normalize_user_path(str(entry.get("path", "")))
        source = require_within(root / relative, root, "git policy source")
        assert_file_snapshot_fresh(
            source, entry, message=f"git policy source changed or disappeared: {relative}"
        )
        if actual_git_state(relative, snapshot) != entry.get("actual_git_state"):
            raise StalePlanError(f"git state changed since review: {relative}")
        rule = str(entry.get("ignore_rule") or "")
        if not rule:
            raise CliError(f"git policy entry lacks ignore rule: {relative}")
        rules.append(rule)
    if not rules:
        for entry in entries:
            result.skip(entry.get("path", "?"), f"non-executable action: {entry.get('action')}")
        return
    target = root / ".gitignore"
    if target.is_symlink():
        raise CliError("managed .gitignore must not be a symlink")
    require_within(target, root, ".gitignore")
    if target.exists() and not target.is_file():
        raise CliError("managed .gitignore target is not a file")
    source = read_text_file(target) if target.exists() else TextFile("", False, os.linesep)
    validate_marker_pair(source.text, GIT_POLICY_START, GIT_POLICY_END)
    existing_rules: set[str] = set()
    if GIT_POLICY_START in source.text and GIT_POLICY_END in source.text:
        body = source.text.split(GIT_POLICY_START, 1)[1].split(GIT_POLICY_END, 1)[0]
        for line in body.splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                existing_rules.add(value)
    unique_rules = sorted(existing_rules | set(rules), key=str.casefold)
    block = GIT_POLICY_START + "\n# Repository lifecycle rules approved through project-upgrade-maintainer\n" + "\n".join(unique_rules) + "\n" + GIT_POLICY_END
    updated = update_marker(source.text, GIT_POLICY_START, GIT_POLICY_END, block)
    if atomic_write(target, updated, source=source, dry_run=args.dry_run):
        result.change(".gitignore", "would_update" if args.dry_run else "updated", rules=unique_rules)
    for entry in entries:
        if entry.get("action") != "add-ignore":
            result.skip(entry.get("path", "?"), f"non-executable action: {entry.get('action')}")
    if not args.dry_run:
        finalize_mutation(root, result)
