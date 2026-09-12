"""Deterministic maintenance CLI for the project-upgrade-maintainer skill."""
from __future__ import annotations

import argparse

from core.constants import ARTIFACT_DOCKER_POLICIES, ARTIFACT_GIT_POLICIES, ARTIFACT_KINDS, ARTIFACT_LIFECYCLES, MODULE_ORDER, RISK_STATUSES, RISK_LEVELS, EXCEPTION_TYPES, EXCEPTION_STATUSES, DELIVERY_GATES

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--view", choices=("full", "agent"), default="full", help="output projection: full evidence or token-efficient agent decision surface")
    parser.add_argument("--output", help="write rendered output to a new repository-local path; relative paths use --root")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("version", help="report skill, policy, and plan schema versions without mutation")

    inspect_parser = commands.add_parser("inspect", help="read a saved review plan with bounded progressive disclosure")
    inspect_parser.add_argument("--plan", required=True, help="repository-local saved plan path")
    inspect_parser.add_argument("--section", choices=("summary", "attention", "entries"), default="attention")
    inspect_parser.add_argument("--action", action="append", help="filter entries by exact action")
    inspect_parser.add_argument("--class", dest="classification", action="append", help="filter entries by scalar classification")
    inspect_parser.add_argument("--status", action="append", help="filter entries by status or record status")
    inspect_parser.add_argument("--path", action="append", help="filter entries by exact path/source/target/id")
    inspect_parser.add_argument("--index", action="append", type=int, help="filter entries by zero-based plan entry index")
    inspect_parser.add_argument("--offset", type=int, default=0)
    inspect_parser.add_argument("--limit", type=int, default=20)
    commands.add_parser("bootstrap-review", help="read-only repository profiling and adaptive initialization recommendation")

    initialize_parser = commands.add_parser("initialize")
    initialize_parser.add_argument(
        "--profile", choices=("lean", "standard", "governed", "custom", "default"),
        help="initial workspace footprint; omitted uses the safe Lean footprint, while 'default' is a supported alias for the full Governed workspace profile",
    )
    initialize_parser.add_argument(
        "--module", action="append", choices=MODULE_ORDER,
        help="module to enable with --profile custom; repeat as needed (core is always enabled)",
    )
    initialize_parser.add_argument("--dry-run", action="store_true")
    initialize_parser.add_argument(
        "--create-root-bridge", action="store_true",
        help="create a minimal root AGENTS.md bridge only when no supported root Agent instruction file exists",
    )
    initialize_parser.add_argument(
        "--sync-nested-bridges", action="store_true",
        help="add/update managed governance bridges in existing nested Agent instruction files",
    )

    sync_parser = commands.add_parser("sync")
    sync_parser.add_argument("--dry-run", action="store_true")
    sync_parser.add_argument(
        "--enable-module", action="append", choices=MODULE_ORDER,
        help="materialize and enable an additional workspace module without removing existing modules",
    )
    sync_parser.add_argument(
        "--create-root-bridge", action="store_true",
        help="create a minimal root AGENTS.md bridge only when no supported root Agent instruction file exists",
    )
    sync_parser.add_argument(
        "--sync-nested-bridges", action="store_true",
        help="add/update managed governance bridges in existing nested Agent instruction files",
    )

    authority_review = commands.add_parser(
        "authority-review",
        help="discover project authority candidates and review explicit register/rebind/unregister decisions",
    )
    authority_review.add_argument("--accept", action="append", help="repository-relative project source to register or explicitly rebind; repeat as needed")
    authority_review.add_argument("--remove", action="append", help="registered project source to unregister without modifying the source file")
    authority_review.add_argument("--ignore", action="append", help="candidate to leave unregistered in this review; ignore is not persisted")
    authority_review.add_argument("--kind", action="append", help="PATH=KIND override for an accepted source")
    authority_review.add_argument("--scope", action="append", help="PATH=SCOPE override for an accepted source")
    authority_review.add_argument("--authority", action="append", help="PATH=repository|scoped|reference override for an accepted source")
    authority_review.add_argument("--required", action="append", help="PATH=yes|no override for an accepted source")
    authority_review.add_argument("--plan-out")

    authority_apply = commands.add_parser("authority-apply")
    authority_apply.add_argument("--plan", required=True)
    authority_apply.add_argument("--approve", required=True)
    authority_apply.add_argument("--dry-run", action="store_true")

    commands.add_parser("authority-audit", help="audit registered authority sources and list unregistered candidates")

    artifact_policy_review_parser = commands.add_parser(
        "artifact-policy-review",
        help="classify and review linkage policy for an extension artifact under .upgrade",
    )
    artifact_policy_review_parser.add_argument("--path", required=True, help="artifact path under .upgrade; the .upgrade/ prefix is optional")
    artifact_policy_review_parser.add_argument("--purpose")
    artifact_policy_review_parser.add_argument("--kind", choices=("auto", *ARTIFACT_KINDS), default="auto")
    artifact_policy_review_parser.add_argument("--module", choices=("auto", *MODULE_ORDER), default="auto")
    artifact_policy_review_parser.add_argument("--lifecycle", choices=("auto", *ARTIFACT_LIFECYCLES), default="auto")
    artifact_policy_review_parser.add_argument("--git-policy", choices=("auto", *ARTIFACT_GIT_POLICIES), default="auto")
    artifact_policy_review_parser.add_argument("--docker-policy", choices=("auto", *ARTIFACT_DOCKER_POLICIES), default="auto")
    artifact_policy_review_parser.add_argument("--required", choices=("auto", "yes", "no"), default="auto")
    artifact_policy_review_parser.add_argument("--agents-link", choices=("auto", "yes", "no"), default="auto")
    artifact_policy_review_parser.add_argument("--readme-link", choices=("auto", "yes", "no"), default="auto")
    artifact_policy_review_parser.add_argument("--rule", action="append", help="additional single-line governance rule; repeat as needed")
    artifact_policy_review_parser.add_argument("--create", action="store_true", help="review creation of the artifact together with policy registration")
    artifact_policy_review_parser.add_argument("--remove", action="store_true", help="review removal of policy linkage without deleting the artifact file")
    artifact_policy_review_parser.add_argument("--plan-out")

    artifact_policy_apply_parser = commands.add_parser("artifact-policy-apply")
    artifact_policy_apply_parser.add_argument("--plan", required=True)
    artifact_policy_apply_parser.add_argument("--approve", required=True)
    artifact_policy_apply_parser.add_argument("--dry-run", action="store_true")

    collect_review_parser = commands.add_parser("collect-review")
    collect_review_parser.add_argument("--focus", action="append")
    collect_review_parser.add_argument("--decision", action="append", help="PATH=move|copy|archive|ignore")
    collect_review_parser.add_argument("--allow-protected", action="append")
    collect_review_parser.add_argument("--allow-sensitive", action="append")
    collect_review_parser.add_argument("--plan-out")

    collect_apply_parser = commands.add_parser("collect-apply")
    collect_apply_parser.add_argument("--plan", required=True)
    collect_apply_parser.add_argument("--approve", required=True)
    collect_apply_parser.add_argument("--allow-protected", action="append")
    collect_apply_parser.add_argument("--allow-sensitive", action="append")
    collect_apply_parser.add_argument("--dry-run", action="store_true")

    phase_parser = commands.add_parser("phase-update")
    phase_parser.add_argument("--phase", type=int, required=True)
    phase_parser.add_argument("--current-task", required=True)
    phase_parser.add_argument("--completed-task")
    phase_parser.add_argument("--next-action")
    phase_parser.add_argument("--plan-drift")
    phase_parser.add_argument("--blocker")
    phase_parser.add_argument("--unverified")
    phase_parser.add_argument("--complete-previous", action="store_true")
    phase_parser.add_argument("--completion-note")
    phase_parser.add_argument("--allow-skip", action="store_true")
    phase_parser.add_argument("--allow-regression", action="store_true")
    phase_parser.add_argument("--transition-reason")
    phase_parser.add_argument("--dry-run", action="store_true")

    cleanup_review_parser = commands.add_parser("cleanup-review")
    cleanup_review_parser.add_argument("--decision", action="append", help="PATH=archive|delete|keep|update|merge")
    cleanup_review_parser.add_argument("--replacement", action="append", help="PATH=FILE replacement for update or merge")
    cleanup_review_parser.add_argument("--plan-out")

    cleanup_apply_parser = commands.add_parser("cleanup-apply")
    cleanup_apply_parser.add_argument("--plan", required=True)
    cleanup_apply_parser.add_argument("--approve", required=True)
    cleanup_apply_parser.add_argument("--dry-run", action="store_true")

    artifact_parser = commands.add_parser("artifact-audit")
    artifact_parser.add_argument("--focus", action="append")

    git_audit_parser = commands.add_parser("git-audit")
    git_audit_parser.add_argument("--focus", action="append")

    git_policy_review_parser = commands.add_parser("git-policy-review")
    git_policy_review_parser.add_argument("--focus", action="append")
    git_policy_review_parser.add_argument("--decision", action="append", help="PATH=add-ignore|keep")
    git_policy_review_parser.add_argument("--plan-out")

    git_policy_apply_parser = commands.add_parser("git-policy-apply")
    git_policy_apply_parser.add_argument("--plan", required=True)
    git_policy_apply_parser.add_argument("--approve", required=True)
    git_policy_apply_parser.add_argument("--dry-run", action="store_true")

    validation_parser = commands.add_parser("validation-record")
    validation_parser.add_argument("--check", required=True)
    validation_parser.add_argument("--status", choices=("passed", "failed", "unverified"), required=True)
    validation_parser.add_argument("--exit-code", type=int)
    validation_parser.add_argument("--environment")
    validation_parser.add_argument("--scope")
    validation_parser.add_argument("--note")
    validation_parser.add_argument("--subject-path")
    validation_parser.add_argument("--subject-digest")
    validation_parser.add_argument("--subject-ref")
    validation_parser.add_argument("--dry-run", action="store_true")

    purge_review_parser = commands.add_parser("purge-review")
    purge_review_parser.add_argument("--plan-out")

    purge_apply_parser = commands.add_parser("purge-apply")
    purge_apply_parser.add_argument("--plan", required=True)
    purge_apply_parser.add_argument("--approve", required=True)
    purge_apply_parser.add_argument("--dry-run", action="store_true")


    decision_review = commands.add_parser("decision-review")
    decision_review.add_argument("--title")
    decision_review.add_argument("--decision")
    decision_review.add_argument("--rationale")
    decision_review.add_argument("--scope")
    decision_review.add_argument("--evidence", action="append")
    decision_review.add_argument("--supersedes")
    decision_review.add_argument("--plan-out")
    decision_apply = commands.add_parser("decision-apply")
    decision_apply.add_argument("--plan", required=True); decision_apply.add_argument("--approve", required=True); decision_apply.add_argument("--dry-run", action="store_true")
    decision_audit = commands.add_parser("decision-audit")
    decision_audit.add_argument("--decision-id")

    risk_review = commands.add_parser("risk-review")
    risk_review.add_argument("--risk-id")
    risk_review.add_argument("--title")
    risk_review.add_argument("--description")
    risk_review.add_argument("--likelihood", choices=RISK_LEVELS)
    risk_review.add_argument("--impact", choices=RISK_LEVELS)
    risk_review.add_argument("--scope")
    risk_review.add_argument("--status", choices=RISK_STATUSES)
    risk_review.add_argument("--mitigation")
    risk_review.add_argument("--evidence", action="append")
    risk_review.add_argument("--acceptance-reason")
    risk_review.add_argument("--resolution")
    risk_review.add_argument("--transition-reason")
    risk_review.add_argument("--plan-out")
    risk_apply = commands.add_parser("risk-apply")
    risk_apply.add_argument("--plan", required=True); risk_apply.add_argument("--approve", required=True); risk_apply.add_argument("--dry-run", action="store_true")
    risk_audit = commands.add_parser("risk-audit")
    risk_audit.add_argument("--risk-id"); risk_audit.add_argument("--status", action="append", choices=RISK_STATUSES)

    recovery_audit = commands.add_parser("recovery-audit")
    recovery_review = commands.add_parser("recovery-review")
    recovery_review.add_argument("--transaction-id", required=True); recovery_review.add_argument("--plan-out")
    recovery_apply = commands.add_parser("recovery-apply")
    recovery_apply.add_argument("--plan", required=True); recovery_apply.add_argument("--approve", required=True); recovery_apply.add_argument("--dry-run", action="store_true")

    exception_review = commands.add_parser("exception-review")
    exception_review.add_argument("--exception-type", choices=EXCEPTION_TYPES)
    exception_review.add_argument("--status", choices=EXCEPTION_STATUSES)
    exception_review.add_argument("--title"); exception_review.add_argument("--scope"); exception_review.add_argument("--requirement"); exception_review.add_argument("--justification"); exception_review.add_argument("--impact")
    exception_review.add_argument("--compensating-control", action="append")
    exception_review.add_argument("--evidence", action="append")
    exception_review.add_argument("--linked-decision", action="append")
    exception_review.add_argument("--linked-risk", action="append")
    exception_review.add_argument("--expires-at"); exception_review.add_argument("--resolution"); exception_review.add_argument("--supersedes"); exception_review.add_argument("--plan-out")
    exception_apply = commands.add_parser("exception-apply")
    exception_apply.add_argument("--plan", required=True); exception_apply.add_argument("--approve", required=True); exception_apply.add_argument("--dry-run", action="store_true")
    exception_audit = commands.add_parser("exception-audit")
    exception_audit.add_argument("--exception-id"); exception_audit.add_argument("--status", action="append", choices=EXCEPTION_STATUSES); exception_audit.add_argument("--exception-type", action="append", choices=EXCEPTION_TYPES)

    delivery_policy_review = commands.add_parser("delivery-policy-review")
    delivery_policy_review.add_argument("--profile", default="project")
    delivery_policy_review.add_argument("--readiness-check", action="append")
    delivery_policy_review.add_argument("--final-check", action="append")
    delivery_policy_review.add_argument("--rollback-check", action="append")
    delivery_policy_review.add_argument("--note")
    delivery_policy_review.add_argument("--plan-out")
    delivery_policy_apply = commands.add_parser("delivery-policy-apply")
    delivery_policy_apply.add_argument("--plan", required=True); delivery_policy_apply.add_argument("--approve", required=True); delivery_policy_apply.add_argument("--dry-run", action="store_true")
    commands.add_parser("delivery-policy-audit")

    delivery_review = commands.add_parser("delivery-review")
    delivery_review.add_argument("--gate", required=True, choices=DELIVERY_GATES); delivery_review.add_argument("--target", required=True); delivery_review.add_argument("--scope"); delivery_review.add_argument("--environment"); delivery_review.add_argument("--require-check", action="append"); delivery_review.add_argument("--subject-path"); delivery_review.add_argument("--subject-digest"); delivery_review.add_argument("--subject-ref"); delivery_review.add_argument("--acceptance-note"); delivery_review.add_argument("--plan-out")
    delivery_apply = commands.add_parser("delivery-apply")
    delivery_apply.add_argument("--plan", required=True); delivery_apply.add_argument("--approve", required=True); delivery_apply.add_argument("--dry-run", action="store_true")
    delivery_audit = commands.add_parser("delivery-audit")
    delivery_audit.add_argument("--delivery-id"); delivery_audit.add_argument("--gate", action="append", choices=DELIVERY_GATES)

    health = commands.add_parser("health-check")
    health_mode = health.add_mutually_exclusive_group()
    health_mode.add_argument("--ci", action="store_true", help="return non-zero for health errors or recovery-required state")
    health_mode.add_argument("--strict", action="store_true", help="return non-zero for any warning/error health state")
    return parser
