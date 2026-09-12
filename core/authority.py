"""Project authority-source discovery, registry provenance, and generated view helpers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable

from .constants import (
    AGENT_INSTRUCTION_NAMES,
    AUTHORITY_MAP_MARKER,
    AUTHORITY_REGISTRY_NAME,
    AUTHORITY_REGISTRY_SCHEMA_VERSION,
    AUTHORITY_VIEW_NAME,
)
from .errors import CliError
from .paths import normalize_user_path, relative_posix, require_within
from .schemas import validate_authority_registry
from .workspace import TextFile, atomic_write, read_text_file, write_managed_json


def empty_authority_registry() -> dict[str, Any]:
    return {
        "schema_version": AUTHORITY_REGISTRY_SCHEMA_VERSION,
        "managed_by": "project-upgrade-maintainer",
        "entries": [],
    }


def authority_registry_path(upgrade: Path) -> Path:
    return require_within(upgrade / AUTHORITY_REGISTRY_NAME, upgrade, "authority registry")


def authority_view_path(upgrade: Path) -> Path:
    return require_within(upgrade / AUTHORITY_VIEW_NAME, upgrade, "authority view")


def authority_registry_digest(registry: dict[str, Any]) -> str:
    validated = validate_authority_registry(registry)
    canonical = json.dumps(validated, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(b"project-upgrade-maintainer:authority-registry:v1\0")
    digest.update(canonical)
    return digest.hexdigest()


def load_authority_registry(upgrade: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    path = authority_registry_path(upgrade)
    if path.is_symlink():
        raise CliError(f"{AUTHORITY_REGISTRY_NAME} must not be a symlink")
    if not path.exists():
        if allow_missing:
            return empty_authority_registry()
        raise CliError(f"{AUTHORITY_REGISTRY_NAME} is missing; run sync to create the managed authority registry")
    if not path.is_file():
        raise CliError(f"{AUTHORITY_REGISTRY_NAME} is not a file")
    try:
        payload = json.loads(read_text_file(path).text)
    except json.JSONDecodeError as exc:
        raise CliError(f"{AUTHORITY_REGISTRY_NAME} is invalid JSON: {exc}") from exc
    return validate_authority_registry(payload)


def write_authority_registry(
    upgrade: Path,
    registry: dict[str, Any],
    result: Any,
    *,
    dry_run: bool,
    writer: Callable[..., bool] = atomic_write,
) -> bool:
    return write_managed_json(
        authority_registry_path(upgrade),
        validate_authority_registry(registry),
        result,
        f".upgrade/{AUTHORITY_REGISTRY_NAME}",
        dry_run=dry_run,
        writer=writer,
    )


def authority_entry_map(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["path"]: entry for entry in validate_authority_registry(registry)["entries"]}


def normalize_authority_source_path(value: str) -> str:
    value = normalize_user_path(value)
    if not value:
        raise CliError("authority source path must not be empty")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise CliError("authority source path must be repository-relative")
    normalized = candidate.as_posix().lstrip("./")
    if not normalized or normalized == ".upgrade" or normalized.startswith(".upgrade/"):
        raise CliError("external authority sources must be outside .upgrade/; managed upgrade authority is implicit")
    return normalized


def normalize_authority_scope(value: str) -> str:
    value = value.strip().replace("\\", "/")
    if value in {"", ".", "./"}:
        return "."
    candidate = Path(normalize_user_path(value))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise CliError("authority scope must be repository-relative")
    normalized = candidate.as_posix().strip("/")
    if not normalized or normalized == ".upgrade" or normalized.startswith(".upgrade/"):
        raise CliError("authority scope must not target .upgrade/")
    return normalized


def _scope_for(relative: str) -> str:
    parent = Path(relative).parent.as_posix()
    if parent in {".", ""}:
        return "."
    if parent.startswith("docs/") or parent in {"docs", ".github", ".circleci"}:
        return "."
    return parent


def classify_authority_candidate(relative: str) -> dict[str, Any] | None:
    """Classify a repository path as a possible authority source without granting authority."""
    relative = normalize_authority_source_path(relative)
    path = Path(relative)
    name = path.name.casefold()
    parts = tuple(part.casefold() for part in path.parts)
    instruction_names = {item.casefold() for item in AGENT_INSTRUCTION_NAMES}

    kind: str | None = None
    authority = "reference"
    confidence = "medium"
    required = False
    reason = ""

    if name in instruction_names:
        kind = "agent_instruction"
        authority = "repository" if len(path.parts) == 1 else "scoped"
        confidence = "high"
        required = True
        reason = "supported Agent instruction file detected"
    elif name in {"security.md", "security.rst", "security.txt"}:
        kind = "security_policy"; authority = "repository"; confidence = "high"; required = True
        reason = "security policy filename detected"
    elif name in {"contributing.md", "contributing.rst", "contributing"}:
        kind = "contribution_policy"; authority = "repository"; confidence = "high"; required = False
        reason = "contribution policy filename detected"
    elif name == "codeowners":
        kind = "ownership_policy"; authority = "repository"; confidence = "high"; required = False
        reason = "CODEOWNERS detected"
    elif name in {"openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml", "swagger.yml"}:
        kind = "api_contract"; authority = "scoped" if len(path.parts) > 1 else "repository"; confidence = "high"; required = True
        reason = "API contract filename detected"
    elif relative.casefold().startswith(".github/workflows/") or name in {".gitlab-ci.yml", "azure-pipelines.yml", "jenkinsfile", "circle.yml"} or ".circleci" in parts:
        kind = "ci_policy"; authority = "reference"; confidence = "medium"; required = False
        reason = "CI workflow/configuration detected"
    elif name in {"architecture.md", "architecture.rst", "architecture.txt", "design.md", "system-design.md", "system_design.md"} or "architecture" in parts:
        kind = "architecture"; authority = "reference"; confidence = "medium"; required = False
        reason = "architecture/design documentation detected"
    elif "adr" in parts and path.suffix.casefold() in {".md", ".rst", ".txt"}:
        kind = "architecture"; authority = "reference"; confidence = "medium"; required = False
        reason = "ADR documentation detected"
    elif name in {"deployment.md", "deploy.md", "release.md", "release-process.md", "runbook.md", "operations.md"}:
        kind = "delivery_policy"; authority = "reference"; confidence = "medium"; required = False
        reason = "delivery/operations documentation detected"
    elif path.suffix.casefold() in {".md", ".rst", ".txt"} and any(term in name for term in ("migration", "upgrade", "rollout", "rollback")):
        kind = "migration_guidance"; authority = "reference"; confidence = "medium"; required = False
        reason = "migration/upgrade guidance filename detected"
    elif name in {"readme.md", "readme.rst", "readme.txt"} and len(path.parts) == 1:
        kind = "project_overview"; authority = "reference"; confidence = "medium"; required = False
        reason = "repository overview detected"

    if kind is None:
        return None
    return {
        "path": relative,
        "recommended_kind": kind,
        "recommended_scope": _scope_for(relative),
        "recommended_authority": authority,
        "recommended_required": required,
        "confidence": confidence,
        "reason": reason,
    }


def discover_authority_candidates(root: Path, files: Iterable[Path], *, limit: int = 100) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in files:
        try:
            relative = relative_posix(path, root)
        except ValueError:
            continue
        if relative == ".upgrade" or relative.startswith(".upgrade/"):
            continue
        classification = classify_authority_candidate(relative)
        if classification is None:
            continue
        key = classification["path"].casefold()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(classification)
    candidates.sort(key=lambda item: (0 if item["confidence"] == "high" else 1, item["path"].casefold()))
    return candidates[:limit]


def build_authority_entry(
    root: Path,
    classification: dict[str, Any],
    *,
    kind: str | None = None,
    scope: str | None = None,
    authority: str | None = None,
    required: bool | None = None,
) -> dict[str, Any]:
    path_value = normalize_authority_source_path(classification["path"])
    raw_target = root / path_value
    if raw_target.is_symlink():
        raise CliError(f"authority source must not be a symlink: {path_value}")
    target = require_within(raw_target, root, "authority source")
    if not target.is_file():
        raise CliError(f"authority source must be an existing regular file: {path_value}")
    stat = target.stat()
    entry = {
        "path": path_value,
        "kind": kind or classification["recommended_kind"],
        "scope": normalize_authority_scope(scope or classification["recommended_scope"]),
        "authority": authority or classification["recommended_authority"],
        "required": classification["recommended_required"] if required is None else bool(required),
        "managed": False,
        "sha256": _sha256_file(target),
        "size": stat.st_size,
    }
    from .schemas import validate_authority_entry
    return validate_authority_entry(entry)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def authority_view_text(registry: dict[str, Any]) -> str:
    registry = validate_authority_registry(registry)
    lines = [
        AUTHORITY_MAP_MARKER,
        "# Project Authority Map",
        "",
        "This file is generated by `$project-upgrade-maintainer` from `.upgrade/AUTHORITY.json`. Do not edit it manually.",
        "It is a navigation and scope map; repository documents remain owned by their existing maintainers and are never rewritten merely because they are registered here.",
        "",
        "## Managed Upgrade Authority",
        "",
        "- `.upgrade/AGENTS.md`: scoped governance contract for `.upgrade/**`.",
        "- `.upgrade/CONFIG.json`: active governance profile/modules and registry provenance.",
        "- `.upgrade/AUTHORITY.json`: approved external project authority-source registry.",
        "- `.upgrade/STATE.md`: current upgrade execution state.",
        "- `.upgrade/docs/UPGRADE_REQUIREMENTS.md`: approved/recorded upgrade requirements when planning is active.",
        "- `.upgrade/docs/UPGRADE_PLAN.md`: current upgrade plan when planning is active.",
        "",
        "## Approved Project Authority Sources",
        "",
    ]
    if not registry["entries"]:
        lines.append("- None registered. Run `authority-review` and approve an `authority-apply` plan to register project sources.")
    else:
        lines.extend([
            "| Path | Kind | Authority | Scope | Required |",
            "| --- | --- | --- | --- | --- |",
        ])
        for entry in registry["entries"]:
            lines.append(
                f"| `{entry['path']}` | `{entry['kind']}` | `{entry['authority']}` | `{entry['scope']}` | "
                f"{'yes' if entry['required'] else 'no'} |"
            )
    lines.extend([
        "",
        "## Interpretation Rules",
        "",
        "- `repository`: approved as a repository-wide authority source for its declared kind.",
        "- `scoped`: approved only for the recorded repository-relative scope.",
        "- `reference`: useful project context, but not elevated to normative authority by this registry.",
        "- Content drift never auto-rebinds. Run `authority-review --accept <path>` and approve the resulting plan before updating a registered digest.",
        "- Registration grants discoverability and drift monitoring only; `managed` remains false for project-owned sources.",
        "",
    ])
    return "\n".join(lines)


def plan_authority_view_update(upgrade: Path, registry: dict[str, Any]) -> tuple[Path, TextFile, str]:
    target = authority_view_path(upgrade)
    if target.is_symlink():
        raise CliError(f"managed authority view must not be a symlink: .upgrade/{AUTHORITY_VIEW_NAME}")
    if target.exists() and not target.is_file():
        raise CliError(f"managed authority view target is not a file: .upgrade/{AUTHORITY_VIEW_NAME}")
    source = read_text_file(target) if target.exists() else TextFile("", False, os.linesep)
    if target.exists() and not source.text.startswith(AUTHORITY_MAP_MARKER + "\n"):
        raise CliError(
            f"unmanaged .upgrade/{AUTHORITY_VIEW_NAME} already exists; refusing to overwrite it. "
            "Move or reconcile that file before running initialize/sync"
        )
    return target, source, authority_view_text(registry)
