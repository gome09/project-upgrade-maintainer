"""Read-only diagnostics commands, including version, audits, and workspace health."""
from __future__ import annotations
import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from core.constants import *
from core.artifacts import artifact_registry_digest, empty_artifact_registry, load_artifact_registry
from core.authority import authority_registry_digest, authority_view_text, discover_authority_candidates, empty_authority_registry, load_authority_registry
from core.config import load_workspace_config, module_resources
from core.audit import Result
from core.errors import CliError
from core.paths import normalize_focuses, normalize_user_path, relative_posix, require_within, verified_upgrade
from core.workspace import read_text_file, section_value, update_marker, validate_marker_pair
from ._runtime import now_iso
from ._common import scan_files, walk_files
from ._inventory import audit_entries, git_repo_root, git_snapshot, project_inventory
from ._artifacts import artifact_policy_agents_block, artifact_policy_readme_block, governance_contract_text, render_dockerignore_block, render_gitignore_block, render_registered_exclusion_block
from ._bridges import agent_bridge_status, nested_agent_instruction_files, root_agent_instruction_files
from ._governance import cleanup_thresholds, current_phase
from ._delivery_state import active_decisions, latest_risks, current_exceptions, exception_expired
from ._delivery_policy import load_delivery_policy
from ._governance_records import read_json_object, read_jsonl_objects
from core.schemas import validate_delivery_record, validate_validation_history
from core.transactions import unresolved_transactions

def artifact_audit(root: Path, args: argparse.Namespace, result: Result) -> None:
    entries, snapshot = audit_entries(root, args.focus)
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["classification"]] = counts.get(entry["classification"], 0) + 1
    result.result = {
        "entries": entries,
        "summary": dict(sorted(counts.items())),
        "git": {"available": snapshot["available"], "repo_root": snapshot["repo_root"]},
    }
    if snapshot["repo_root"] and Path(snapshot["repo_root"]).resolve() != root.resolve():
        result.warnings.append(f"--root differs from Git repository root: {snapshot['repo_root']}")


def git_audit(root: Path, args: argparse.Namespace, result: Result) -> None:
    entries, snapshot = audit_entries(root, args.focus)
    if getattr(args, "include_artifacts", False):
        result.result["artifact_entries"] = entries
    if not snapshot["available"]:
        result.status = "warning"
        result.warnings.append("repository is not a readable Git work tree")
        result.result.update({"entries": [], "summary": {}, "repo_root": snapshot["repo_root"]})
        return
    findings = [entry for entry in entries if entry["git_mismatch"] or entry["classification"] == "sensitive"]
    summary: dict[str, int] = {}
    for entry in findings:
        key = f"{entry['classification']}:{entry['actual_git_state']}"
        summary[key] = summary.get(key, 0) + 1
    result.result.update({"entries": findings, "summary": dict(sorted(summary.items())), "repo_root": snapshot["repo_root"]})
    if Path(snapshot["repo_root"]).resolve() != root.resolve():
        result.warnings.append(f"--root differs from Git repository root: {snapshot['repo_root']}")
    if findings:
        result.status = "warning"
        result.warnings.append("Git lifecycle mismatches require review")


def workspace_reference_issues(root: Path, upgrade: Path, text: str) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    unsafe: list[str] = []
    references = sorted(set(re.findall(r"`(\.upgrade/[^`]+)`", text)))
    for reference in references:
        target = root / normalize_user_path(reference)
        try:
            require_within(target, upgrade, "workspace reference")
        except CliError:
            unsafe.append(reference)
            continue
        if not target.exists():
            missing.append(reference)
    return missing, unsafe


def _health_workspace_context(
    root: Path,
    upgrade: Path,
    normal: list[str],
    warnings: list[str],
    errors: list[str],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any], dict[str, Any]]:
    workspace: dict[str, Any] = {"config_status": "unknown", "active_modules": [], "inactive_modules": []}
    try:
        config = load_workspace_config(upgrade, allow_missing=True, allow_previous=True)
        if config is None:
            errors.append(f"adaptive workspace configuration is missing: .upgrade/{WORKSPACE_CONFIG_NAME}")
            workspace["config_status"] = "missing"
            active_modules = ["core"]
        else:
            active_modules = list(config["active_modules"])
            config_current = config.get("schema_version") == WORKSPACE_CONFIG_SCHEMA_VERSION
            workspace.update({
                "config_status": "current" if config_current else "migration-required",
                "config_schema_version": config.get("schema_version"),
                "initial_profile": config["initial_profile"],
                "selection_source": config["selection_source"],
                "active_modules": active_modules,
                "inactive_modules": [module for module in MODULE_ORDER if module not in active_modules],
            })
            if config_current:
                normal.append(
                    f"adaptive workspace config is valid ({config['initial_profile']}; modules: {', '.join(active_modules)})"
                )
            else:
                warnings.append(
                    f"workspace config schema {config.get('schema_version')} requires sync migration to schema {WORKSPACE_CONFIG_SCHEMA_VERSION}"
                )
            if config.get("last_synced_skill_version") != SKILL_VERSION or config.get("last_synced_policy_version") != POLICY_VERSION:
                warnings.append("workspace config was last synchronized under an older Skill/policy version")
    except CliError as exc:
        config = None
        active_modules = ["core"]
        errors.append(str(exc))
        workspace["config_status"] = "invalid"

    registry: dict[str, Any]
    artifact_registry_health: dict[str, Any] = {"status": "unknown", "entries": []}
    try:
        registry = load_artifact_registry(upgrade)
        digest = artifact_registry_digest(registry)
        artifact_registry_health = {
            "status": "current",
            "sha256": digest,
            "entries": [entry["path"] for entry in registry["entries"]],
        }
        normal.append(f"artifact policy registry is valid ({len(registry['entries'])} registered artifact(s))")
        if config is not None:
            recorded = config.get("artifact_registry_sha256")
            if recorded is None:
                errors.append("workspace config lacks artifact registry provenance; run sync before further mutations")
                artifact_registry_health["status"] = "unprovenanced"
            elif recorded != digest:
                errors.append(f"{ARTIFACT_REGISTRY_NAME} changed outside the approved artifact-policy lifecycle")
                artifact_registry_health["status"] = "drifted"
    except CliError as exc:
        registry = empty_artifact_registry()
        artifact_registry_health = {"status": "invalid", "detail": str(exc), "entries": []}
        errors.append(str(exc))
    workspace["artifact_registry"] = artifact_registry_health

    for entry in registry["entries"]:
        try:
            artifact = require_within(root / entry["path"], upgrade, "registered artifact")
        except CliError as exc:
            errors.append(str(exc))
            continue
        if entry["module"] not in active_modules:
            errors.append(f"registered artifact requires inactive module {entry['module']}: {entry['path']}")
        if artifact.is_symlink():
            errors.append(f"registered artifact must not be a symlink: {entry['path']}")
        elif entry["required"] and not artifact.is_file():
            errors.append(f"required registered artifact is missing: {entry['path']}")
        elif artifact.is_file():
            normal.append(f"registered artifact exists: {entry['path']}")

    return config, active_modules, registry, workspace


def _health_state_and_module_resources(
    root: Path,
    upgrade: Path,
    config: dict[str, Any] | None,
    active_modules: list[str],
    normal: list[str],
    warnings: list[str],
    errors: list[str],
) -> int:
    state = upgrade / "STATE.md"
    if not state.is_file():
        errors.append("STATE.md is missing")
        phase = 0
    else:
        require_within(state, upgrade, "STATE.md")
        normal.append("STATE.md exists")
        state_text = read_text_file(state).text
        try:
            phase = current_phase(upgrade)
        except CliError as exc:
            phase = 0
            errors.append(str(exc))
        missing, unsafe = workspace_reference_issues(root, upgrade, state_text)
        warnings.extend(f"STATE.md references a missing path: {reference}" for reference in missing)
        errors.extend(f"STATE.md contains an unsafe workspace reference: {reference}" for reference in unsafe)

    required_dirs, required_templates, _required_placeholders = module_resources(active_modules)
    for directory in required_dirs:
        candidate = upgrade / directory
        if candidate.is_symlink():
            errors.append(f"enabled-module workspace directory must not be a symlink: .upgrade/{directory}")
        elif not candidate.is_dir():
            errors.append(f"enabled-module workspace directory is missing: .upgrade/{directory}")
    legacy_config = config is not None and config.get("schema_version") != WORKSPACE_CONFIG_SCHEMA_VERSION
    for relative in required_templates.values():
        if legacy_config and relative in {AUTHORITY_REGISTRY_NAME, AUTHORITY_VIEW_NAME}:
            continue
        candidate = upgrade / relative
        if candidate.is_symlink():
            errors.append(f"enabled-module workspace file must not be a symlink: .upgrade/{relative}")
        elif not candidate.is_file():
            errors.append(f"enabled-module workspace file is missing: .upgrade/{relative}")
    config_candidate = upgrade / WORKSPACE_CONFIG_NAME
    if config is not None and not config_candidate.is_file():
        errors.append(f"required workspace file is missing: .upgrade/{WORKSPACE_CONFIG_NAME}")
    if phase > 0 and "phase" not in active_modules:
        errors.append("STATE.md has an active phase but the phase module is not enabled")
    return phase


def _health_authority_network(
    root: Path,
    upgrade: Path,
    config: dict[str, Any] | None,
    normal: list[str],
    warnings: list[str],
    errors: list[str],
    governance_actions: list[str],
) -> dict[str, Any]:
    state: dict[str, Any] = {"status": "unknown", "entries": [], "candidates": []}
    legacy_config = config is not None and config.get("schema_version") != WORKSPACE_CONFIG_SCHEMA_VERSION
    try:
        registry = load_authority_registry(upgrade, allow_missing=legacy_config)
        digest = authority_registry_digest(registry)
        state["sha256"] = digest
        state["entries"] = [entry["path"] for entry in registry["entries"]]
        if legacy_config:
            state["status"] = "migration-required"
            warnings.append("authority registry provenance requires sync migration before governed authority mutations")
            governance_actions.append("run sync to create the managed authority registry and view")
        else:
            recorded = config.get("authority_registry_sha256") if config else None
            if recorded != digest:
                state["status"] = "drifted"
                errors.append(f"{AUTHORITY_REGISTRY_NAME} changed outside the approved authority lifecycle")
                governance_actions.append("restore/reconcile .upgrade/AUTHORITY.json before further mutations")
            else:
                state["status"] = "current"
                normal.append(f"authority registry is valid ({len(registry['entries'])} registered source(s))")

        normative: dict[tuple[str, str], list[str]] = {}
        details: list[dict[str, Any]] = []
        registered_paths = {entry["path"] for entry in registry["entries"]}
        for entry in registry["entries"]:
            raw_target = root / entry["path"]
            target = require_within(raw_target, root, "authority source") if not raw_target.is_symlink() else raw_target
            status = "current"
            if raw_target.is_symlink():
                status = "unsafe"
                errors.append(f"registered authority source must not be a symlink: {entry['path']}")
            elif not target.is_file():
                status = "missing"
                message = f"registered authority source is missing: {entry['path']}"
                (errors if entry["required"] else warnings).append(message)
            else:
                from core.workspace import sha256_file
                if sha256_file(target) != entry["sha256"]:
                    status = "drifted"
                    warnings.append(f"registered authority source content changed and requires review: {entry['path']}")
                    governance_actions.append(f"run authority-review --accept {entry['path']} and approve the resulting plan if the new content is authoritative")
                else:
                    normal.append(f"registered authority source is current: {entry['path']}")
            details.append({"path": entry["path"], "status": status, "kind": entry["kind"], "scope": entry["scope"], "authority": entry["authority"]})
            if entry["authority"] != "reference" and status != "missing":
                normative.setdefault((entry["kind"], entry["scope"]), []).append(entry["path"])
        for (kind, scope), paths in sorted(normative.items()):
            if len(paths) > 1:
                errors.append(f"conflicting normative authority sources for kind={kind} scope={scope}: {', '.join(paths)}")
                governance_actions.append("run authority-review to reconcile conflicting normative sources")
        state["details"] = details

        view = upgrade / AUTHORITY_VIEW_NAME
        if view.is_symlink():
            errors.append(f".upgrade/{AUTHORITY_VIEW_NAME} must not be a symlink")
            state["view_status"] = "unsafe"
        elif not view.is_file():
            if legacy_config:
                warnings.append(f".upgrade/{AUTHORITY_VIEW_NAME} will be created by sync migration")
                state["view_status"] = "migration-required"
            else:
                errors.append(f"managed authority view is missing: .upgrade/{AUTHORITY_VIEW_NAME}")
                state["view_status"] = "missing"
                governance_actions.append(f"run sync to recreate .upgrade/{AUTHORITY_VIEW_NAME}")
        else:
            source = read_text_file(view).text
            if not source.startswith(AUTHORITY_MAP_MARKER + "\n"):
                errors.append(f".upgrade/{AUTHORITY_VIEW_NAME} is not owned by project-upgrade-maintainer")
                state["view_status"] = "unmanaged"
            elif source != authority_view_text(registry):
                warnings.append(f".upgrade/{AUTHORITY_VIEW_NAME} is stale relative to the approved authority registry")
                state["view_status"] = "drifted"
                governance_actions.append(f"run sync to refresh .upgrade/{AUTHORITY_VIEW_NAME}")
            else:
                normal.append("generated authority view is current")
                state["view_status"] = "current"

        state["candidates"] = [
            item for item in discover_authority_candidates(root, project_inventory(root))
            if item["path"] not in registered_paths
        ]
    except CliError as exc:
        state["status"] = "invalid"
        state["detail"] = str(exc)
        errors.append(str(exc))
    return state


def _health_governance_files(
    root: Path,
    upgrade: Path,
    registry: dict[str, Any],
    normal: list[str],
    warnings: list[str],
    errors: list[str],
    governance: dict[str, Any],
    governance_actions: list[str],
) -> None:
    contract = upgrade / "AGENTS.md"
    if contract.is_symlink():
        errors.append(".upgrade/AGENTS.md must be a real managed file, not a symlink")
        governance["contract"] = {"status": "unsafe"}
        governance_actions.append("replace the unsafe .upgrade/AGENTS.md link and run sync")
    elif not contract.is_file():
        errors.append("canonical governance contract is missing: .upgrade/AGENTS.md")
        governance["contract"] = {"status": "missing"}
        governance_actions.append("run sync to recreate .upgrade/AGENTS.md")
    else:
        try:
            contract_source = read_text_file(contract)
            if not contract_source.text.startswith(GOVERNANCE_CONTRACT_MARKER + "\n"):
                errors.append(".upgrade/AGENTS.md is not owned by project-upgrade-maintainer")
                governance["contract"] = {"status": "unmanaged"}
                governance_actions.append("reconcile the unmanaged .upgrade/AGENTS.md before running sync")
            elif contract_source.text != governance_contract_text(registry):
                errors.append(".upgrade/AGENTS.md governance contract has drifted from the current Skill policy")
                governance["contract"] = {"status": "drifted"}
                governance_actions.append("run sync to refresh the canonical governance contract")
            else:
                normal.append("canonical .upgrade/AGENTS.md governance contract is current")
                governance["contract"] = {"status": "current"}
        except CliError as exc:
            errors.append(str(exc))
            governance["contract"] = {"status": "invalid", "detail": str(exc)}

    readme = upgrade / "README.md"
    if readme.is_symlink():
        errors.append(".upgrade/README.md must not be a symlink")
        governance_actions.append("replace the unsafe workspace README and run sync")
    elif readme.is_file():
        try:
            readme_source = read_text_file(readme)
            expected_readme = update_marker(
                readme_source.text,
                README_ARTIFACT_START,
                README_ARTIFACT_END,
                artifact_policy_readme_block(registry),
            )
            if expected_readme != readme_source.text:
                warnings.append(".upgrade/README.md registered-artifact index is stale")
                governance_actions.append("run sync to refresh the workspace artifact index")
            else:
                normal.append("workspace README artifact index is current")
        except CliError as exc:
            errors.append(str(exc))

    gitignore = root / ".gitignore"
    if gitignore.is_symlink():
        errors.append("managed .gitignore must not be a symlink")
    elif gitignore.is_file():
        try:
            source = read_text_file(gitignore)
            expected = update_marker(source.text, IGNORE_START, IGNORE_END, render_gitignore_block(registry))
            if expected != source.text:
                errors.append(".gitignore managed artifact-policy linkage is stale")
                governance_actions.append("run sync to refresh registered artifact ignore rules")
            else:
                normal.append(".gitignore registered-artifact linkage is current")
        except CliError as exc:
            errors.append(str(exc))

    docker_evidence = (root / ".dockerignore").exists() or any(
        (root / name).exists() for name in ("Dockerfile", "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
    )
    if docker_evidence:
        dockerignore = root / ".dockerignore"
        if dockerignore.is_symlink():
            errors.append("managed .dockerignore must not be a symlink")
        elif not dockerignore.is_file():
            warnings.append("Docker workflow detected but .dockerignore governance linkage is missing")
            governance_actions.append("run sync to create/update .dockerignore governance rules")
        else:
            try:
                source = read_text_file(dockerignore)
                expected = update_marker(source.text, IGNORE_START, IGNORE_END, render_dockerignore_block(registry))
                if expected != source.text:
                    warnings.append(".dockerignore managed artifact-policy linkage is stale")
                    governance_actions.append("run sync to refresh registered artifact Docker exclusions")
                else:
                    normal.append(".dockerignore registered-artifact linkage is current")
            except CliError as exc:
                errors.append(str(exc))


def _health_agent_bridges(
    root: Path,
    normal: list[str],
    warnings: list[str],
    errors: list[str],
    governance: dict[str, Any],
    governance_actions: list[str],
) -> None:
    root_instructions = root_agent_instruction_files(root)
    if not root_instructions:
        warnings.append("no root Agent instruction file exposes the .upgrade governance bridge")
        governance_actions.append("run sync --create-root-bridge to create a minimal repository AGENTS.md bridge")
    else:
        root_current = 0
        for instruction in root_instructions:
            relative = instruction.relative_to(root).as_posix()
            status, detail = agent_bridge_status(instruction)
            governance["root_bridges"].append({"path": relative, "status": status})
            if status == "current":
                root_current += 1
            elif status in {"unsafe", "conflict"}:
                errors.append(f"root Agent instruction bridge is invalid in {relative}: {detail or status}")
                governance_actions.append("repair root Agent instruction marker conflicts, then run sync")
            elif status == "missing":
                warnings.append(f"root Agent instruction bridge is missing in {relative}")
                governance_actions.append("run sync to refresh existing root Agent instruction bridges")
            elif status == "drifted":
                warnings.append(f"root Agent instruction bridge is stale in {relative}")
                governance_actions.append("run sync to refresh existing root Agent instruction bridges")
        if root_current == len(root_instructions):
            normal.append("all existing root Agent instruction bridges are current")

    nested_instructions = nested_agent_instruction_files(root)
    nested_current = 0
    for instruction in nested_instructions:
        relative = instruction.relative_to(root).as_posix()
        status, detail = agent_bridge_status(instruction)
        if status == "missing":
            governance["nested_bridge_candidates"].append(relative)
            continue
        governance["nested_bridges"].append({"path": relative, "status": status})
        if status == "current":
            nested_current += 1
        elif status == "conflict":
            errors.append(f"nested Agent instruction bridge marker conflict in {relative}: {detail}")
            governance_actions.append("repair nested Agent instruction marker conflicts before syncing nested bridges")
        elif status == "drifted":
            warnings.append(f"nested Agent instruction bridge is stale in {relative}")
            governance_actions.append("run sync --sync-nested-bridges to refresh nested governance bridges")
    if nested_current:
        normal.append(f"{nested_current} nested Agent instruction bridge(s) are current")


def _health_phase_lifecycle(
    root: Path,
    upgrade: Path,
    phase: int,
    normal: list[str],
    warnings: list[str],
    errors: list[str],
) -> None:
    manifest = upgrade / "MANIFEST.md"
    if manifest.is_file():
        try:
            validate_marker_pair(read_text_file(manifest).text, MANIFEST_START, MANIFEST_END)
        except CliError as exc:
            errors.append(str(exc))
    stage = require_within(upgrade / "stages" / f"phase-{phase}.md", upgrade, "phase artifact")
    previous_report = require_within(upgrade / "reports" / f"stage{phase}_report.md", upgrade, "previous-format phase artifact")
    if phase > 0 and (stage.exists() or previous_report.exists()):
        normal.append(f"current phase artifact exists for phase {phase}")
    elif phase > 0:
        errors.append(f"current phase artifact is missing for phase {phase}")
    checkpoint_dir = upgrade / "checkpoints"
    if checkpoint_dir.exists():
        require_within(checkpoint_dir, upgrade, "checkpoints directory")
    checkpoints = list(checkpoint_dir.glob(f"phase-{phase}.*")) if checkpoint_dir.is_dir() else []
    if phase > 0 and not checkpoints:
        warnings.append(f"no checkpoint found for phase {phase}")
    checkpoint_records: list[tuple[int, int, str]] = []
    if checkpoint_dir.is_dir():
        for checkpoint in scan_files(checkpoint_dir):
            match = re.search(r"phase-(\d+)", checkpoint.name, re.IGNORECASE)
            if match:
                checkpoint_records.append((checkpoint.stat().st_mtime_ns, int(match.group(1)), checkpoint.name))
    latest_checkpoint_phase = max(checkpoint_records, key=lambda item: (item[0], item[2]))[1] if checkpoint_records else None
    if latest_checkpoint_phase is not None and latest_checkpoint_phase != phase:
        errors.append(f"current phase {phase} does not match latest checkpoint phase {latest_checkpoint_phase}")
    elif phase > 0 and checkpoints:
        normal.append("current phase matches the latest checkpoint")

    phase_artifact = stage if stage.exists() else previous_report if previous_report.exists() else None
    if phase_artifact:
        try:
            phase_text = read_text_file(phase_artifact).text
            if stage.exists():
                status = (section_value(phase_text, "Status") or "").strip().casefold()
                if status and status != "in_progress":
                    errors.append(f"current phase artifact status is {status}, expected in_progress")
            missing, unsafe = workspace_reference_issues(root, upgrade, phase_text)
            if missing:
                warnings.extend(f"current phase artifact references a missing path: {reference}" for reference in missing)
            else:
                normal.append("current phase artifact has no missing local workspace references")
            errors.extend(f"current phase artifact contains an unsafe workspace reference: {reference}" for reference in unsafe)
        except CliError as exc:
            errors.append(str(exc))

    stages_dir = upgrade / "stages"
    if stages_dir.is_dir():
        for stage_path in scan_files(stages_dir):
            match = re.fullmatch(r"phase-(\d+)\.md", stage_path.name, re.IGNORECASE)
            if not match or int(match.group(1)) == phase:
                continue
            try:
                status = (section_value(read_text_file(stage_path).text, "Status") or "").strip().casefold()
            except CliError as exc:
                errors.append(str(exc))
                continue
            if status == "in_progress":
                errors.append(f"non-current phase artifact remains in_progress: .upgrade/stages/{stage_path.name}")


def _health_runtime_state(
    root: Path,
    upgrade: Path,
    phase: int,
    normal: list[str],
    warnings: list[str],
    errors: list[str],
) -> tuple[dict[str, Any], list[str]]:
    validation_records = upgrade / "validation" / "records.jsonl"
    if validation_records.exists():
        try:
            if validation_records.is_symlink() or not validation_records.is_file():
                raise CliError("validation records must be a regular file")
            validate_validation_history(read_jsonl_objects(validation_records, upgrade, "validation records"))
            normal.append("validation evidence integrity chain is valid")
        except CliError as exc:
            errors.append(f"validation records are invalid: {exc}")
    lock = upgrade / ".lock"
    if lock.exists():
        warnings.append("workspace lock exists; verify whether another mutation is active or the lock is stale")
    repo = git_repo_root(root)
    if repo is not None and repo.resolve() != root.resolve():
        warnings.append(f"--root differs from Git repository root: {repo}")
    thresholds = cleanup_thresholds(upgrade, phase)
    if thresholds["suggested"]:
        warnings.append("cleanup thresholds are exceeded")
    else:
        normal.append("cleanup thresholds are within limits")
    git_status: list[str] = []
    git = shutil.which("git")
    if repo is not None and git:
        completed = subprocess.run([git, "status", "--short"], cwd=root, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
        if completed.returncode == 0:
            git_status = completed.stdout.splitlines()
            if git_status:
                warnings.append("git working tree contains uncommitted changes")
            else:
                normal.append("git working tree is clean")
        else:
            warnings.append("git status could not be read")
    else:
        warnings.append("repository is not a readable Git work tree; git status was skipped")
    return thresholds, git_status



def _health_extended_governance(root: Path, upgrade: Path, normal: list[str], warnings: list[str], errors: list[str]) -> int:
    try:
        decisions = active_decisions(upgrade)
        risks = latest_risks(upgrade)
        exceptions = current_exceptions(upgrade)
        normal.append(f"governance records are valid (decisions={len(decisions)}, risks={len(risks)}, exceptions={len(exceptions)})")
        at = now_iso()
        for item in exceptions:
            if item["status"] == "active" and exception_expired(item, at):
                warnings.append(f"active exception is expired: {item['exception_id']}")
    except CliError as exc:
        errors.append(str(exc))
    try:
        policy = load_delivery_policy(upgrade)
        if policy is None:
            normal.append("delivery policy is not configured; delivery gates use explicitly requested checks")
        else:
            normal.append(f"delivery policy is valid ({policy['policy_id']}, profile={policy['profile']})")
    except CliError as exc:
        errors.append(str(exc))

    try:
        raw = upgrade / "delivery"
        if raw.is_symlink():
            raise CliError("delivery directory must not be a symlink")
        directory = require_within(raw, upgrade, "delivery directory")
        count = 0
        if directory.exists():
            if not directory.is_dir():
                raise CliError("delivery directory is not a directory")
            for path in sorted(directory.glob("DLV-*.json")):
                record = validate_delivery_record(read_json_object(path, upgrade, "delivery record"))
                if path.name != f"{record['delivery_id']}.json":
                    raise CliError(f"delivery filename does not match record id: {path.name}")
                count += 1
        normal.append(f"delivery records are valid ({count})")
    except CliError as exc:
        errors.append(str(exc))
    try:
        unresolved = unresolved_transactions(root)
        if unresolved:
            warnings.append(f"{len(unresolved)} unresolved transaction(s) require recovery")
        else:
            normal.append("no unresolved transactions")
        unresolved_count = len(unresolved)
    except CliError as exc:
        errors.append(str(exc))
        unresolved_count = 0
    return unresolved_count

def health_check(root: Path, args: argparse.Namespace, result: Result) -> None:
    upgrade_path = root / ".upgrade"
    normal: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    if not upgrade_path.is_dir():
        errors.append(".upgrade directory is missing")
        result.status = "error"
        result.result = {"正常项": normal, "警告项": warnings, "错误项": errors, "建议动作": ["run bootstrap-review, choose a profile/modules, then run initialize"]}
        if getattr(args, "ci", False) or getattr(args, "strict", False):
            result.result["ci"] = {"mode": "strict" if getattr(args, "strict", False) else "ci", "recovery_required": False, "unresolved_transactions": 0, "contract": {"ok": 0, "error": 5, "recovery_required": 6, "strict_warning": 7}}
        return
    try:
        upgrade = verified_upgrade(root)
    except CliError as exc:
        result.status = "error"
        result.errors.append(str(exc))
        result.result = {"正常项": normal, "警告项": warnings, "错误项": result.errors, "建议动作": ["replace the unsafe .upgrade link with a local directory"]}
        if getattr(args, "ci", False) or getattr(args, "strict", False):
            result.result["ci"] = {"mode": "strict" if getattr(args, "strict", False) else "ci", "recovery_required": False, "unresolved_transactions": 0, "contract": {"ok": 0, "error": 5, "recovery_required": 6, "strict_warning": 7}}
        return

    config, active_modules, registry, workspace = _health_workspace_context(
        root, upgrade, normal, warnings, errors
    )
    phase = _health_state_and_module_resources(
        root, upgrade, config, active_modules, normal, warnings, errors
    )

    governance_actions: list[str] = []
    governance: dict[str, Any] = {
        "contract": {},
        "authority": {},
        "root_bridges": [],
        "nested_bridges": [],
        "nested_bridge_candidates": [],
    }
    governance["authority"] = _health_authority_network(
        root, upgrade, config, normal, warnings, errors, governance_actions
    )
    _health_governance_files(
        root, upgrade, registry, normal, warnings, errors, governance, governance_actions
    )
    _health_agent_bridges(root, normal, warnings, errors, governance, governance_actions)
    _health_phase_lifecycle(root, upgrade, phase, normal, warnings, errors)
    unresolved_transactions_count = _health_extended_governance(root, upgrade, normal, warnings, errors)
    thresholds, git_status = _health_runtime_state(root, upgrade, phase, normal, warnings, errors)

    actions = ["review each warning"] if warnings else ["no action required"]
    if errors:
        actions.insert(0, "repair errors before applying maintenance plans")
    for action in reversed(list(dict.fromkeys(governance_actions))):
        if action not in actions:
            actions.insert(0, action)
    result.status = "error" if errors else ("warning" if warnings else "ok")
    result.warnings.extend(warnings)
    result.errors.extend(errors)
    result.result = {
        "正常项": normal,
        "警告项": warnings,
        "错误项": errors,
        "建议动作": actions,
        "thresholds": thresholds,
        "git_status": git_status,
        "workspace": workspace,
        "governance": governance,
    }
    if getattr(args, "ci", False) or getattr(args, "strict", False):
        result.result["ci"] = {
            "mode": "strict" if getattr(args, "strict", False) else "ci",
            "recovery_required": unresolved_transactions_count > 0,
            "unresolved_transactions": unresolved_transactions_count,
            "contract": {"ok": 0, "error": 5, "recovery_required": 6, "strict_warning": 7},
        }


def version_info(root: Path, args: argparse.Namespace, result: Result) -> None:
    """Report the release and plan-compatibility contract without mutating the repository."""
    result.result = {
        "skill_version": SKILL_VERSION,
        "policy_version": POLICY_VERSION,
        "plan_schema_version": PLAN_SCHEMA_VERSION,
        "previous_plan_schema_versions": list(PREVIOUS_PLAN_SCHEMA_VERSIONS),
        "workspace_config_schema_version": WORKSPACE_CONFIG_SCHEMA_VERSION,
        "previous_workspace_config_schema_versions": list(PREVIOUS_WORKSPACE_CONFIG_SCHEMA_VERSIONS),
        "authority_registry_schema_version": AUTHORITY_REGISTRY_SCHEMA_VERSION,
        "artifact_registry_schema_version": ARTIFACT_REGISTRY_SCHEMA_VERSION,
        "decision_schema_version": DECISION_SCHEMA_VERSION,
        "risk_schema_version": RISK_SCHEMA_VERSION,
        "validation_schema_version": VALIDATION_SCHEMA_VERSION,
        "transaction_schema_version": TRANSACTION_SCHEMA_VERSION,
        "exception_schema_version": EXCEPTION_SCHEMA_VERSION,
        "delivery_schema_version": DELIVERY_SCHEMA_VERSION,
        "delivery_policy_schema_version": DELIVERY_POLICY_SCHEMA_VERSION,
        "health_ci_exit_codes": {"ok": 0, "error": 5, "recovery_required": 6, "strict_warning": 7},
        "plan_policy_scope": "per-kind-policy-dependencies",
    }
