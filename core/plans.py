"""Canonical Plan Schema 3 construction, validation, and approval checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .audit import Result, now_iso
from .constants import PLAN_SCHEMA_VERSION, POLICY_VERSION, PREVIOUS_PLAN_SCHEMA_VERSIONS, SKILL_VERSION, SKILL_ROOT
from .errors import CliError, StalePlanError
from .paths import relative_posix, require_within
from .policy import plan_policy_dependencies, policy_implementation_digest
from .workspace import atomic_write, read_text_file, sha256_file


def canonical_plan(plan: dict[str, Any]) -> bytes:
    payload = dict(plan)
    payload.pop("plan_id", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def plan_id(plan: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_plan(plan)).hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise CliError(f"refusing to overwrite plan file: {path}")
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def build_review_plan(
    root: Path,
    kind: str,
    entries: list[dict[str, Any]],
    *,
    clock: Callable[[], str] = now_iso,
    policy_digest: Callable[[str], str] = policy_implementation_digest,
) -> dict[str, Any]:
    dependencies = plan_policy_dependencies(kind)
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "skill_version": SKILL_VERSION,
        "policy_version": POLICY_VERSION,
        "policy_dependencies": list(dependencies),
        "implementation_sha256": policy_digest(kind),
        "kind": kind,
        "root": str(root),
        "created_at": clock(),
        "entries": entries,
    }
    plan["plan_id"] = plan_id(plan)
    return plan


def write_plan_output(root: Path, plan_out: str | None, plan: dict[str, Any], result: Result) -> None:
    if not plan_out:
        return
    output = Path(plan_out)
    if not output.is_absolute():
        output = root / output
    require_within(output, root, "plan output")
    write_json(output, plan)
    result.change(relative_posix(output, root), "created_plan")


def resolve_output_target(root: Path, output: str | None) -> Path | None:
    if not output:
        return None
    target = Path(output)
    if not target.is_absolute():
        target = root / target
    require_within(target, root, "output")
    if target.exists() or target.is_symlink():
        raise CliError(f"refusing to overwrite output file: {target}")
    return target



def assert_plan_fresh(
    value: dict[str, Any],
    *,
    policy_digest: Callable[[str], str] = policy_implementation_digest,
) -> None:
    """Validate the version/policy freshness boundary for an already-parsed plan."""
    schema_version = value.get("schema_version")
    if schema_version != PLAN_SCHEMA_VERSION:
        if schema_version in PREVIOUS_PLAN_SCHEMA_VERSIONS:
            raise StalePlanError(
                f"plan schema {schema_version} is obsolete; current schema is {PLAN_SCHEMA_VERSION}; "
                "regenerate the review plan"
            )
        raise CliError("unsupported or invalid plan schema")
    if value.get("skill_version") != SKILL_VERSION or value.get("policy_version") != POLICY_VERSION:
        raise StalePlanError("plan was created by a different skill or policy version; regenerate the review plan")
    kind = value.get("kind")
    if not isinstance(kind, str):
        raise CliError("plan kind is missing or invalid")
    dependencies = list(plan_policy_dependencies(kind))
    if value.get("policy_dependencies") != dependencies:
        raise StalePlanError("plan policy dependency scope changed since review; regenerate the review plan")
    if value.get("implementation_sha256") != policy_digest(kind):
        raise StalePlanError("plan policy implementation changed since review; regenerate the review plan")


def validate_plan(
    value: Any,
    *,
    policy_digest: Callable[[str], str] = policy_implementation_digest,
) -> dict[str, Any]:
    """Validate Plan Schema 3, freshness, and canonical plan_id integrity."""
    if not isinstance(value, dict):
        raise CliError("unsupported or invalid plan schema")
    assert_plan_fresh(value, policy_digest=policy_digest)
    recorded = value.get("plan_id")
    if not recorded or recorded != plan_id(value):
        raise CliError("plan_id does not match plan content")
    return value


def assert_exact_approval(plan: dict[str, Any], approval: str) -> None:
    if approval != plan.get("plan_id"):
        raise CliError("approval token does not match plan_id")


def assert_file_snapshot_fresh(path: Path, entry: dict[str, Any], *, message: str) -> None:
    """Revalidate the file snapshot tuple used by mutation plans."""
    if (
        not path.is_file()
        or sha256_file(path) != entry.get("sha256")
        or path.stat().st_mtime_ns != entry.get("mtime_ns")
    ):
        raise StalePlanError(message)


def create_plan(
    root: Path,
    kind: str,
    entries: list[dict[str, Any]],
    *,
    clock: Callable[[], str] = now_iso,
    policy_digest: Callable[[str], str] = policy_implementation_digest,
) -> dict[str, Any]:
    """Create a review plan through the stable core API."""
    return build_review_plan(root, kind, entries, clock=clock, policy_digest=policy_digest)

def load_plan(
    path: Path,
    *,
    policy_digest: Callable[[str], str] = policy_implementation_digest,
) -> dict[str, Any]:
    try:
        value = json.loads(read_text_file(path).text)
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError(f"cannot read plan: {path}: {exc}") from exc
    return validate_plan(value, policy_digest=policy_digest)


def validate_apply_plan(root: Path, plan: dict[str, Any], approve: str, kind: str) -> list[dict[str, Any]]:
    if plan.get("kind") != kind or plan.get("root") != str(root):
        raise CliError("plan kind or repository root does not match")
    assert_exact_approval(plan, approve)
    entries = plan.get("entries")
    if not isinstance(entries, list):
        raise CliError("plan entries must be a list")
    return entries
