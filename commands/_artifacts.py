"""Artifact-policy classification and managed ignore/governance rendering."""
from __future__ import annotations
import argparse
from pathlib import Path
from typing import Any, Callable
from core.constants import *
from core.artifacts import empty_artifact_registry
from core.errors import CliError
from core.schemas import _single_line_policy_text, normalize_registered_artifact_path, validate_artifact_policy_entry, validate_artifact_registry
from core.workspace import read_text_file, update_marker
from ._inventory import escape_gitignore_literal
from ._workspace import module_for_upgrade_destination

def artifact_policy_agents_block(registry: dict[str, Any]) -> str:
    entries = [entry for entry in validate_artifact_registry(registry)["entries"] if entry["expose_in_agents"]]
    lines = [AGENTS_ARTIFACT_START, "## Registered Artifact Policies", ""]
    if not entries:
        lines.append("- None registered. Add governed extension artifacts through `artifact-policy-review` and `artifact-policy-apply`.")
    else:
        for entry in entries:
            metadata = f"module={entry['module']}; lifecycle={entry['lifecycle']}; git={entry['git_policy']}; required={str(entry['required']).lower()}"
            lines.append(f"- `{entry['path']}` — {entry['purpose']} ({metadata})")
            for rule in entry["rules"]:
                lines.append(f"  - {rule}")
    lines.extend([AGENTS_ARTIFACT_END])
    return "\n".join(lines)


def artifact_policy_readme_block(registry: dict[str, Any]) -> str:
    entries = [entry for entry in validate_artifact_registry(registry)["entries"] if entry["expose_in_readme"]]
    lines = [README_ARTIFACT_START, "## Registered Artifact Index", ""]
    if not entries:
        lines.append("- No extension artifacts are registered.")
    else:
        for entry in entries:
            lines.append(
                f"- `{entry['path']}` — {entry['purpose']} "
                f"(kind: {entry['kind']}; module: {entry['module']}; lifecycle: {entry['lifecycle']})"
            )
    lines.extend([README_ARTIFACT_END])
    return "\n".join(lines)


def governance_contract_text(registry: dict[str, Any] | None = None) -> str:
    template = read_text_file(TEMPLATES / "AGENTS.md").text
    if not template.startswith(GOVERNANCE_CONTRACT_MARKER + "\n"):
        raise CliError("governance contract template is missing its managed ownership marker")
    registry = validate_artifact_registry(registry or empty_artifact_registry())
    rendered = update_marker(template, AGENTS_ARTIFACT_START, AGENTS_ARTIFACT_END, artifact_policy_agents_block(registry))
    return rendered.rstrip() + "\n"


def artifact_policy_classification(path: str, purpose: str = "") -> dict[str, Any]:
    normalized_path = normalize_registered_artifact_path(path)
    lower = f"{normalized_path} {purpose}".casefold()
    name = Path(normalized_path).name.casefold()
    parts = {part.casefold() for part in Path(normalized_path).parts}
    governance_terms = {
        "risk", "decision", "security", "review", "rollback", "delivery", "gate", "acceptance",
        "exception", "waiver", "approval", "compliance", "governance", "migration-matrix", "migration_matrix",
        "风险", "决策", "安全", "审查", "回滚", "交付", "验收", "例外", "豁免",
    }
    runtime_terms = {"tmp", "temp", "debug", "scratch", "runtime", "raw", "log", "logs", "cache"}
    governance_hits = sorted(term for term in governance_terms if term in lower)
    runtime_hits = sorted(term for term in runtime_terms if term in lower)
    runtime_signal = bool(runtime_hits) or Path(name).suffix.casefold() == ".log" or bool(parts & TEMPORARY_PARTS)
    governance_signal = bool(governance_hits) or bool(parts & {"reviews", "decisions"})
    ambiguous = governance_signal and runtime_signal
    if ambiguous:
        kind = "content"
        reason = "conflicting governance and runtime signals; explicit --kind is required before registration"
    elif governance_signal:
        kind = "governance"
        reason = "governance/evidence terminology or governed review/decision scope detected"
    elif runtime_signal:
        kind = "runtime"
        reason = "temporary/runtime/log signal detected"
    else:
        kind = "content"
        reason = "no governance or runtime signal detected; treat as retained upgrade content"

    semantic_module = module_for_upgrade_destination(normalized_path)
    if kind == "governance" and semantic_module == "core":
        if any(term in lower for term in ("validation", "evidence", "test", "验证", "证据")):
            semantic_module = "validation"
        elif any(term in lower for term in ("report", "summary", "final", "报告", "总结")):
            semantic_module = "reporting"
        elif any(term in lower for term in ("phase", "stage", "checkpoint", "implementation", "阶段", "实施")):
            semantic_module = "phase"
        elif any(term in lower for term in ("plan", "requirement", "rollback", "migration", "matrix", "计划", "需求", "回滚", "迁移")):
            semantic_module = "planning"
        else:
            semantic_module = "review"
    elif kind == "runtime":
        semantic_module = "lifecycle"

    inferred_rules: list[str] = []
    if kind == "governance":
        if any(term in lower for term in ("security", "auth", "credential", "secret", "permission", "安全", "认证", "权限", "密钥")):
            inferred_rules.append("Read and update this artifact before authentication, authorization, credential, secret, or permission-sensitive upgrade changes.")
        if any(term in lower for term in ("risk", "风险")):
            inferred_rules.append("Consult this artifact before accepting, changing, or closing an upgrade risk; the Skill must not accept risk on the user's behalf.")
        if any(term in lower for term in ("decision", "决策")):
            inferred_rules.append("Consult the recorded decision before reopening a settled technical or governance choice; surface conflicts instead of silently replacing it.")
        if any(term in lower for term in ("exception", "waiver", "例外", "豁免")):
            inferred_rules.append("A governance exception must be explicit, scoped, attributable to the user decision, and must not silently become the default rule.")
        if any(term in lower for term in ("delivery", "gate", "acceptance", "交付", "验收")):
            inferred_rules.append("Do not declare delivery or release readiness without consulting this artifact and recording real validation evidence.")
        if any(term in lower for term in ("rollback", "migration", "回滚", "迁移")):
            inferred_rules.append("Consult this artifact before irreversible, migration, or rollback-sensitive changes.")
        if not inferred_rules:
            inferred_rules.append("Consult this artifact before work in the governance scope described by its purpose.")

    default_purpose = purpose.strip() if purpose.strip() else Path(normalized_path).stem.replace("_", " ").replace("-", " ").strip().title()
    return {
        "path": normalized_path,
        "recommended_kind": kind,
        "recommended_module": semantic_module,
        "recommended_lifecycle": "temporary" if kind == "runtime" else "permanent",
        "recommended_git_policy": "ignore" if kind == "runtime" else "tracked",
        "recommended_docker_policy": "exclude",
        "recommended_required": kind != "runtime",
        "recommended_expose_in_agents": kind == "governance",
        "recommended_expose_in_readme": kind != "runtime",
        "purpose": default_purpose or "Registered upgrade artifact",
        "inferred_rules": inferred_rules,
        "ambiguous": ambiguous,
        "reason": reason,
        "evidence": {
            "governance_terms": governance_hits,
            "runtime_terms": runtime_hits,
        },
    }


def _auto_choice(value: str | None, recommended: str) -> str:
    return recommended if not value or value == "auto" else value


def _auto_bool(value: str | None, recommended: bool) -> bool:
    if not value or value == "auto":
        return recommended
    return value == "yes"


def build_artifact_policy_entry(args: argparse.Namespace, classification: dict[str, Any]) -> dict[str, Any]:
    kind = _auto_choice(args.kind, classification["recommended_kind"])
    module = _auto_choice(args.module, classification["recommended_module"])
    lifecycle = _auto_choice(args.lifecycle, classification["recommended_lifecycle"])
    git_policy = _auto_choice(args.git_policy, classification["recommended_git_policy"])
    docker_policy = _auto_choice(args.docker_policy, classification["recommended_docker_policy"])
    required = _auto_bool(args.required, classification["recommended_required"])
    expose_in_agents = _auto_bool(args.agents_link, classification["recommended_expose_in_agents"])
    expose_in_readme = _auto_bool(args.readme_link, classification["recommended_expose_in_readme"])
    custom_rules = [_single_line_policy_text(rule, "rule", allow_empty=False) for rule in (args.rule or [])]
    rules = list(dict.fromkeys([*classification["inferred_rules"], *custom_rules])) if kind == "governance" else custom_rules
    entry = {
        "path": classification["path"],
        "kind": kind,
        "module": module,
        "lifecycle": lifecycle,
        "git_policy": git_policy,
        "docker_policy": docker_policy,
        "required": required,
        "expose_in_agents": expose_in_agents,
        "expose_in_readme": expose_in_readme,
        "purpose": _single_line_policy_text(args.purpose or classification["purpose"], "purpose", allow_empty=False),
        "rules": rules,
        "classifier_reason": classification["reason"],
    }
    return validate_artifact_policy_entry(entry)


def artifact_stub_text(entry: dict[str, Any]) -> str:
    path = Path(entry["path"])
    suffix = path.suffix.casefold()
    if suffix == ".md":
        title = path.stem.replace("_", " ").replace("-", " ").strip().title() or "Upgrade Artifact"
        return (
            f"# {title}\n\n"
            f"## Purpose\n\n{entry['purpose']}\n\n"
            "## Status\n\nNot yet completed.\n\n"
            "## Evidence / Notes\n\n- Add factual evidence and decisions here; do not fabricate validation or approval.\n"
        )
    if suffix in {".txt", ".rst"}:
        return f"{entry['purpose']}\n"
    return ""


def escape_dockerignore_literal(value: str) -> str | None:
    if any(character in value for character in ("\x00", "\n", "\r")):
        return None
    escaped: list[str] = []
    for character in value:
        if character in {"\\", "*", "?", "[", "]"}:
            escaped.append("\\" + character)
        else:
            escaped.append(character)
    return "".join(escaped)


def render_registered_exclusion_block(
    registry: dict[str, Any],
    *,
    policy_key: str,
    excluded_value: str,
    base_block: str,
    escape: Callable[[str], str | None],
) -> str:
    rules = [
        rule
        for entry in validate_artifact_registry(registry)["entries"]
        if entry[policy_key] == excluded_value
        if (rule := escape("/" + entry["path"]))
    ]
    lines = base_block.splitlines()
    end = lines.pop() if lines and lines[-1] == IGNORE_END else IGNORE_END
    if rules:
        lines.extend(["# Registered artifact policy exclusions", *sorted(set(rules), key=str.casefold)])
    lines.append(end)
    return "\n".join(lines)


def render_gitignore_block(registry: dict[str, Any]) -> str:
    return render_registered_exclusion_block(
        registry,
        policy_key="git_policy",
        excluded_value="ignore",
        base_block=GITIGNORE_BLOCK,
        escape=escape_gitignore_literal,
    )


def render_dockerignore_block(registry: dict[str, Any]) -> str:
    return render_registered_exclusion_block(
        registry,
        policy_key="docker_policy",
        excluded_value="exclude",
        base_block=DOCKERIGNORE_BLOCK,
        escape=escape_dockerignore_literal,
    )
