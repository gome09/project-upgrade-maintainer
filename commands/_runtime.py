"""Shared runtime hooks used by command handlers and the CLI entry point."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from core import audit as _core_audit
from core import plans as _core_plans
from core import policy as _core_policy
from core.audit import Result
from core.constants import SKILL_ROOT as DEFAULT_SKILL_ROOT
from core.workspace import atomic_write as _core_atomic_write

_clock: Callable[[], str] = _core_audit.now_iso
_atomic_write: Callable[..., bool] = _core_atomic_write
_skill_root: Path = DEFAULT_SKILL_ROOT

def configure(*, clock: Callable[[], str] | None = None, atomic_write_fn: Callable[..., bool] | None = None, skill_root: Path | None = None) -> None:
    global _clock, _atomic_write, _skill_root
    if clock is not None:
        _clock = clock
    if atomic_write_fn is not None:
        _atomic_write = atomic_write_fn
    if skill_root is not None:
        _skill_root = Path(skill_root)

def now_iso() -> str:
    return _clock()

def atomic_write(path: Path, text: str, **kwargs: Any) -> bool:
    return _atomic_write(path, text, **kwargs)

def _implementation_files(kind: str) -> tuple[str, ...]:
    try:
        current = _skill_root.resolve()
        baseline = DEFAULT_SKILL_ROOT.resolve()
    except OSError:
        return ()
    return _core_policy.implementation_files_for_kind(kind) if current == baseline else ()

def policy_implementation_digest(kind: str) -> str:
    return _core_policy.policy_implementation_digest(
        kind, skill_root=_skill_root, implementation_files=_implementation_files(kind),
    )

def build_review_plan(root: Path, kind: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    return _core_plans.build_review_plan(
        root, kind, entries, clock=now_iso, policy_digest=policy_implementation_digest,
    )

def load_plan(path: Path) -> dict[str, Any]:
    return _core_plans.load_plan(path, policy_digest=policy_implementation_digest)

def write_report(root: Path, result: Result, dry_run: bool = False) -> list[str]:
    return _core_audit.write_report(root, result, dry_run, clock=now_iso, writer=atomic_write)


def finalize_mutation(root: Path, result: Result, collected: list[dict[str, Any]] | None = None) -> None:
    _core_audit.finalize_mutation(root, result, collected, clock=now_iso, writer=atomic_write)
