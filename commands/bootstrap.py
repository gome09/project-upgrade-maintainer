"""Bootstrap review and initialization commands."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any
from core.constants import *
from core.audit import Result
from core.errors import CliError
from core.durability import durable_mkdir
from core.schemas import ordered_modules
from core.paths import relative_posix
from core.artifacts import artifact_registry_digest, empty_artifact_registry, load_artifact_registry
from core.authority import authority_registry_digest, discover_authority_candidates, empty_authority_registry, load_authority_registry, plan_authority_view_update
from core.config import load_workspace_config, new_workspace_config, write_workspace_config
from core.workspace import ensure_workspace_structure, preflight_mutation_workspace, read_text_file
from ._runtime import atomic_write, now_iso, finalize_mutation
from ._inventory import git_inventory_paths, git_repo_root, ignored_collect_pathspecs, project_inventory
from ._common import walk_files
from ._workspace import modules_for_profile, normalized_profile_name
from ._bridges import plan_governance_contract_update, plan_workspace_readme_update, plan_guardrail_updates, sync_guardrails, sync_planned_texts, root_agent_instruction_files, nested_agent_instruction_files

def bootstrap_path_signals(root: Path, files: list[Path]) -> dict[str, Any]:
    relatives = [relative_posix(path, root) for path in files]
    lower_relatives = [relative.casefold() for relative in relatives]
    names = [Path(relative).name.casefold() for relative in relatives]
    package_names = {
        "package.json", "pyproject.toml", "cargo.toml", "go.mod", "pom.xml",
        "build.gradle", "build.gradle.kts", "composer.json", "gemfile", "mix.exs",
    }
    package_files = [relative for relative in relatives if Path(relative).name.casefold() in package_names]
    package_dirs = sorted({str(Path(relative).parent).replace("\\", "/") for relative in package_files})
    root_package = root / "package.json"
    workspace_declared = False
    if root_package.is_file() and not root_package.is_symlink():
        try:
            if root_package.stat().st_size <= 1024 * 1024:
                package_payload = json.loads(read_text_file(root_package).text)
                workspace_declared = bool(package_payload.get("workspaces")) if isinstance(package_payload, dict) else False
        except (OSError, CliError, json.JSONDecodeError):
            pass
    monorepo = workspace_declared or len(package_dirs) >= 3
    ci = any(
        relative.startswith(".github/workflows/")
        or Path(relative).name.casefold() in {".gitlab-ci.yml", "azure-pipelines.yml", "jenkinsfile", "circle.yml"}
        or relative.startswith(".circleci/")
        for relative in lower_relatives
    )
    docker = any(
        name in {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", ".dockerignore"}
        for name in names
    )
    database = any(
        any(part in {"migration", "migrations", "migrate", "alembic", "prisma"} for part in Path(relative).parts)
        or relative.endswith("prisma/schema.prisma")
        or Path(relative).name.casefold() in {"alembic.ini", "liquibase.properties"}
        for relative in lower_relatives
    )
    infrastructure = any(
        relative.endswith(".tf")
        or any(part in {"terraform", "infra", "infrastructure", "k8s", "kubernetes", "helm", "deploy", "deployment"} for part in Path(relative).parts)
        for relative in lower_relatives
    )
    auth = any(any(part in {"auth", "authentication", "authorization", "oauth", "identity", "security"} for part in Path(relative).parts) for relative in lower_relatives)
    public_api = any(
        Path(relative).name.casefold() in {"openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml", "swagger.yml"}
        or any(part in {"api", "apis", "routes"} for part in Path(relative).parts)
        for relative in lower_relatives
    )
    ownership = any(Path(relative).name.casefold() in {"codeowners", "contributing.md", "contributing"} for relative in lower_relatives)
    languages: set[str] = set()
    extension_languages = {
        ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
        ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".cs": "C#",
        ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".scala": "Scala", ".cpp": "C++", ".cc": "C++", ".c": "C",
    }
    for relative in relatives:
        language = extension_languages.get(Path(relative).suffix.casefold())
        if language:
            languages.add(language)
    root_instructions = [relative_posix(path, root) for path in root_agent_instruction_files(root)]
    nested_instructions = [relative_posix(path, root) for path in nested_agent_instruction_files(root)]
    authority_candidates = discover_authority_candidates(root, files)
    upgrade_artifacts = [
        relative for relative in relatives
        if SIGNALS.search(Path(relative).name) and not relative.startswith(".upgrade/")
    ]
    return {
        "file_count": len(files),
        "package_files": package_files,
        "package_count": len(package_dirs),
        "monorepo": monorepo,
        "workspace_declared": workspace_declared,
        "languages": sorted(languages),
        "ci": ci,
        "docker": docker,
        "database_migration": database,
        "infrastructure": infrastructure,
        "auth_security": auth,
        "public_api": public_api,
        "ownership_rules": ownership,
        "root_agent_instructions": root_instructions,
        "nested_agent_instructions": nested_instructions,
        "authority_candidate_count": len(authority_candidates),
        "authority_candidates": authority_candidates,
        "upgrade_artifact_candidates": len(upgrade_artifacts),
        "upgrade_artifact_examples": upgrade_artifacts[:10],
    }


def bootstrap_recommendation(signals: dict[str, Any]) -> tuple[str, str, int, list[str], list[str]]:
    score = 0
    reasons: list[str] = []
    against: list[str] = []
    file_count = int(signals["file_count"])
    package_count = int(signals["package_count"])
    nested_count = len(signals["nested_agent_instructions"])
    if file_count >= 2000:
        score += 3; reasons.append(f"large repository ({file_count} files in the filtered project inventory)")
    elif file_count >= 500:
        score += 2; reasons.append(f"moderate repository size ({file_count} files)")
    elif file_count >= 150:
        score += 1; reasons.append(f"non-trivial repository size ({file_count} files)")
    if signals["monorepo"]:
        score += 2; reasons.append("monorepo/workspace structure detected")
    elif package_count >= 2:
        score += 1; reasons.append(f"multiple package roots detected ({package_count})")
    if signals["ci"]:
        score += 1; reasons.append("CI configuration detected")
    if signals["docker"]:
        score += 1; reasons.append("Docker/container workflow detected")
    if signals["database_migration"]:
        score += 2; reasons.append("database migration/schema tooling detected")
    else:
        against.append("no database migration/schema signal detected")
    if signals["infrastructure"]:
        score += 2; reasons.append("infrastructure/deployment configuration detected")
    else:
        against.append("no infrastructure/deployment signal detected")
    if signals["auth_security"]:
        score += 1; reasons.append("authentication/security surface detected")
    if signals["public_api"]:
        score += 1; reasons.append("public API surface detected")
    if nested_count >= 2:
        score += 2; reasons.append(f"multiple nested Agent instruction scopes detected ({nested_count})")
    elif nested_count == 1:
        score += 1; reasons.append("a nested Agent instruction scope was detected")
    if signals["ownership_rules"]:
        score += 1; reasons.append("repository ownership/contribution governance detected")
    if int(signals["upgrade_artifact_candidates"]) >= 8:
        score += 1; reasons.append("existing upgrade/migration artifact signals detected")
    if score >= 7 or (signals["database_migration"] and signals["infrastructure"]) or (signals["monorepo"] and signals["ci"] and signals["docker"]):
        recommendation = "governed"
    elif score >= 3:
        recommendation = "standard"
    else:
        recommendation = "lean"
    if score <= 1 or score >= 9:
        confidence = "high"
    elif score in {2, 3, 6, 7, 8}:
        confidence = "medium"
    else:
        confidence = "medium"
    if not reasons:
        reasons.append("few repository complexity or governance signals were detected")
    if not signals["monorepo"]:
        against.append("no monorepo/workspace signal detected")
    if nested_count == 0:
        against.append("no nested Agent instruction scopes detected")
    return recommendation, confidence, score, reasons, against


def bootstrap_review(root: Path, args: argparse.Namespace, result: Result) -> None:
    # Deliberately read-only: no .upgrade directory, plan file, bridge, or config is created.
    files = project_inventory(root)
    signals = bootstrap_path_signals(root, files)
    recommendation, confidence, score, reasons, against = bootstrap_recommendation(signals)
    existing_config: dict[str, Any] | None = None
    upgrade = root / ".upgrade"
    if upgrade.is_dir() and not upgrade.is_symlink():
        try:
            existing_config = load_workspace_config(upgrade, allow_missing=True)
        except CliError as exc:
            result.warnings.append(str(exc))
    result.result = {
        "read_only": True,
        "workspace_exists": upgrade.exists() or upgrade.is_symlink(),
        "current_config": existing_config,
        "project_signals": signals,
        "recommendation": {
            "profile": recommendation,
            "confidence": confidence,
            "heuristic_score": score,
            "reasons": reasons,
            "factors_against_governed": against,
        },
        "profiles": {
            profile: {"modules": list(modules)} for profile, modules in PROFILE_MODULES.items()
        },
        "customization": {
            "always_enabled": ["core"],
            "available_modules": list(MODULE_ORDER),
            "note": "Use --profile custom with repeated --module values; core is always enabled.",
        },
        "authority_discovery": {
            "candidate_count": len(signals["authority_candidates"]),
            "candidates": signals["authority_candidates"],
            "note": "Candidates are advisory only; initialize does not grant project authority. Use authority-review after initialization.",
        },
        "suggested_commands": {
            "recommended": f"initialize --profile {recommendation}",
            "lean": "initialize --profile lean",
            "standard": "initialize --profile standard",
            "governed": "initialize --profile governed",
            "custom_example": "initialize --profile custom --module planning --module validation",
            "authority_review_after_initialize": "authority-review",
        },
    }


def initialize(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root, allow_missing=True)
    dry_run = args.dry_run
    existed = upgrade.exists()
    existing_config = load_workspace_config(upgrade, allow_missing=True, allow_previous=True) if existed else None
    registry = load_artifact_registry(upgrade, allow_missing=True) if existed else empty_artifact_registry()
    registry_digest = artifact_registry_digest(registry)
    authority_registry = load_authority_registry(upgrade, allow_missing=True) if existed else empty_authority_registry()
    authority_digest = authority_registry_digest(authority_registry)
    if existing_config and existing_config.get("artifact_registry_sha256") not in {None, registry_digest}:
        raise CliError(
            f"{ARTIFACT_REGISTRY_NAME} changed outside the approved artifact-policy lifecycle; "
            "restore/reconcile the registry before initialize or sync"
        )
    if existing_config and existing_config.get("authority_registry_sha256") not in {None, authority_digest}:
        raise CliError(
            f"{AUTHORITY_REGISTRY_NAME} changed outside the approved authority lifecycle; "
            "restore/reconcile the registry before initialize or sync"
        )

    # Plan all linked policy/bridge mutations before creating anything so marker
    # conflicts remain side-effect free.
    planned_contract = plan_governance_contract_update(upgrade, registry)
    planned_readme = plan_workspace_readme_update(upgrade, registry)
    planned_authority_view = plan_authority_view_update(upgrade, authority_registry)
    planned_guardrails = plan_guardrail_updates(
        root,
        create_root_bridge=args.create_root_bridge,
        sync_nested_bridges=args.sync_nested_bridges,
        registry=registry,
    )

    requested_profile = args.profile
    requested_modules = args.module or []
    if not existed:
        profile = normalized_profile_name(requested_profile)
        modules = modules_for_profile(requested_profile, requested_modules)
        if requested_profile is None:
            result.warnings.append(
                "no initialization profile was selected; using the safe Lean footprint. "
                "Run bootstrap-review first to obtain an evidence-based recommendation."
            )
            selection_source = "safe-default"
        elif requested_profile == "default":
            selection_source = "legacy-default-alias"
        else:
            selection_source = "explicit"
        config = new_workspace_config(
            initial_profile=profile,
            active_modules=modules,
            selection_source=selection_source,
            clock=now_iso,
        )
    elif existing_config is None:
        if registry["entries"]:
            raise CliError(
                f"existing fixed-footprint workspace has a non-empty {ARTIFACT_REGISTRY_NAME} without managed config provenance; "
                "reconcile it before registration"
            )
        # A workspace without managed config provenance may use the fixed full structure.
        # Preserve its semantics
        # unless the user explicitly chooses a new adaptive profile.
        if requested_profile is None:
            profile = "legacy-governed"
            modules = list(PROFILE_MODULES["governed"])
            selection_source = "legacy-adopted"
            result.warnings.append(
                f"existing fixed-footprint workspace has no {WORKSPACE_CONFIG_NAME}; registering it as Governed"
            )
        else:
            profile = normalized_profile_name(requested_profile)
            modules = modules_for_profile(requested_profile, requested_modules)
            selection_source = "explicit-legacy-adoption"
        config = new_workspace_config(
            initial_profile=profile,
            active_modules=modules,
            selection_source=selection_source,
            clock=now_iso,
        )
    else:
        config = dict(existing_config)
        if config.get("schema_version") != WORKSPACE_CONFIG_SCHEMA_VERSION:
            config["schema_version"] = WORKSPACE_CONFIG_SCHEMA_VERSION
            config["authority_registry_sha256"] = authority_digest
            config["updated_at"] = now_iso()
            result.warnings.append(
                f"migrating {WORKSPACE_CONFIG_NAME} to schema {WORKSPACE_CONFIG_SCHEMA_VERSION}; "
                "external authority candidates remain unregistered until authority-review/authority-apply"
            )
        modules = list(config["active_modules"])
        if requested_profile is not None:
            selected = modules_for_profile(requested_profile, requested_modules)
            modules = ordered_modules(set(modules) | set(selected))
            if modules != config["active_modules"]:
                config["active_modules"] = modules
                config["updated_at"] = now_iso()
        elif requested_modules:
            raise CliError("--module is valid only with --profile custom")
        if config.get("last_synced_skill_version") != SKILL_VERSION or config.get("last_synced_policy_version") != POLICY_VERSION:
            config["last_synced_skill_version"] = SKILL_VERSION
            config["last_synced_policy_version"] = POLICY_VERSION
            config["updated_at"] = now_iso()

    config["artifact_registry_sha256"] = registry_digest
    config["authority_registry_sha256"] = authority_digest
    if not upgrade.exists():
        if not dry_run:
            durable_mkdir(upgrade)
        result.change(".upgrade", "would_create" if dry_run else "created")
    ensure_workspace_structure(upgrade, result, dry_run, config["active_modules"], writer=atomic_write)
    sync_planned_texts(root, result, dry_run, planned_contract, planned_readme, planned_authority_view)
    write_workspace_config(upgrade, config, result, dry_run=dry_run, writer=atomic_write)
    sync_guardrails(root, result, dry_run, planned_guardrails)
    result.result["workspace"] = {
        "initial_profile": config["initial_profile"],
        "active_modules": config["active_modules"],
        "selection_source": config["selection_source"],
        "adaptive_bootstrap": True,
        "artifact_policies": len(registry["entries"]),
        "authority_sources": len(authority_registry["entries"]),
        "authority_registry_schema_version": AUTHORITY_REGISTRY_SCHEMA_VERSION,
    }
    if not dry_run:
        finalize_mutation(root, result)
