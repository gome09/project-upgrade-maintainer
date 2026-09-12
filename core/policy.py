"""Logical policy registry, Plan dependency resolution, and implementation digests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .constants import SKILL_ROOT
from .errors import CliError

# Stable logical dependency IDs are used internally while Plan Schema 3
# continues to persist concrete relative file paths. This decouples
# plan-kind semantics from the physical layout without changing the public
# JSON field type or schema version.
POLICY_FILES_BY_ID: dict[str, tuple[str, ...]] = {
    # Router SKILL.md files are intentionally excluded. They control context
    # loading, not plan semantics; only executable policy references belong in
    # persisted plan dependency scopes.
    "shared": (
        "references/shared/safety-and-lifecycle.md",
    ),
    "bootstrap.authority": (
        "subskills/bootstrap/references/authority-network.md",
    ),
    "lifecycle.artifact-policy": (
        "subskills/lifecycle/references/artifact-policy-linking.md",
    ),
    "lifecycle.collect": (
        "subskills/lifecycle/references/collect-classification.md",
    ),
    "lifecycle.cleanup": (
        "subskills/lifecycle/references/cleanup-classification.md",
    ),
    "lifecycle.git": (
        "subskills/lifecycle/references/git-policy.md",
    ),
    "lifecycle.purge": (
        "subskills/lifecycle/references/purge.md",
    ),
    "governance.validation": (
        "subskills/governance/references/validation.md",
    ),
    "governance.decision": (
        "subskills/governance/references/decision.md",
    ),
    "governance.risk": (
        "subskills/governance/references/risk.md",
    ),
    "core.recovery": (
        "references/shared/transactions-and-recovery.md",
    ),
    "delivery.exception": (
        "subskills/delivery/references/exception.md",
    ),
    "delivery.gate": (
        "subskills/delivery/references/delivery-gate.md",
    ),
    "delivery.policy": (
        "subskills/delivery/references/delivery-policy.md",
    ),
    "templates.authority": (
        "assets/templates/AGENTS.md",
        "assets/templates/AUTHORITY.json",
        "assets/templates/AUTHORITY.md",
    ),
    "templates.artifact-governance": (
        "assets/templates/AGENTS.md",
        "assets/templates/README.md",
        "assets/templates/ARTIFACTS.json",
    ),
}

POLICY_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "authority": ("shared", "bootstrap.authority", "templates.authority"),
    "artifact-policy": (
        "shared",
        "lifecycle.artifact-policy",
        "templates.artifact-governance",
    ),
    "collect": (
        "shared",
        "lifecycle.collect",
    ),
    "cleanup": (
        "shared",
        "lifecycle.cleanup",
    ),
    "git-policy": (
        "shared",
        "lifecycle.git",
    ),
    "purge": (
        "shared",
        "lifecycle.purge",
    ),
    "decision": ("shared", "governance.decision"),
    "risk": ("shared", "governance.risk"),
    "recovery": ("shared", "core.recovery"),
    "exception": ("shared", "delivery.exception"),
    "delivery-policy": ("shared", "delivery.policy", "delivery.gate"),
    "delivery": (
        "shared",
        "delivery.gate",
        "delivery.policy",
        "delivery.exception",
        "governance.decision",
        "governance.risk",
        "governance.validation",
        "core.recovery",
    ),
}

# These implementation files are hard-safety dependencies shared by all
# mutation plans. They are folded into implementation_sha256 without appearing
# in Plan Schema 3 policy_dependencies.
CORE_IMPLEMENTATION_FILES = (
    "core/constants.py",
    "core/errors.py",
    "core/paths.py",
    "core/workspace.py",
    "core/config.py",
    "core/artifacts.py",
    "core/authority.py",
    "core/manifests.py",
    "core/schemas.py",
    "core/policy.py",
    "core/plans.py",
    "core/locking.py",
    "core/audit.py",
    "core/transactions.py",
    "core/durability.py",
    "core/subjects.py",
)

_SHARED_COMMAND_IMPLEMENTATION_FILES = (
    "scripts/upgrade_workspace.py",
    "cli/parser.py",
    "cli/main.py",
    "commands/_runtime.py",
)

COMMAND_IMPLEMENTATION_FILES_BY_KIND: dict[str, tuple[str, ...]] = {
    "authority": (
        "commands/_common.py",
        "commands/_inventory.py",
        "commands/_bridges.py",
        "commands/authority.py",
    ),
    "artifact-policy": (
        "commands/_common.py",
        "commands/_inventory.py",
        "commands/_workspace.py",
        "commands/_artifacts.py",
        "commands/_bridges.py",
        "commands/artifact_policy.py",
    ),
    "collect": (
        "commands/_common.py",
        "commands/_inventory.py",
        "commands/_workspace.py",
        "commands/collect.py",
    ),
    "cleanup": (
        "commands/_common.py",
        "commands/_governance.py",
        "commands/cleanup.py",
    ),
    "git-policy": (
        "commands/_common.py",
        "commands/_inventory.py",
        "commands/git_policy.py",
    ),
    "purge": (
        "commands/_common.py",
        "commands/purge.py",
    ),
    "decision": ("commands/_governance_records.py", "commands/decision.py"),
    "risk": ("commands/_governance_records.py", "commands/risk.py"),
    "recovery": ("commands/recovery.py",),
    "exception": ("commands/_governance_records.py", "commands/_delivery_state.py", "commands/exception.py"),
    "delivery-policy": ("commands/_governance_records.py", "commands/_delivery_policy.py", "commands/delivery_policy.py"),
    "delivery": ("commands/_governance_records.py", "commands/_delivery_policy.py", "commands/_delivery_state.py", "commands/delivery.py"),
}


def policy_dependency_ids(kind: str) -> tuple[str, ...]:
    """Return stable logical policy IDs for a persisted Plan kind."""
    try:
        return POLICY_DEPENDENCIES[kind]
    except KeyError as exc:
        raise CliError(f"unsupported plan kind for policy digest: {kind}") from exc


def resolve_policy_dependencies(dependency_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve logical IDs to ordered, de-duplicated canonical file paths."""
    files: list[str] = []
    seen: set[str] = set()
    for dependency_id in dependency_ids:
        try:
            mapped = POLICY_FILES_BY_ID[dependency_id]
        except KeyError as exc:
            raise CliError(f"unknown logical policy dependency: {dependency_id}") from exc
        for relative in mapped:
            if relative in seen:
                continue
            seen.add(relative)
            files.append(relative)
    return tuple(files)


def plan_policy_dependencies(kind: str) -> tuple[str, ...]:
    """Return Plan Schema 3 concrete policy paths for *kind*."""
    return resolve_policy_dependencies(policy_dependency_ids(kind))


def implementation_files_for_kind(kind: str) -> tuple[str, ...]:
    try:
        command_files = COMMAND_IMPLEMENTATION_FILES_BY_KIND[kind]
    except KeyError as exc:
        raise CliError(f"unsupported plan kind for implementation digest: {kind}") from exc
    return (*CORE_IMPLEMENTATION_FILES, *_SHARED_COMMAND_IMPLEMENTATION_FILES, *command_files)


def policy_implementation_digest(
    kind: str,
    *,
    skill_root: Path = SKILL_ROOT,
    implementation_files: tuple[str, ...] = CORE_IMPLEMENTATION_FILES,
) -> str:
    digest = hashlib.sha256()
    digest.update(f"plan-kind:{kind}".encode("utf-8"))
    digest.update(b"\0")
    dependencies = (*plan_policy_dependencies(kind), *implementation_files)
    seen: set[str] = set()
    for relative in dependencies:
        if relative in seen:
            continue
        seen.add(relative)
        path = skill_root / relative
        if not path.is_file():
            raise CliError(f"plan policy file is missing: {path}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
