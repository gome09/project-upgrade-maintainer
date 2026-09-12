"""Phase transition command."""
from __future__ import annotations
import argparse
from pathlib import Path
from core.constants import *
from core.audit import Result
from core.errors import CliError
from core.paths import relative_posix, require_within, verified_upgrade
from core.config import load_workspace_config
from core.workspace import TextFile, activate_workspace_modules, preflight_mutation_workspace, read_text_file, replace_section
from ._runtime import atomic_write, now_iso, finalize_mutation
from ._governance import cleanup_thresholds, current_phase, phase_transition_checkpoint

def phase_update(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root, require_config=True)
    state_path = upgrade / "STATE.md"
    require_within(state_path, upgrade, "STATE.md")
    if not state_path.is_file():
        raise CliError("STATE.md does not exist; run initialize first")
    if args.phase < 0:
        raise CliError("phase must be zero or greater")
    dry_run = args.dry_run
    source = read_text_file(state_path)
    previous_phase = current_phase(upgrade)
    if args.complete_previous and args.phase <= previous_phase:
        raise CliError("--complete-previous is valid only when advancing to a later phase")
    if args.complete_previous and not (args.completion_note or "").strip():
        raise CliError("--complete-previous requires --completion-note")
    if args.phase > previous_phase + 1:
        if not args.allow_skip:
            raise CliError(
                f"phase transition {previous_phase} -> {args.phase} skips phases; use --allow-skip with --transition-reason"
            )
        if not (args.transition_reason or "").strip():
            raise CliError("--allow-skip requires --transition-reason")
    if args.phase < previous_phase:
        if not args.allow_regression:
            raise CliError(
                f"phase transition {previous_phase} -> {args.phase} is a regression; use --allow-regression with --transition-reason"
            )
        if not (args.transition_reason or "").strip():
            raise CliError("--allow-regression requires --transition-reason")
    if args.phase > previous_phase and previous_phase > 0 and not args.complete_previous:
        raise CliError(
            f"phase {previous_phase} must be explicitly completed before advancing; use --complete-previous with --completion-note"
        )

    transition_kind = "refresh"
    if args.phase > previous_phase + 1:
        transition_kind = "skip"
    elif args.phase > previous_phase:
        transition_kind = "advance"
    elif args.phase < previous_phase:
        transition_kind = "regression"
    if args.transition_reason:
        transition_reason = args.transition_reason.strip()
    elif transition_kind == "advance":
        transition_reason = "normal phase advance"
    else:
        transition_reason = "phase refresh"

    config = load_workspace_config(upgrade)
    if previous_phase > 0 and "phase" not in config["active_modules"]:
        raise CliError("STATE.md has an active phase but the phase module is disabled; run sync --enable-module phase before continuing")
    activate_workspace_modules(root, result, {"phase"}, dry_run=dry_run, clock=now_iso, writer=atomic_write)
    upgrade = verified_upgrade(root)
    stage = require_within(upgrade / "stages" / f"phase-{args.phase}.md", upgrade, "phase file")
    checkpoint = require_within(upgrade / "checkpoints" / f"phase-{args.phase}.entered", upgrade, "checkpoint")
    previous_stage: Path | None = None
    previous_stage_source: TextFile | None = None
    previous_stage_content: str | None = None
    if previous_phase > 0 and args.phase != previous_phase:
        previous_stage = require_within(upgrade / "stages" / f"phase-{previous_phase}.md", upgrade, "previous phase file")
        if not previous_stage.is_file():
            raise CliError(f"current phase artifact is missing for phase {previous_phase}; repair it before transitioning")
        previous_stage_source = read_text_file(previous_stage)
        previous_stage_content = previous_stage_source.text
        if transition_kind in {"advance", "skip"}:
            previous_stage_content = replace_section(previous_stage_content, "Status", "completed")
            previous_stage_content = replace_section(previous_stage_content, "Completion Decision", "User-confirmed through --complete-previous")
            previous_stage_content = replace_section(previous_stage_content, "Completion Note", args.completion_note.strip())
            previous_stage_content = replace_section(previous_stage_content, "Completed At", now_iso())
        elif transition_kind == "regression":
            previous_stage_content = replace_section(previous_stage_content, "Status", "regressed")
            previous_stage_content = replace_section(previous_stage_content, "Regression Note", transition_reason)
            previous_stage_content = replace_section(previous_stage_content, "Regressed At", now_iso())
    threshold = cleanup_thresholds(upgrade, args.phase)
    text = source.text
    text = replace_section(text, "current_phase", f"Phase {args.phase}: In progress")
    text = replace_section(text, "current_task", args.current_task)
    if args.completed_task is not None:
        text = replace_section(text, "last_completed_task", args.completed_task)
    if args.next_action is not None:
        text = replace_section(text, "next_action", args.next_action)
    if args.plan_drift is not None:
        text = replace_section(text, "plan_drift", args.plan_drift)
    if args.blocker is not None:
        text = replace_section(text, "current_blockers", args.blocker)
    if args.unverified is not None:
        text = replace_section(text, "unverified_items", args.unverified)
    before_last_update = text
    if before_last_update != source.text:
        text = replace_section(text, "last_update", f"- Date: {now_iso()}\n- Updated by: project-upgrade-maintainer\n- Summary: Entered or refreshed Phase {args.phase}.")
    if previous_stage is not None and previous_stage_source is not None and previous_stage_content is not None:
        if atomic_write(previous_stage, previous_stage_content, source=previous_stage_source, dry_run=dry_run):
            result.change(
                f".upgrade/stages/phase-{previous_phase}.md",
                "would_update" if dry_run else "updated",
            )
    if atomic_write(state_path, text, source=source, dry_run=dry_run):
        result.change(".upgrade/STATE.md", "would_update" if dry_run else "updated")
    stage_source = read_text_file(stage) if stage.exists() else None
    if stage_source:
        stage_content = replace_section(stage_source.text, "Status", "in_progress")
        stage_content = replace_section(stage_content, "Current Task", args.current_task)
        stage_content = replace_section(stage_content, "Next Action", args.next_action or "Not specified.")
        if args.plan_drift is not None:
            stage_content = replace_section(stage_content, "Plan Drift", args.plan_drift)
    else:
        stage_content = f"# Phase {args.phase}\n\n## Status\n\nin_progress\n\n## Current Task\n\n{args.current_task}\n\n## Next Action\n\n{args.next_action or 'Not specified.'}\n\n## Plan Drift\n\n{args.plan_drift or 'None.'}\n"
    if atomic_write(stage, stage_content, source=stage_source, dry_run=dry_run):
        action = "would_update" if dry_run and stage.exists() else "would_create" if dry_run else "updated" if stage.exists() else "created"
        result.change(f".upgrade/stages/phase-{args.phase}.md", action)
    if not checkpoint.exists():
        atomic_write(checkpoint, f"entered_at: {now_iso()}\n", dry_run=dry_run)
        result.change(f".upgrade/checkpoints/phase-{args.phase}.entered", "would_create" if dry_run else "created")
    if transition_kind != "refresh":
        transition_checkpoint, transitioned_at = phase_transition_checkpoint(
            upgrade,
            previous_phase,
            args.phase,
            transition_kind,
            transition_reason,
            dry_run=dry_run,
        )
        result.change(relative_posix(transition_checkpoint, root), "would_create" if dry_run else "created")
        result.result["transition"] = {
            "from_phase": previous_phase,
            "to_phase": args.phase,
            "kind": transition_kind,
            "reason": transition_reason,
            "timestamp": transitioned_at,
        }
    result.result["cleanup"] = threshold
    if threshold["suggested"]:
        result.warnings.append("cleanup thresholds exceeded; ask whether to generate a cleanup-review plan")
    if not dry_run:
        finalize_mutation(root, result)
