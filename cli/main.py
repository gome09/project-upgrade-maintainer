"""Stable CLI dispatch, rendering, locking, and error-to-exit-code translation."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from core.audit import Result
from core.agent_view import agent_payload
from core.constants import MUTATION_COMMANDS, TRANSACTIONAL_MUTATION_COMMANDS, SKILL_ROOT
from core.errors import CliError
from core.locking import mutation_lock
from core.transactions import Transaction
from core.paths import require_within, root_from
from core.plans import resolve_output_target
from core.workspace import atomic_write as core_atomic_write
from core import audit as core_audit

from commands import _runtime as command_runtime
from commands.authority import authority_apply, authority_audit, authority_review
from commands.artifact_policy import artifact_policy_apply, artifact_policy_review
from commands.bootstrap import bootstrap_review, initialize
from commands.cleanup import cleanup_apply, cleanup_review
from commands.collect import collect_apply, collect_review
from commands.diagnostics import artifact_audit, git_audit, health_check, version_info
from commands.git_policy import git_policy_apply, git_policy_review
from commands.inspect import inspect_plan
from commands.phase import phase_update
from commands.purge import purge_apply, purge_review
from commands.sync import sync
from commands.validation import validation_record
from commands.decision import decision_review, decision_apply, decision_audit
from commands.risk import risk_review, risk_apply, risk_audit
from commands.recovery import recovery_audit, recovery_review, recovery_apply
from commands.exception import exception_review, exception_apply, exception_audit
from commands.delivery import delivery_review, delivery_apply, delivery_audit
from commands.delivery_policy import delivery_policy_review, delivery_policy_apply, delivery_policy_audit
from .parser import build_parser


HANDLERS = {
    "version": version_info,
    "inspect": inspect_plan,
    "bootstrap-review": bootstrap_review,
    "initialize": initialize,
    "sync": sync,
    "authority-review": authority_review,
    "authority-apply": authority_apply,
    "authority-audit": authority_audit,
    "artifact-policy-review": artifact_policy_review,
    "artifact-policy-apply": artifact_policy_apply,
    "collect-review": collect_review,
    "collect-apply": collect_apply,
    "phase-update": phase_update,
    "cleanup-review": cleanup_review,
    "cleanup-apply": cleanup_apply,
    "artifact-audit": artifact_audit,
    "git-audit": git_audit,
    "git-policy-review": git_policy_review,
    "git-policy-apply": git_policy_apply,
    "validation-record": validation_record,
    "purge-review": purge_review,
    "purge-apply": purge_apply,
    "decision-review": decision_review,
    "decision-apply": decision_apply,
    "decision-audit": decision_audit,
    "risk-review": risk_review,
    "risk-apply": risk_apply,
    "risk-audit": risk_audit,
    "recovery-audit": recovery_audit,
    "recovery-review": recovery_review,
    "recovery-apply": recovery_apply,
    "exception-review": exception_review,
    "exception-apply": exception_apply,
    "exception-audit": exception_audit,
    "delivery-policy-review": delivery_policy_review,
    "delivery-policy-apply": delivery_policy_apply,
    "delivery-policy-audit": delivery_policy_audit,
    "delivery-review": delivery_review,
    "delivery-apply": delivery_apply,
    "delivery-audit": delivery_audit,
    "health-check": health_check,
}


def markdown_output(payload: dict[str, Any]) -> str:
    if payload.get("view") == "agent":
        lines = [
            f"# {payload['command']}",
            "",
            f"- Status: `{payload['status']}`",
            f"- Root: `{payload['root']}`",
            "",
            "## Agent decision surface",
            "",
            "```json",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        return "\n".join(lines)
    lines = [f"# {payload['command']}", "", f"- 状态: `{payload['status']}`", f"- 根目录: `{payload['root']}`"]
    if payload.get("plan_id"):
        lines.append(f"- Plan ID: `{payload['plan_id']}`")
    lines.extend(["", "## 变更"])
    lines.extend([f"- `{item['path']}`: {item['action']}" for item in payload["changed"]] or ["- 无"])
    lines.extend(["", "## 跳过"])
    lines.extend([f"- `{item['path']}`: {item['reason']}" for item in payload["skipped"]] or ["- 无"])
    lines.extend(["", "## 警告"])
    lines.extend([f"- {item}" for item in payload["warnings"]] or ["- 无"])
    lines.extend(["", "## 错误"])
    lines.extend([f"- {item}" for item in payload["errors"]] or ["- 无"])
    lines.extend(["", "## 结果", "", "```json", json.dumps(payload["result"], ensure_ascii=False, indent=2), "```", ""])
    return "\n".join(lines)


def emit(
    payload: dict[str, Any],
    fmt: str,
    output: Path | None,
    root: Path,
    *,
    atomic_write_fn: Callable[..., bool] = core_atomic_write,
) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n" if fmt == "json" else markdown_output(payload)
    if output:
        require_within(output, root, "output")
        if output.exists() or output.is_symlink():
            raise CliError(f"refusing to overwrite output file: {output}")
        atomic_write_fn(output, text)
    else:
        sys.stdout.write(text)


def _view_payload(result: Result, view: str) -> dict[str, Any]:
    return agent_payload(result) if view == "agent" else result.as_dict()


def _health_exit_code(args: Any, result: Result) -> int:
    if getattr(args, "command", None) != "health-check" or not (getattr(args, "ci", False) or getattr(args, "strict", False)):
        return 0
    if result.status == "error":
        return 5
    ci_state = result.result.get("ci", {}) if isinstance(result.result, dict) else {}
    if isinstance(ci_state, dict) and ci_state.get("recovery_required"):
        return 6
    if getattr(args, "strict", False) and result.status == "warning":
        return 7
    return 0


def run(
    argv: list[str] | None = None,
    *,
    clock: Callable[[], str] | None = None,
    atomic_write_fn: Callable[..., bool] = core_atomic_write,
    skill_root: Path = SKILL_ROOT,
) -> int:
    """Run the CLI with injectable runtime hooks for deterministic execution."""
    parser = build_parser()
    args = parser.parse_args(argv)
    root: Path | None = None
    result: Result | None = None
    actual_mutation = False
    effective_clock = clock or core_audit.now_iso
    command_runtime.configure(clock=effective_clock, atomic_write_fn=atomic_write_fn, skill_root=skill_root)
    try:
        root = root_from(args.root)
        output_target = resolve_output_target(root, args.output)
        result = Result(command=args.command, root=root)
        actual_mutation = args.command in MUTATION_COMMANDS and not getattr(args, "dry_run", False)
        if actual_mutation:
            transactional = args.command in TRANSACTIONAL_MUTATION_COMMANDS and (root / ".upgrade").exists()
            transaction = Transaction(root, args.command) if transactional else None
            with mutation_lock(root, operation=args.command, transaction_id=transaction.transaction_id if transaction else None):
                if transaction is not None:
                    transaction._start()
                try:
                    HANDLERS[args.command](root, args, result)
                    if transaction is not None:
                        if result.status == "partial" or result.errors:
                            transaction.fail("command completed with partial/error result")
                        else:
                            transaction.commit()
                except Exception as exc:
                    if transaction is not None:
                        transaction.fail(str(exc))
                    raise
        else:
            HANDLERS[args.command](root, args, result)
        if result.errors and result.status == "ok":
            result.status = "error"
        emit(_view_payload(result, args.view), args.format, output_target, root, atomic_write_fn=atomic_write_fn)
        if result.status == "partial":
            return 4
        return _health_exit_code(args, result)
    except CliError as exc:
        if root is None:
            try:
                root = Path(args.root).resolve()
            except OSError:
                root = Path(args.root)
        if result is None:
            result = Result(command=args.command, root=root, status="error", errors=[str(exc)])
        else:
            result.errors.append(str(exc))
            result.status = "partial" if actual_mutation and result.changed else "error"
        emit(_view_payload(result, args.view), args.format, None, root, atomic_write_fn=atomic_write_fn)
        return 4 if result.status == "partial" else exc.code
    except Exception as exc:  # pragma: no cover - last-resort stable interface
        print(f"unexpected error: {exc}", file=sys.stderr)
        if root is None:
            try:
                root = Path(args.root).resolve()
            except OSError:
                root = Path(args.root)
        message = f"unexpected error: {exc}"
        if result is None:
            result = Result(command=args.command, root=root, status="error", errors=[message])
        else:
            result.errors.append(message)
            result.status = "partial" if actual_mutation and result.changed else "error"
        emit(_view_payload(result, args.view), args.format, None, root, atomic_write_fn=atomic_write_fn)
        return 4 if result.status == "partial" else 1


def main(argv: list[str] | None = None) -> int:
    return run(argv)
