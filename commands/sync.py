"""Workspace synchronization command."""
from __future__ import annotations
import argparse
from pathlib import Path
from core.constants import *
from core.audit import Result
from core.errors import CliError
from core.artifacts import artifact_registry_digest, load_artifact_registry
from core.authority import authority_registry_digest, load_authority_registry, plan_authority_view_update
from core.config import load_workspace_config, new_workspace_config, write_workspace_config
from core.schemas import ordered_modules
from core.workspace import ensure_workspace_structure, preflight_mutation_workspace
from ._runtime import atomic_write, now_iso, finalize_mutation
from ._bridges import plan_governance_contract_update, plan_workspace_readme_update, plan_guardrail_updates, sync_guardrails, sync_planned_texts

def sync(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade = preflight_mutation_workspace(root)
    config = load_workspace_config(upgrade, allow_missing=True, allow_previous=True)
    registry = load_artifact_registry(upgrade, allow_missing=True)
    registry_digest = artifact_registry_digest(registry)
    authority_registry = load_authority_registry(upgrade, allow_missing=True)
    authority_digest = authority_registry_digest(authority_registry)
    if config is None:
        if registry["entries"]:
            raise CliError(
                f"existing fixed-footprint workspace has a non-empty {ARTIFACT_REGISTRY_NAME} without managed config provenance; "
                "reconcile it before registration"
            )
        config = new_workspace_config(
            initial_profile="legacy-governed",
            active_modules=list(PROFILE_MODULES["governed"]),
            selection_source="legacy-adopted",
            clock=now_iso,
        )
        result.warnings.append(
            f"existing fixed-footprint workspace has no {WORKSPACE_CONFIG_NAME}; sync is registering it as Governed"
        )
    else:
        config = dict(config)
        recorded_digest = config.get("artifact_registry_sha256")
        if recorded_digest is not None and recorded_digest != registry_digest:
            raise CliError(
                f"{ARTIFACT_REGISTRY_NAME} changed outside the approved artifact-policy lifecycle; "
                "restore/reconcile the registry before sync"
            )
        recorded_authority_digest = config.get("authority_registry_sha256")
        if recorded_authority_digest is not None and recorded_authority_digest != authority_digest:
            raise CliError(
                f"{AUTHORITY_REGISTRY_NAME} changed outside the approved authority lifecycle; "
                "restore/reconcile the registry before sync"
            )
        if config.get("schema_version") != WORKSPACE_CONFIG_SCHEMA_VERSION:
            config["schema_version"] = WORKSPACE_CONFIG_SCHEMA_VERSION
            config["authority_registry_sha256"] = authority_digest
            config["updated_at"] = now_iso()
            result.warnings.append(
                f"migrated {WORKSPACE_CONFIG_NAME} to schema {WORKSPACE_CONFIG_SCHEMA_VERSION}; "
                "project authority candidates remain advisory until authority-review/authority-apply"
            )

    planned_contract = plan_governance_contract_update(upgrade, registry)
    planned_readme = plan_workspace_readme_update(upgrade, registry)
    planned_authority_view = plan_authority_view_update(upgrade, authority_registry)
    planned_guardrails = plan_guardrail_updates(
        root,
        create_root_bridge=args.create_root_bridge,
        sync_nested_bridges=args.sync_nested_bridges,
        registry=registry,
    )
    enabled = set(args.enable_module or [])
    if enabled:
        config["active_modules"] = ordered_modules(set(config["active_modules"]) | enabled)
        config["updated_at"] = now_iso()
    if config.get("last_synced_skill_version") != SKILL_VERSION or config.get("last_synced_policy_version") != POLICY_VERSION:
        config["last_synced_skill_version"] = SKILL_VERSION
        config["last_synced_policy_version"] = POLICY_VERSION
        config["updated_at"] = now_iso()
    config["artifact_registry_sha256"] = registry_digest
    config["authority_registry_sha256"] = authority_digest
    ensure_workspace_structure(upgrade, result, args.dry_run, config["active_modules"], writer=atomic_write)
    sync_planned_texts(root, result, args.dry_run, planned_contract, planned_readme, planned_authority_view)
    write_workspace_config(upgrade, config, result, dry_run=args.dry_run, writer=atomic_write)
    sync_guardrails(root, result, args.dry_run, planned_guardrails)
    result.result["workspace"] = {
        "initial_profile": config["initial_profile"],
        "active_modules": config["active_modules"],
        "selection_source": config["selection_source"],
        "adaptive_bootstrap": True,
        "artifact_policies": len(registry["entries"]),
        "authority_sources": len(authority_registry["entries"]),
        "authority_registry_schema_version": AUTHORITY_REGISTRY_SCHEMA_VERSION,
    }
    if not args.dry_run:
        finalize_mutation(root, result)
