from __future__ import annotations

import codecs
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


from cli.main import run as cli_run
from commands._artifacts import governance_contract_text
from commands._inventory import ignored_collect_pathspecs
from core.artifacts import artifact_registry_digest
from core import audit as core_audit
from core import constants as core_constants
from core import plans as core_plans
from core import policy as core_policy
from core import workspace as core_workspace
from core.errors import StalePlanError


SKILL_ROOT = Path(__file__).resolve().parents[1]
CLI = SKILL_ROOT / "scripts" / "upgrade_workspace.py"


class _UpgradeTestHarness:
    """In-process dependency-injection surface for behavioral tests only.

    The production compatibility script is intentionally a pure CLI entrypoint;
    tests patch this harness instead of requiring private helpers to be re-exported
    from scripts/upgrade_workspace.py.
    """

    SKILL_ROOT = core_constants.SKILL_ROOT
    SKILL_VERSION = core_constants.SKILL_VERSION
    POLICY_VERSION = core_constants.POLICY_VERSION
    PLAN_SCHEMA_VERSION = core_constants.PLAN_SCHEMA_VERSION
    PREVIOUS_PLAN_SCHEMA_VERSIONS = core_constants.PREVIOUS_PLAN_SCHEMA_VERSIONS
    MODULE_ORDER = core_constants.MODULE_ORDER
    PROFILE_MODULES = core_constants.PROFILE_MODULES
    TEMPLATES = core_constants.TEMPLATES
    GOVERNANCE_CONTRACT_MARKER = core_constants.GOVERNANCE_CONTRACT_MARKER
    GUARD_START = core_constants.GUARD_START
    GUARD_END = core_constants.GUARD_END
    AGENTS_ARTIFACT_START = core_constants.AGENTS_ARTIFACT_START
    README_ARTIFACT_START = core_constants.README_ARTIFACT_START
    StalePlanError = StalePlanError

    atomic_write = staticmethod(core_workspace.atomic_write)
    now_iso = staticmethod(core_audit.now_iso)
    plan_id = staticmethod(core_plans.plan_id)
    plan_policy_dependencies = staticmethod(core_policy.plan_policy_dependencies)
    replace_section = staticmethod(core_workspace.replace_section)
    governance_contract_text = staticmethod(governance_contract_text)
    ignored_collect_pathspecs = staticmethod(ignored_collect_pathspecs)
    artifact_registry_digest = staticmethod(artifact_registry_digest)

    def _implementation_digest_files(self, kind: str) -> tuple[str, ...]:
        try:
            current = Path(self.SKILL_ROOT).resolve()
            baseline = Path(core_constants.SKILL_ROOT).resolve()
        except OSError:
            return ()
        return core_policy.implementation_files_for_kind(kind) if current == baseline else ()

    def policy_implementation_digest(self, kind: str) -> str:
        return core_policy.policy_implementation_digest(
            kind,
            skill_root=Path(self.SKILL_ROOT),
            implementation_files=self._implementation_digest_files(kind),
        )

    def build_review_plan(self, root: Path, kind: str, entries: list[dict]) -> dict:
        return core_plans.build_review_plan(
            root,
            kind,
            entries,
            clock=self.now_iso,
            policy_digest=self.policy_implementation_digest,
        )

    def load_plan(self, path: Path) -> dict:
        return core_plans.load_plan(path, policy_digest=self.policy_implementation_digest)

    def main(self, argv: list[str] | None = None) -> int:
        return cli_run(
            argv,
            clock=self.now_iso,
            atomic_write_fn=self.atomic_write,
            skill_root=Path(self.SKILL_ROOT),
        )


UPGRADE = _UpgradeTestHarness()


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli_at(self, root: Path, *args: str, expected: int = 0, fmt: str = "json", env: dict[str, str] | None = None) -> tuple[dict, subprocess.CompletedProcess[str]]:
        argv = ["--root", str(root), "--format", fmt, *args]
        command = [sys.executable, "-X", "utf8", str(CLI), *argv]
        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = patch.dict(os.environ, env, clear=True) if env is not None else contextlib.nullcontext()
        with environment, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            returncode = UPGRADE.main(argv)
        completed = subprocess.CompletedProcess(command, returncode, stdout.getvalue(), stderr.getvalue())
        self.assertEqual(expected, completed.returncode, completed.stderr + completed.stdout)
        payload = json.loads(completed.stdout) if fmt == "json" and completed.stdout.strip() else {}
        return payload, completed

    def run_cli(self, *args: str, expected: int = 0, fmt: str = "json", env: dict[str, str] | None = None) -> tuple[dict, subprocess.CompletedProcess[str]]:
        return self.run_cli_at(self.root, *args, expected=expected, fmt=fmt, env=env)

    def initialize(self) -> dict:
        # Existing behavioral tests use the full profile so they continue to
        # exercise every legacy workspace surface. Adaptive-footprint tests call
        # initialize directly with an explicit profile.
        payload, _ = self.run_cli("initialize", "--profile", "governed")
        return payload

    def write_forged_plan(self, name: str, plan: dict) -> Path:
        payload = dict(plan)
        payload["schema_version"] = UPGRADE.PLAN_SCHEMA_VERSION
        payload["skill_version"] = UPGRADE.SKILL_VERSION
        payload["policy_version"] = UPGRADE.POLICY_VERSION
        kind = payload.get("kind")
        if not isinstance(kind, str):
            raise AssertionError("forged plan requires a kind")
        payload["policy_dependencies"] = list(UPGRADE.plan_policy_dependencies(kind))
        payload["implementation_sha256"] = UPGRADE.policy_implementation_digest(kind)
        payload.pop("plan_id", None)
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload["plan_id"] = hashlib.sha256(canonical).hexdigest()
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_round11_release_contract_and_version_command(self) -> None:
        self.assertEqual("18.0.0", UPGRADE.SKILL_VERSION)
        self.assertEqual(14, UPGRADE.POLICY_VERSION)
        self.assertEqual(3, UPGRADE.PLAN_SCHEMA_VERSION)
        self.assertEqual((2,), UPGRADE.PREVIOUS_PLAN_SCHEMA_VERSIONS)
        payload, _ = self.run_cli("version")
        self.assertEqual("18.0.0", payload["result"]["skill_version"])
        self.assertEqual(14, payload["result"]["policy_version"])
        self.assertEqual(3, payload["result"]["plan_schema_version"])
        self.assertEqual([2], payload["result"]["previous_plan_schema_versions"])
        self.assertEqual("per-kind-policy-dependencies", payload["result"]["plan_policy_scope"])
        self.assertFalse((self.root / ".upgrade").exists())

    def test_schema2_plan_is_explicitly_stale_and_requires_regeneration(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        legacy = self.root / "legacy-schema2-plan.json"
        legacy.write_text(json.dumps({
            "schema_version": 2,
            "skill_version": "9.0.0",
            "policy_version": 5,
            "kind": "collect",
            "root": str(self.root),
            "entries": [],
            "plan_id": "legacy",
        }), encoding="utf-8")
        payload, _ = self.run_cli(
            "collect-apply", "--plan", str(legacy), "--approve", "legacy", expected=3,
        )
        self.assertIn("plan schema 2 is obsolete", payload["errors"][0])
        self.assertIn("current schema is 3", payload["errors"][0])
        self.assertIn("regenerate the review plan", payload["errors"][0])

    def test_schema3_plan_requires_exact_policy_dependency_scope(self) -> None:
        plan = UPGRADE.build_review_plan(self.root, "collect", [])
        plan.pop("policy_dependencies")
        plan["plan_id"] = UPGRADE.plan_id(plan)
        path = self.root / "schema3-missing-dependencies.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(UPGRADE.StalePlanError, "dependency scope changed"):
            UPGRADE.load_plan(path)

    def test_initialize_creates_workspace_and_is_idempotent(self) -> None:
        first = self.initialize()
        self.assertTrue((self.root / ".upgrade" / "STATE.md").is_file())
        self.assertTrue((self.root / ".upgrade" / "docs" / "UPGRADE_PLAN.md").is_file())
        self.assertTrue((self.root / ".gitignore").is_file())
        self.assertTrue(first["changed"])

        snapshots = {
            path: path.read_bytes()
            for path in (self.root / ".upgrade" / "STATE.md", self.root / ".upgrade" / "MANIFEST.md", self.root / ".gitignore")
        }
        second = self.initialize()
        self.assertEqual([], second["changed"])
        self.assertEqual(snapshots, {path: path.read_bytes() for path in snapshots})

    def test_initialize_dry_run_has_no_side_effects(self) -> None:
        payload, _ = self.run_cli("initialize", "--dry-run")
        self.assertFalse((self.root / ".upgrade").exists())
        self.assertTrue(any(item["action"].startswith("would_") for item in payload["changed"]))

    def test_initialize_creates_canonical_upgrade_governance_contract_without_forcing_root_agents(self) -> None:
        payload = self.initialize()
        contract = self.root / ".upgrade" / "AGENTS.md"
        self.assertTrue(contract.is_file())
        text = contract.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(UPGRADE.GOVERNANCE_CONTRACT_MARKER + "\n"))
        self.assertIn("canonical machine-readable governance contract", text)
        self.assertIn(".upgrade/STATE.md", text)
        self.assertFalse((self.root / "AGENTS.md").exists())
        self.assertTrue(any(item["path"] == ".upgrade/AGENTS.md" for item in payload["changed"]))

    def test_initialize_create_root_bridge_is_explicit_and_points_to_canonical_contract(self) -> None:
        payload, _ = self.run_cli("initialize", "--create-root-bridge")
        agents = self.root / "AGENTS.md"
        self.assertTrue(agents.is_file())
        text = agents.read_text(encoding="utf-8")
        self.assertIn(UPGRADE.GUARD_START, text)
        self.assertIn(".upgrade/AGENTS.md", text)
        self.assertTrue(any(item["path"] == "AGENTS.md" for item in payload["changed"]))
        health, _ = self.run_cli("health-check")
        self.assertEqual("current", health["result"]["governance"]["contract"]["status"] )
        self.assertEqual([{"path": "AGENTS.md", "status": "current"}], health["result"]["governance"]["root_bridges"] )

    def test_existing_root_agent_instruction_receives_bridge_and_user_content_is_preserved(self) -> None:
        claude = self.root / "CLAUDE.md"
        claude.write_text("# Local rules\n\nKeep this.\n", encoding="utf-8")
        self.initialize()
        text = claude.read_text(encoding="utf-8")
        self.assertIn("Keep this.", text)
        self.assertIn(".upgrade/AGENTS.md", text)
        self.assertIn(UPGRADE.GUARD_START, text)

    def test_health_check_detects_contract_drift_and_sync_repairs_full_managed_file(self) -> None:
        self.initialize()
        contract = self.root / ".upgrade" / "AGENTS.md"
        contract.write_text(contract.read_text(encoding="utf-8") + "\nmanual drift\n", encoding="utf-8")
        health, _ = self.run_cli("health-check")
        self.assertTrue(any("governance contract has drifted" in item for item in health["errors"]))
        self.assertEqual("drifted", health["result"]["governance"]["contract"]["status"] )
        self.run_cli("sync")
        self.assertEqual(UPGRADE.governance_contract_text(), contract.read_text(encoding="utf-8"))
        repaired, _ = self.run_cli("health-check")
        self.assertFalse(any("governance contract has drifted" in item for item in repaired["errors"]))

    def test_initialize_refuses_to_overwrite_unmanaged_upgrade_agents(self) -> None:
        upgrade = self.root / ".upgrade"
        upgrade.mkdir()
        contract = upgrade / "AGENTS.md"
        contract.write_text("# Existing local governance\n", encoding="utf-8")
        payload, _ = self.run_cli("initialize", expected=2)
        self.assertIn("unmanaged .upgrade/AGENTS.md already exists", payload["errors"][0])
        self.assertEqual("# Existing local governance\n", contract.read_text(encoding="utf-8"))
        self.assertFalse((self.root / ".gitignore").exists())
        self.assertFalse((upgrade / "STATE.md").exists())

    def test_nested_agent_bridges_are_detected_but_only_synced_explicitly(self) -> None:
        nested = self.root / "src"
        nested.mkdir()
        agents = nested / "AGENTS.md"
        agents.write_text("# Source rules\n", encoding="utf-8")
        self.initialize()
        self.assertNotIn(UPGRADE.GUARD_START, agents.read_text(encoding="utf-8"))
        health, _ = self.run_cli("health-check")
        self.assertIn("src/AGENTS.md", health["result"]["governance"]["nested_bridge_candidates"])
        self.run_cli("sync", "--sync-nested-bridges")
        text = agents.read_text(encoding="utf-8")
        self.assertIn("# Source rules", text)
        self.assertIn(".upgrade/AGENTS.md", text)
        health, _ = self.run_cli("health-check")
        self.assertEqual([], health["result"]["governance"]["nested_bridge_candidates"])
        self.assertIn({"path": "src/AGENTS.md", "status": "current"}, health["result"]["governance"]["nested_bridges"])

    def test_health_check_detects_missing_root_bridge_and_sync_repairs_it(self) -> None:
        agents = self.root / "AGENTS.md"
        agents.write_text("# Project rules\n", encoding="utf-8")
        self.initialize()
        text = agents.read_text(encoding="utf-8")
        begin = text.index(UPGRADE.GUARD_START)
        end = text.index(UPGRADE.GUARD_END, begin) + len(UPGRADE.GUARD_END)
        agents.write_text((text[:begin] + text[end:]).strip() + "\n", encoding="utf-8")
        health, _ = self.run_cli("health-check")
        self.assertTrue(any("root Agent instruction bridge is missing" in item for item in health["warnings"]))
        self.run_cli("sync")
        repaired = agents.read_text(encoding="utf-8")
        self.assertIn("# Project rules", repaired)
        self.assertIn(".upgrade/AGENTS.md", repaired)

    def test_legacy_default_profile_maps_to_governed_and_profiles_are_user_visible(self) -> None:
        payload, _ = self.run_cli("initialize", "--profile", "default")
        self.assertEqual("ok", payload["status"])
        config = json.loads((self.root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertEqual("governed", config["initial_profile"])
        self.assertEqual(list(UPGRADE.MODULE_ORDER), config["active_modules"])
        command = [sys.executable, "-X", "utf8", str(CLI), "initialize", "--help"]
        completed = subprocess.run(command, text=True, encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(0, completed.returncode, completed.stderr + completed.stdout)
        self.assertIn("--profile", completed.stdout)
        self.assertIn("lean", completed.stdout)
        self.assertIn("standard", completed.stdout)
        self.assertIn("governed", completed.stdout)
        self.assertIn("custom", completed.stdout)

    def test_bootstrap_review_is_read_only_and_recommends_lean_for_small_repository(self) -> None:
        (self.root / "package.json").write_text('{"name":"small"}\n', encoding="utf-8")
        payload, _ = self.run_cli("bootstrap-review")
        self.assertFalse((self.root / ".upgrade").exists())
        self.assertEqual([], payload["changed"])
        result = payload["result"]
        self.assertTrue(result["read_only"])
        self.assertEqual("lean", result["recommendation"]["profile"])
        self.assertEqual(["core", "planning"], result["profiles"]["lean"]["modules"])
        self.assertIn("customization", result)

    def test_bootstrap_review_recommends_governed_from_multiple_explainable_signals(self) -> None:
        (self.root / ".github" / "workflows").mkdir(parents=True)
        (self.root / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
        (self.root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (self.root / "migrations").mkdir()
        (self.root / "migrations" / "001.sql").write_text("select 1;\n", encoding="utf-8")
        (self.root / "terraform").mkdir()
        (self.root / "terraform" / "main.tf").write_text("terraform {}\n", encoding="utf-8")
        (self.root / "package.json").write_text('{"workspaces":["packages/*"]}\n', encoding="utf-8")
        for name in ("a", "b"):
            directory = self.root / "packages" / name
            directory.mkdir(parents=True)
            (directory / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
        nested = self.root / "src"
        nested.mkdir()
        (nested / "AGENTS.md").write_text("# Source rules\n", encoding="utf-8")
        payload, _ = self.run_cli("bootstrap-review")
        recommendation = payload["result"]["recommendation"]
        self.assertEqual("governed", recommendation["profile"])
        self.assertTrue(any("monorepo" in reason for reason in recommendation["reasons"]))
        self.assertTrue(any("database" in reason for reason in recommendation["reasons"]))
        self.assertTrue(any("infrastructure" in reason for reason in recommendation["reasons"]))
        self.assertFalse((self.root / ".upgrade").exists())

    def test_initialize_without_profile_uses_safe_lean_footprint(self) -> None:
        payload, _ = self.run_cli("initialize")
        config = json.loads((self.root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertEqual("lean", config["initial_profile"])
        self.assertEqual(["core", "planning"], config["active_modules"])
        self.assertEqual("safe-default", config["selection_source"])
        self.assertTrue(any("safe Lean footprint" in warning for warning in payload["warnings"]))
        self.assertTrue((self.root / ".upgrade" / "docs" / "UPGRADE_PLAN.md").is_file())
        self.assertFalse((self.root / ".upgrade" / "docs" / "IMPLEMENTATION_PLAN.md").exists())
        for relative in ("stages", "validation", "reports", "reviews", "decisions", "tmp", "archive", "logs"):
            self.assertFalse((self.root / ".upgrade" / relative).exists(), relative)
        health, _ = self.run_cli("health-check")
        self.assertFalse(health["errors"])
        self.assertEqual(["phase", "validation", "reporting", "review", "lifecycle"], health["result"]["workspace"]["inactive_modules"])

    def test_standard_profile_materializes_common_modules_but_not_formal_review(self) -> None:
        self.run_cli("initialize", "--profile", "standard")
        config = json.loads((self.root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertEqual(list(UPGRADE.PROFILE_MODULES["standard"]), config["active_modules"])
        for relative in ("stages", "checkpoints", "validation", "reports", "tmp", "archive", "logs"):
            self.assertTrue((self.root / ".upgrade" / relative).is_dir(), relative)
        self.assertFalse((self.root / ".upgrade" / "decisions").exists())
        self.assertFalse((self.root / ".upgrade" / "reviews").exists())

    def test_custom_profile_expands_module_dependencies_only(self) -> None:
        self.run_cli("initialize", "--profile", "custom", "--module", "phase")
        config = json.loads((self.root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertEqual(["core", "planning", "phase"], config["active_modules"])
        self.assertTrue((self.root / ".upgrade" / "docs" / "IMPLEMENTATION_PLAN.md").is_file())
        self.assertTrue((self.root / ".upgrade" / "stages").is_dir())
        self.assertFalse((self.root / ".upgrade" / "validation").exists())
        self.assertFalse((self.root / ".upgrade" / "tmp").exists())

    def test_phase_update_lazily_activates_phase_from_lean(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        payload, _ = self.run_cli("phase-update", "--phase", "1", "--current-task", "Start implementation")
        config = json.loads((self.root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertIn("phase", config["active_modules"])
        self.assertTrue((self.root / ".upgrade" / "stages" / "phase-1.md").is_file())
        self.assertTrue((self.root / ".upgrade" / "checkpoints" / "phase-1.entered").is_file())
        self.assertTrue(any(item["path"] == ".upgrade/CONFIG.json" for item in payload["changed"]))

    def test_inconsistent_active_phase_does_not_materialize_phase_module_before_error(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        state = self.root / ".upgrade" / "STATE.md"
        state.write_text(UPGRADE.replace_section(state.read_text(encoding="utf-8"), "current_phase", "Phase 1: In progress"), encoding="utf-8")
        payload, _ = self.run_cli("phase-update", "--phase", "1", "--current-task", "Unsafe", expected=2)
        self.assertIn("phase module is disabled", payload["errors"][0])
        self.assertFalse((self.root / ".upgrade" / "stages").exists())
        config = json.loads((self.root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertNotIn("phase", config["active_modules"])

    def test_validation_record_lazily_activates_validation_from_lean(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        self.run_cli("validation-record", "--check", "unit tests", "--status", "passed", "--exit-code", "0")
        config = json.loads((self.root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertIn("validation", config["active_modules"])
        self.assertTrue((self.root / ".upgrade" / "validation" / "records.jsonl").is_file())

    def test_collect_apply_lazily_activates_destination_module(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        source = self.root / "debug-upgrade.log"
        source.write_text("temporary upgrade log\n", encoding="utf-8")
        plan_path = self.root / "collect.json"
        review, _ = self.run_cli("collect-review", "--plan-out", str(plan_path))
        self.run_cli("collect-apply", "--plan", str(plan_path), "--approve", review["plan_id"] )
        config = json.loads((self.root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertIn("lifecycle", config["active_modules"])
        self.assertTrue((self.root / ".upgrade" / "logs" / "debug-upgrade.log").is_file())

    def test_forged_collect_plan_cannot_target_inactive_managed_canonical_file(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        source = self.root / "upgrade-note.md"
        source.write_text("upgrade notes\n", encoding="utf-8")
        review, _ = self.run_cli("collect-review")
        entry = next(item for item in review["result"]["plan"]["entries"] if item["source"] == "upgrade-note.md")
        forged_entry = dict(entry)
        forged_entry["destination"] = ".upgrade/reports/FINAL_REPORT.md"
        forged_entry["action"] = "copy"
        plan = dict(review["result"]["plan"] )
        plan["entries"] = [forged_entry]
        forged = self.write_forged_plan("forged-canonical.json", plan)
        payload, _ = self.run_cli("collect-apply", "--plan", str(forged), "--approve", json.loads(forged.read_text(encoding="utf-8"))["plan_id"], expected=2)
        self.assertIn("managed canonical workspace file", payload["errors"][0])
        self.assertFalse((self.root / ".upgrade" / "reports").exists())
        config = json.loads((self.root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertNotIn("reporting", config["active_modules"])

    def test_sync_enable_module_expands_without_disabling_existing_modules(self) -> None:
        self.run_cli("initialize", "--profile", "custom")
        self.run_cli("sync", "--enable-module", "review")
        config = json.loads((self.root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertEqual(["core", "planning", "review"], config["active_modules"])
        self.assertTrue((self.root / ".upgrade" / "reviews" / "POST_UPGRADE_REVIEW.md").is_file())
        self.assertTrue((self.root / ".upgrade" / "decisions").is_dir())

    def test_health_check_requires_only_enabled_module_resources(self) -> None:
        self.run_cli("initialize", "--profile", "standard")
        shutil.rmtree(self.root / ".upgrade" / "validation")
        health, _ = self.run_cli("health-check")
        self.assertTrue(any("validation" in error and "missing" in error for error in health["errors"]))
        self.run_cli("sync")
        repaired, _ = self.run_cli("health-check")
        self.assertFalse(any("validation" in error and "missing" in error for error in repaired["errors"]))

    def test_missing_adaptive_config_blocks_mutation_until_sync_repairs_it(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        config = self.root / ".upgrade" / "CONFIG.json"
        config.unlink()
        payload, _ = self.run_cli("validation-record", "--check", "x", "--status", "passed", expected=2)
        self.assertIn("CONFIG.json is missing", payload["errors"][0])
        self.assertFalse((self.root / ".upgrade" / "validation").exists())
        self.run_cli("sync")
        adopted = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual("legacy-governed", adopted["initial_profile"])
        self.assertEqual(list(UPGRADE.MODULE_ORDER), adopted["active_modules"])

    def test_sync_upgrades_release_metadata_without_reinitializing_workspace(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        config_path = self.root / ".upgrade" / "CONFIG.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["last_synced_skill_version"] = "10.0.0"
        config["last_synced_policy_version"] = 6
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        state_before = (self.root / ".upgrade" / "STATE.md").read_bytes()
        self.run_cli("sync")
        updated = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual("18.0.0", updated["last_synced_skill_version"])
        self.assertEqual(14, updated["last_synced_policy_version"])
        self.assertEqual("lean", updated["initial_profile"])
        self.assertEqual(state_before, (self.root / ".upgrade" / "STATE.md").read_bytes())

    def test_sync_adopts_round7_style_workspace_as_legacy_governed(self) -> None:
        upgrade = self.root / ".upgrade"
        upgrade.mkdir()
        # A minimal pre-Round-8 workspace can be repaired without guessing that
        # absent adaptive modules were intentionally disabled.
        (upgrade / "STATE.md").write_text((UPGRADE.TEMPLATES / "STATE.md").read_text(encoding="utf-8"), encoding="utf-8")
        (upgrade / "MANIFEST.md").write_text("# Legacy Manifest\n", encoding="utf-8")
        self.run_cli("sync")
        config = json.loads((upgrade / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertEqual("legacy-governed", config["initial_profile"])
        self.assertEqual(list(UPGRADE.MODULE_ORDER), config["active_modules"])
        self.assertTrue((upgrade / "reviews" / "POST_UPGRADE_REVIEW.md").is_file())
        health, _ = self.run_cli("health-check")
        self.assertFalse(health["errors"])

    def test_sync_preserves_user_content_and_rejects_duplicate_markers(self) -> None:
        self.initialize()
        agents = self.root / "AGENTS.md"
        agents.write_text("user text\n", encoding="utf-8")
        payload, _ = self.run_cli("sync")
        self.assertIn("user text", agents.read_text(encoding="utf-8"))
        self.assertTrue(any(item["path"] == "AGENTS.md" for item in payload["changed"]))

        agents.write_text(agents.read_text(encoding="utf-8") + "\n<!-- project-upgrade-maintainer:start -->\nx\n<!-- project-upgrade-maintainer:end -->\n", encoding="utf-8")
        payload, _ = self.run_cli("sync", expected=2)
        self.assertIn("marker conflict", payload["errors"][0])

    def test_sync_preserves_existing_file_mode(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX file modes are not stable on Windows")
        self.initialize()
        agents = self.root / "AGENTS.md"
        agents.write_text("user text\n", encoding="utf-8")
        agents.chmod(0o640)
        self.run_cli("sync")
        self.assertEqual(0o640, stat.S_IMODE(agents.stat().st_mode))

    def test_initialize_marker_conflict_has_no_side_effects(self) -> None:
        agents = self.root / "AGENTS.md"
        agents.write_text(
            "<!-- project-upgrade-maintainer:start -->\na\n<!-- project-upgrade-maintainer:end -->\n"
            "<!-- project-upgrade-maintainer:start -->\nb\n<!-- project-upgrade-maintainer:end -->\n",
            encoding="utf-8",
        )
        payload, _ = self.run_cli("initialize", expected=2)
        self.assertIn("marker conflict", payload["errors"][0])
        self.assertFalse((self.root / ".upgrade").exists())
        self.assertFalse((self.root / ".gitignore").exists())

    def test_manifest_marker_conflict_blocks_mutation_preflight(self) -> None:
        self.initialize()
        manifest = self.root / ".upgrade" / "MANIFEST.md"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + "\n<!-- project-upgrade-maintainer:manifest:start -->\ninvalid\n<!-- project-upgrade-maintainer:manifest:end -->\n",
            encoding="utf-8",
        )
        before_state = (self.root / ".upgrade" / "STATE.md").read_bytes()
        payload, _ = self.run_cli("phase-update", "--phase", "1", "--current-task", "Unsafe", expected=2)
        self.assertIn("marker conflict", payload["errors"][0])
        self.assertEqual(before_state, (self.root / ".upgrade" / "STATE.md").read_bytes())
        self.assertFalse((self.root / ".upgrade" / "stages" / "phase-1.md").exists())

    def test_utf8_bom_and_crlf_are_preserved(self) -> None:
        self.initialize()
        target = self.root / "CLAUDE.md"
        target.write_bytes(codecs.BOM_UTF8 + "用户内容\r\n".encode("utf-8"))
        self.run_cli("sync")
        raw = target.read_bytes()
        self.assertTrue(raw.startswith(codecs.BOM_UTF8))
        self.assertIn(b"\r\n", raw)
        self.assertIn("用户内容", raw.decode("utf-8-sig"))

    def test_collect_review_and_apply_copy(self) -> None:
        self.initialize()
        source = self.root / "upgrade_notes.md"
        source.write_text("migration approach", encoding="utf-8")
        plan = self.root / "collect-plan.json"
        review, _ = self.run_cli("collect-review", "--decision", "upgrade_notes.md=copy", "--plan-out", str(plan))
        self.assertEqual("copy", review["result"]["plan"]["entries"][0]["action"])
        apply, _ = self.run_cli("collect-apply", "--plan", str(plan), "--approve", review["plan_id"])
        self.assertTrue(source.exists())
        self.assertTrue((self.root / ".upgrade" / "tmp" / "upgrade_notes.md").exists())
        self.assertTrue(any(item["action"] == "copy" for item in apply["changed"]))

    def test_collect_stale_plan_and_wrong_approval_are_rejected(self) -> None:
        self.initialize()
        source = self.root / "tmp_upgrade.txt"
        source.write_text("first", encoding="utf-8")
        plan = self.root / "plan.json"
        review, _ = self.run_cli("collect-review", "--plan-out", str(plan))
        self.run_cli("collect-apply", "--plan", str(plan), "--approve", "wrong", expected=2)
        source.write_text("changed", encoding="utf-8")
        payload, _ = self.run_cli("collect-apply", "--plan", str(plan), "--approve", review["plan_id"], expected=3)
        self.assertIn("changed or disappeared", payload["errors"][0])

    def test_collect_tampered_plan_is_rejected(self) -> None:
        self.initialize()
        (self.root / "tmp_upgrade.txt").write_text("x", encoding="utf-8")
        plan = self.root / "plan.json"
        review, _ = self.run_cli("collect-review", "--plan-out", str(plan))
        data = json.loads(plan.read_text(encoding="utf-8"))
        data["entries"][0]["destination"] = ".upgrade/tmp/evil.txt"
        plan.write_text(json.dumps(data), encoding="utf-8")
        payload, _ = self.run_cli("collect-apply", "--plan", str(plan), "--approve", review["plan_id"], expected=2)
        self.assertIn("plan_id does not match", payload["errors"][0])

    def test_forged_protected_collect_plan_is_rejected_by_apply(self) -> None:
        self.initialize()
        source = self.root / "docs" / "upgrade_draft.md"
        source.parent.mkdir()
        source.write_text("draft", encoding="utf-8")
        stat = source.stat()
        plan = {
            "schema_version": 1,
            "kind": "collect",
            "root": str(self.root),
            "created_at": "test",
            "entries": [{
                "source": "docs/upgrade_draft.md",
                "destination": ".upgrade/docs/upgrade_draft.md",
                "action": "copy",
                "classification": "copy",
                "reason": "forged",
                "protected": True,
                "protected_authorized": True,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "mtime_ns": stat.st_mtime_ns,
            }],
        }
        path = self.write_forged_plan("forged.json", plan)
        approval = json.loads(path.read_text(encoding="utf-8"))["plan_id"]
        payload, _ = self.run_cli("collect-apply", "--plan", str(path), "--approve", approval, expected=2)
        self.assertIn("apply-time exact-path authorization", payload["errors"][0])

    def test_protected_collect_requires_path_and_action(self) -> None:
        self.initialize()
        protected = self.root / "docs" / "upgrade_draft.md"
        protected.parent.mkdir()
        protected.write_text("draft", encoding="utf-8")
        review, _ = self.run_cli("collect-review")
        entry = review["result"]["plan"]["entries"][0]
        self.assertEqual("forbidden", entry["action"])

        still_forbidden, _ = self.run_cli("collect-review", "--allow-protected", "docs/upgrade_draft.md")
        self.assertEqual("forbidden", still_forbidden["result"]["plan"]["entries"][0]["action"])

        allowed, _ = self.run_cli(
            "collect-review", "--allow-protected", "docs/upgrade_draft.md",
            "--decision", "docs/upgrade_draft.md=copy",
        )
        self.assertTrue(allowed["result"]["plan"]["entries"][0]["protected_authorized"])

    def test_collect_rg_and_standard_library_fallback_classify_consistently(self) -> None:
        self.initialize()
        (self.root / "tmp_upgrade.txt").write_text("scratch", encoding="utf-8")
        regular, _ = self.run_cli("collect-review")
        env = os.environ.copy()
        env["PATH"] = ""
        fallback, _ = self.run_cli("collect-review", env=env)
        select = lambda payload: [
            (entry["source"], entry["action"], entry["referenced"])
            for entry in payload["result"]["plan"]["entries"]
        ]
        self.assertEqual(select(regular), select(fallback))

    def test_collect_focus_limits_inventory_but_preserves_global_reference_detection(self) -> None:
        self.initialize()
        focused = self.root / "focus[1]" / "tmp_upgrade.txt"
        focused.parent.mkdir(parents=True)
        focused.write_text("scratch", encoding="utf-8")
        outside_candidate = self.root / "outside" / "tmp_upgrade.txt"
        outside_candidate.parent.mkdir()
        outside_candidate.write_text("scratch", encoding="utf-8")
        reference = self.root / "src" / "reference.py"
        reference.parent.mkdir()
        reference.write_text('name = "tmp_upgrade.txt"\n', encoding="utf-8")

        # Force the standard-library fallback so the test proves that focused
        # candidate discovery does not narrow cross-repository reference checks.
        payload, _ = self.run_cli(
            "collect-review", "--focus", "focus[1]/tmp_upgrade.txt",
            env={"PATH": ""},
        )
        entries = payload["result"]["plan"]["entries"]
        self.assertEqual(["focus[1]/tmp_upgrade.txt"], [entry["source"] for entry in entries])
        self.assertTrue(entries[0]["referenced"])
        self.assertEqual("confirm", entries[0]["action"])

    def test_collect_focus_dot_scans_repository_and_escape_is_rejected(self) -> None:
        self.initialize()
        (self.root / "tmp_upgrade.txt").write_text("scratch", encoding="utf-8")
        full, _ = self.run_cli("collect-review")
        dot, _ = self.run_cli("collect-review", "--focus", ".")
        select = lambda payload: [entry["source"] for entry in payload["result"]["plan"]["entries"]]
        self.assertEqual(select(full), select(dot))

        escaped, _ = self.run_cli("collect-review", "--focus", "../outside", expected=2)
        self.assertIn("outside allowed boundary", escaped["errors"][0])

    def test_collect_focus_can_review_explicitly_ignored_file(self) -> None:
        subprocess.run(["git", "init"], cwd=self.root, text=True, capture_output=True, check=True)
        self.initialize()
        ignored = self.root / "scratch" / "tmp_upgrade.txt"
        ignored.parent.mkdir()
        ignored.write_text("scratch", encoding="utf-8")
        with (self.root / ".gitignore").open("a", encoding="utf-8") as stream:
            stream.write("\n/scratch/\n")
        ignored_check = subprocess.run(
            ["git", "check-ignore", "scratch/tmp_upgrade.txt"], cwd=self.root,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, ignored_check.returncode)

        focused, _ = self.run_cli("collect-review", "--focus", "scratch/tmp_upgrade.txt")
        self.assertEqual(
            ["scratch/tmp_upgrade.txt"],
            [entry["source"] for entry in focused["result"]["plan"]["entries"]],
        )

    def test_collect_unfocused_discovers_targeted_ignored_candidates(self) -> None:
        subprocess.run(["git", "init"], cwd=self.root, text=True, capture_output=True, check=True)
        self.initialize()
        ignored_dir = self.root / "ignored" / "deep"
        ignored_dir.mkdir(parents=True)
        (ignored_dir / "upgrade_notes.md").write_text("plain", encoding="utf-8")
        (ignored_dir / "plain.md").write_text("upgrade plan", encoding="utf-8")
        (self.root / "ignored" / "scratch.txt").write_text("plain", encoding="utf-8")
        (self.root / "ignored" / "shallow.md").write_text("migration draft", encoding="utf-8")
        (self.root / "neutral.md").write_text("migration draft", encoding="utf-8")
        heavy = self.root / "node_modules" / "pkg"
        heavy.mkdir(parents=True)
        (heavy / "upgrade_notes.md").write_text("plain", encoding="utf-8")
        with (self.root / ".gitignore").open("a", encoding="utf-8") as stream:
            stream.write("\n/ignored/\n/neutral.md\n/node_modules/\n")

        payload, _ = self.run_cli("collect-review")
        sources = {entry["source"] for entry in payload["result"]["plan"]["entries"]}
        self.assertIn("ignored/deep/upgrade_notes.md", sources)
        self.assertIn("ignored/scratch.txt", sources)
        self.assertIn("ignored/shallow.md", sources)
        self.assertIn("neutral.md", sources)
        self.assertNotIn("ignored/deep/plain.md", sources)
        self.assertNotIn("node_modules/pkg/upgrade_notes.md", sources)

    def test_ignored_collect_pathspecs_exclude_heavy_directories(self) -> None:
        pathspecs = UPGRADE.ignored_collect_pathspecs()
        self.assertIn(":(icase)*upgrade*", pathspecs)
        self.assertIn(":(glob,icase)*.md", pathspecs)
        self.assertIn(":(glob,icase)*/*.md", pathspecs)
        self.assertIn(":(exclude,glob)**/node_modules/**", pathspecs)
        self.assertIn(":(exclude,glob)**/.upgrade/**", pathspecs)

    def test_phase_update_preserves_unknown_section_and_is_idempotent(self) -> None:
        self.initialize()
        state = self.root / ".upgrade" / "STATE.md"
        state.write_text(state.read_text(encoding="utf-8") + "\n## User Custom\n\ndo not touch\n", encoding="utf-8")
        first, _ = self.run_cli(
            "phase-update", "--phase", "2", "--current-task", "Refactor API", "--next-action", "Run tests",
            "--allow-skip", "--transition-reason", "Existing project is already operating in phase 2",
        )
        self.assertTrue(any(item["path"] == ".upgrade/stages/phase-2.md" for item in first["changed"]))
        self.assertIn("do not touch", state.read_text(encoding="utf-8"))
        time.sleep(1.1)
        second, _ = self.run_cli("phase-update", "--phase", "2", "--current-task", "Refactor API", "--next-action", "Run tests")
        self.assertEqual([], second["changed"])

    def test_phase_transition_requires_explicit_completion_and_handles_regression(self) -> None:
        self.initialize()
        self.run_cli("phase-update", "--phase", "1", "--current-task", "Phase one work")
        rejected, _ = self.run_cli(
            "phase-update", "--phase", "2", "--current-task", "Phase two work", expected=2,
        )
        self.assertIn("must be explicitly completed", rejected["errors"][0])

        advanced, _ = self.run_cli(
            "phase-update", "--phase", "2", "--current-task", "Phase two work",
            "--complete-previous", "--completion-note", "Acceptance criteria reviewed by user",
        )
        self.assertEqual("advance", advanced["result"]["transition"]["kind"])
        phase_one = (self.root / ".upgrade" / "stages" / "phase-1.md").read_text(encoding="utf-8")
        self.assertIn("## Status\n\ncompleted", phase_one)
        self.assertIn("Acceptance criteria reviewed by user", phase_one)

        rejected, _ = self.run_cli(
            "phase-update", "--phase", "1", "--current-task", "Rollback work", expected=2,
        )
        self.assertIn("is a regression", rejected["errors"][0])
        regressed, _ = self.run_cli(
            "phase-update", "--phase", "1", "--current-task", "Rollback work",
            "--allow-regression", "--transition-reason", "Rollback requested after phase 2 issue",
        )
        self.assertEqual("regression", regressed["result"]["transition"]["kind"])
        phase_two = (self.root / ".upgrade" / "stages" / "phase-2.md").read_text(encoding="utf-8")
        self.assertIn("## Status\n\nregressed", phase_two)
        health, _ = self.run_cli("health-check")
        self.assertFalse(health["errors"], health)

    def test_phase_skip_requires_explicit_reason(self) -> None:
        self.initialize()
        rejected, _ = self.run_cli("phase-update", "--phase", "3", "--current-task", "Work", expected=2)
        self.assertIn("skips phases", rejected["errors"][0])
        rejected, _ = self.run_cli(
            "phase-update", "--phase", "3", "--current-task", "Work", "--allow-skip", expected=2,
        )
        self.assertIn("requires --transition-reason", rejected["errors"][0])
        accepted, _ = self.run_cli(
            "phase-update", "--phase", "3", "--current-task", "Work",
            "--allow-skip", "--transition-reason", "Imported project already reached phase 3",
        )
        self.assertEqual("skip", accepted["result"]["transition"]["kind"])

    def test_cleanup_review_apply_and_dot_upgrade_decision(self) -> None:
        self.initialize()
        disposable = self.root / ".upgrade" / "tmp" / "scratch.txt"
        disposable.write_text("remove", encoding="utf-8")
        log = self.root / ".upgrade" / "logs" / "run.log"
        log.write_text("retain", encoding="utf-8")
        plan = self.root / ".upgrade" / "cleanup-plan.json"
        review, _ = self.run_cli(
            "cleanup-review", "--decision", ".upgrade/logs/run.log=keep", "--plan-out", str(plan),
        )
        actions = {entry["source"]: entry["action"] for entry in review["result"]["plan"]["entries"]}
        self.assertEqual("delete", actions[".upgrade/tmp/scratch.txt"])
        self.assertEqual("keep", actions[".upgrade/logs/run.log"])
        self.run_cli("cleanup-apply", "--plan", str(plan), "--approve", review["plan_id"])
        self.assertFalse(disposable.exists())
        self.assertTrue(log.exists())

    def test_cleanup_archive_old_trace(self) -> None:
        self.initialize()
        trace = self.root / ".upgrade" / "traces" / "old.trace"
        trace.write_text("old", encoding="utf-8")
        old = time.time() - 31 * 86400
        os.utime(trace, (old, old))
        plan = self.root / "cleanup.json"
        review, _ = self.run_cli("cleanup-review", "--plan-out", str(plan))
        entry = next(item for item in review["result"]["plan"]["entries"] if item["source"].endswith("old.trace"))
        self.assertEqual("archive", entry["action"])
        self.run_cli("cleanup-apply", "--plan", str(plan), "--approve", review["plan_id"])
        self.assertFalse(trace.exists())
        self.assertTrue((self.root / entry["destination"]).exists())

    def test_cleanup_archive_destination_is_collision_safe_across_repeated_paths(self) -> None:
        self.initialize()
        trace = self.root / ".upgrade" / "traces" / "old.trace"
        old = time.time() - 31 * 86400
        trace.write_text("first", encoding="utf-8")
        os.utime(trace, (old, old))
        first_plan = self.root / "cleanup-first.json"
        first_review, _ = self.run_cli("cleanup-review", "--plan-out", str(first_plan))
        first_entry = next(item for item in first_review["result"]["plan"]["entries"] if item["source"].endswith("old.trace"))
        self.run_cli("cleanup-apply", "--plan", str(first_plan), "--approve", first_review["plan_id"])

        trace.write_text("second", encoding="utf-8")
        os.utime(trace, (old, old))
        second_plan = self.root / "cleanup-second.json"
        second_review, _ = self.run_cli("cleanup-review", "--plan-out", str(second_plan))
        second_entry = next(item for item in second_review["result"]["plan"]["entries"] if item["source"].endswith("old.trace"))
        self.assertNotEqual(first_entry["destination"], second_entry["destination"])
        self.run_cli("cleanup-apply", "--plan", str(second_plan), "--approve", second_review["plan_id"])
        self.assertTrue((self.root / first_entry["destination"]).is_file())
        self.assertTrue((self.root / second_entry["destination"]).is_file())

    def test_cleanup_trash_destination_is_collision_safe_across_repeated_paths(self) -> None:
        self.initialize()
        source = self.root / ".upgrade" / "tmp" / "scratch.txt"
        source.write_text("first", encoding="utf-8")
        first_plan = self.root / "trash-first.json"
        first_review, _ = self.run_cli("cleanup-review", "--plan-out", str(first_plan))
        first_entry = next(item for item in first_review["result"]["plan"]["entries"] if item["source"].endswith("scratch.txt"))
        self.run_cli("cleanup-apply", "--plan", str(first_plan), "--approve", first_review["plan_id"])

        source.write_text("second", encoding="utf-8")
        second_plan = self.root / "trash-second.json"
        second_review, _ = self.run_cli("cleanup-review", "--plan-out", str(second_plan))
        second_entry = next(item for item in second_review["result"]["plan"]["entries"] if item["source"].endswith("scratch.txt"))
        self.assertNotEqual(first_entry["destination"], second_entry["destination"])
        self.run_cli("cleanup-apply", "--plan", str(second_plan), "--approve", second_review["plan_id"])
        self.assertTrue((self.root / first_entry["destination"]).is_file())
        self.assertTrue((self.root / second_entry["destination"]).is_file())

    def test_cleanup_update_uses_replacement_captured_by_plan(self) -> None:
        self.initialize()
        target = self.root / ".upgrade" / "reports" / "draft-report.md"
        target.write_text("old", encoding="utf-8")
        replacement = self.root / "replacement.txt"
        replacement.write_text("new content\n", encoding="utf-8")
        plan = self.root / "cleanup-update.json"
        review, _ = self.run_cli(
            "cleanup-review",
            "--decision", ".upgrade/reports/draft-report.md=update",
            "--replacement", ".upgrade/reports/draft-report.md=replacement.txt",
            "--plan-out", str(plan),
        )
        entry = next(item for item in review["result"]["plan"]["entries"] if item["source"].endswith("draft-report.md"))
        self.assertEqual("update", entry["action"])
        replacement.write_text("changed after review", encoding="utf-8")
        self.run_cli("cleanup-apply", "--plan", str(plan), "--approve", review["plan_id"])
        self.assertEqual("new content\n", target.read_text(encoding="utf-8"))

    def test_forged_cleanup_plan_cannot_delete_core_state(self) -> None:
        self.initialize()
        state = self.root / ".upgrade" / "STATE.md"
        stat = state.stat()
        plan = {
            "schema_version": 1,
            "kind": "cleanup",
            "root": str(self.root),
            "created_at": "test",
            "entries": [{
                "source": ".upgrade/STATE.md",
                "destination": None,
                "action": "delete",
                "classification": "delete",
                "reason": "forged",
                "sha256": hashlib.sha256(state.read_bytes()).hexdigest(),
                "mtime_ns": stat.st_mtime_ns,
            }],
        }
        path = self.write_forged_plan("forged-cleanup.json", plan)
        approval = json.loads(path.read_text(encoding="utf-8"))["plan_id"]
        payload, _ = self.run_cli("cleanup-apply", "--plan", str(path), "--approve", approval, expected=2)
        self.assertIn("retained workspace file", payload["errors"][0])
        self.assertTrue(state.exists())

    def test_forged_cleanup_plan_cannot_delete_current_stage(self) -> None:
        self.initialize()
        self.run_cli(
            "phase-update", "--phase", "4", "--current-task", "Work",
            "--allow-skip", "--transition-reason", "Test fixture enters phase 4 directly",
        )
        stage = self.root / ".upgrade" / "stages" / "phase-4.md"
        stat = stage.stat()
        plan = {
            "schema_version": 1,
            "kind": "cleanup",
            "root": str(self.root),
            "created_at": "test",
            "entries": [{
                "source": ".upgrade/stages/phase-4.md",
                "destination": None,
                "action": "delete",
                "classification": "delete",
                "reason": "forged",
                "sha256": hashlib.sha256(stage.read_bytes()).hexdigest(),
                "mtime_ns": stat.st_mtime_ns,
            }],
        }
        path = self.write_forged_plan("forged-stage-cleanup.json", plan)
        approval = json.loads(path.read_text(encoding="utf-8"))["plan_id"]
        payload, _ = self.run_cli("cleanup-apply", "--plan", str(path), "--approve", approval, expected=2)
        self.assertIn("retained workspace file", payload["errors"][0])
        self.assertTrue(stage.exists())

    def test_health_check_new_and_legacy_phase_artifacts(self) -> None:
        self.initialize()
        payload, _ = self.run_cli("health-check")
        self.assertIn(payload["status"], {"ok", "warning"})
        state = self.root / ".upgrade" / "STATE.md"
        text = state.read_text(encoding="utf-8").replace("Phase 0: Not started", "Phase 3: In progress")
        state.write_text(text, encoding="utf-8")
        legacy = self.root / ".upgrade" / "reports" / "stage3_report.md"
        legacy.write_text("legacy", encoding="utf-8")
        payload, _ = self.run_cli("health-check")
        self.assertTrue(any("phase 3" in item for item in payload["result"]["正常项"]))

    def test_health_check_detects_checkpoint_mismatch_and_missing_stage_reference(self) -> None:
        self.initialize()
        self.run_cli(
            "phase-update", "--phase", "2", "--current-task", "Work",
            "--allow-skip", "--transition-reason", "Test fixture enters phase 2 directly",
        )
        checkpoint = self.root / ".upgrade" / "checkpoints" / "phase-4.entered"
        checkpoint.write_text("forged", encoding="utf-8")
        stage = self.root / ".upgrade" / "stages" / "phase-2.md"
        stage.write_text(stage.read_text(encoding="utf-8") + "\nMissing: `.upgrade/docs/DOES_NOT_EXIST.md`\n", encoding="utf-8")
        payload, _ = self.run_cli("health-check")
        self.assertTrue(any("does not match latest checkpoint" in item for item in payload["errors"]))
        self.assertTrue(any("missing path" in item for item in payload["warnings"]))

    def test_health_check_rejects_workspace_reference_escape(self) -> None:
        self.initialize()
        state = self.root / ".upgrade" / "STATE.md"
        state.write_text(state.read_text(encoding="utf-8") + "\nUnsafe: `.upgrade/../../outside.txt`\n", encoding="utf-8")
        payload, _ = self.run_cli("health-check")
        self.assertEqual("error", payload["status"])
        self.assertTrue(any("unsafe workspace reference" in item for item in payload["errors"]))

    def test_health_check_reports_invalid_current_phase_schema(self) -> None:
        self.initialize()
        state = self.root / ".upgrade" / "STATE.md"
        text = state.read_text(encoding="utf-8").replace("Phase 0: Not started", "not-a-phase")
        state.write_text(text, encoding="utf-8")
        payload, _ = self.run_cli("health-check")
        self.assertEqual("error", payload["status"])
        self.assertTrue(any("current_phase is invalid" in item for item in payload["errors"]))

    def test_markdown_output_contains_same_result_payload(self) -> None:
        self.initialize()
        _, completed = self.run_cli("health-check", fmt="markdown")
        self.assertIn("# health-check", completed.stdout)
        self.assertIn("## 结果", completed.stdout)
        self.assertIn("正常项", completed.stdout)

    def test_plan_output_must_stay_within_root(self) -> None:
        self.initialize()
        outside = self.root.parent / "outside-plan.json"
        payload, _ = self.run_cli("collect-review", "--plan-out", str(outside), expected=2)
        self.assertIn("outside allowed boundary", payload["errors"][0])
        self.assertFalse(outside.exists())

    def test_plan_and_rendered_output_are_not_overwritten(self) -> None:
        self.initialize()
        plan = self.root / "existing-plan.json"
        plan.write_text("user", encoding="utf-8")
        payload, _ = self.run_cli("collect-review", "--plan-out", str(plan), expected=2)
        self.assertIn("refusing to overwrite plan", payload["errors"][0])
        self.assertEqual("user", plan.read_text(encoding="utf-8"))

        output = self.root / "existing-output.json"
        output.write_text("user", encoding="utf-8")
        command = [sys.executable, "-X", "utf8", str(CLI), "--root", str(self.root), "--output", str(output), "health-check"]
        completed = subprocess.run(command, text=True, encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(2, completed.returncode)
        self.assertEqual("user", output.read_text(encoding="utf-8"))

    def test_rendered_output_is_preflighted_before_mutation_and_relative_to_root(self) -> None:
        existing = self.root / "existing-output.json"
        existing.write_text("user", encoding="utf-8")
        payload, _ = self.run_cli("--output", "existing-output.json", "initialize", expected=2)
        self.assertIn("refusing to overwrite output", payload["errors"][0])
        self.assertFalse((self.root / ".upgrade").exists())
        self.assertEqual("user", existing.read_text(encoding="utf-8"))

        self.initialize()
        relative_output = self.root / "health-output.json"
        payload, completed = self.run_cli("--output", "health-output.json", "health-check")
        self.assertEqual("", completed.stdout)
        self.assertTrue(relative_output.is_file())
        rendered = json.loads(relative_output.read_text(encoding="utf-8"))
        self.assertEqual(payload, {})
        self.assertEqual("health-check", rendered["command"])

    def test_upgrade_symlink_is_rejected_before_lock_write(self) -> None:
        outside = self.root.parent / f"upgrade-link-{self.root.name}"
        outside.mkdir(exist_ok=True)
        try:
            try:
                (self.root / ".upgrade").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are not available")
            payload, _ = self.run_cli("initialize", expected=2)
            self.assertIn("not a symlink", payload["errors"][0])
            self.assertFalse((outside / ".lock").exists())
            self.assertEqual([], list(outside.iterdir()))
        finally:
            if (self.root / ".upgrade").is_symlink():
                (self.root / ".upgrade").unlink()
            outside.rmdir()

    def test_symlink_candidate_is_not_followed(self) -> None:
        outside_dir = Path(self.temp.name).parent / f"outside-{Path(self.temp.name).name}"
        outside_dir.mkdir(exist_ok=True)
        try:
            (outside_dir / "tmp_upgrade.txt").write_text("secret", encoding="utf-8")
            link = self.root / "linked"
            try:
                link.symlink_to(outside_dir, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are not available")
            self.initialize()
            payload, _ = self.run_cli("collect-review")
            sources = [item["source"] for item in payload["result"]["plan"]["entries"]]
            self.assertFalse(any(source.startswith("linked/") for source in sources))
        finally:
            for path in outside_dir.glob("*"):
                path.unlink()
            outside_dir.rmdir()


    def test_initialize_only_creates_dockerignore_when_docker_is_present(self) -> None:
        self.initialize()
        self.assertFalse((self.root / ".dockerignore").exists())

        second_root = self.root / "docker-project"
        second_root.mkdir()
        (second_root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        command = [sys.executable, "-X", "utf8", str(CLI), "--root", str(second_root), "--format", "json", "initialize"]
        completed = subprocess.run(command, text=True, encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(0, completed.returncode, completed.stderr + completed.stdout)
        self.assertTrue((second_root / ".dockerignore").exists())

    def test_sensitive_collect_requires_exact_path_authorization(self) -> None:
        self.initialize()
        source = self.root / "upgrade-secret.txt"
        source.write_text("OPENAI_API_KEY=TEST_PLACEHOLDER_SECRET_VALUE\n", encoding="utf-8")
        review, _ = self.run_cli("collect-review", "--decision", "upgrade-secret.txt=copy")
        entry = next(item for item in review["result"]["plan"]["entries"] if item["source"] == "upgrade-secret.txt")
        self.assertEqual("forbidden", entry["action"])
        self.assertTrue(entry["sensitive"])

        plan = self.root / "sensitive-plan.json"
        allowed, _ = self.run_cli(
            "collect-review", "--decision", "upgrade-secret.txt=copy", "--allow-sensitive", "upgrade-secret.txt",
            "--plan-out", str(plan),
        )
        entry = next(item for item in allowed["result"]["plan"]["entries"] if item["source"] == "upgrade-secret.txt")
        self.assertEqual("copy", entry["action"])
        self.assertTrue(entry["sensitive_authorized"])
        denied, _ = self.run_cli(
            "collect-apply", "--plan", str(plan), "--approve", allowed["plan_id"], expected=2,
        )
        self.assertIn("apply-time exact-path authorization", denied["errors"][0])
        self.run_cli(
            "collect-apply", "--plan", str(plan), "--approve", allowed["plan_id"],
            "--allow-sensitive", "upgrade-secret.txt",
        )
        self.assertTrue((self.root / ".upgrade" / "tmp" / "upgrade-secret.txt").is_file())

    def test_forged_sensitive_plan_cannot_bypass_apply_time_authorization(self) -> None:
        self.initialize()
        source = self.root / ".env"
        source.write_text("OPENAI_API_KEY=TEST_PLACEHOLDER_SECRET_VALUE\n", encoding="utf-8")
        stat = source.stat()
        plan = {
            "kind": "collect",
            "root": str(self.root),
            "created_at": "test",
            "entries": [{
                "source": ".env",
                "destination": ".upgrade/tmp/.env",
                "action": "copy",
                "classification": "copy",
                "reason": "forged",
                "protected": False,
                "protected_authorized": False,
                "sensitive": True,
                "sensitive_authorized": True,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "mtime_ns": stat.st_mtime_ns,
            }],
        }
        path = self.write_forged_plan("forged-sensitive.json", plan)
        approval = json.loads(path.read_text(encoding="utf-8"))["plan_id"]
        payload, _ = self.run_cli("collect-apply", "--plan", str(path), "--approve", approval, expected=2)
        self.assertIn("apply-time exact-path authorization", payload["errors"][0])
        self.assertFalse((self.root / ".upgrade" / "tmp" / ".env").exists())

    def test_plan_policy_dependencies_are_scoped_by_kind(self) -> None:
        common = {"references/shared/safety-and-lifecycle.md"}
        specific = {
            "artifact-policy": {
                "subskills/lifecycle/references/artifact-policy-linking.md",
                "assets/templates/AGENTS.md",
                "assets/templates/README.md",
                "assets/templates/ARTIFACTS.json",
            },
            "collect": {"subskills/lifecycle/references/collect-classification.md"},
            "cleanup": {"subskills/lifecycle/references/cleanup-classification.md"},
            "git-policy": {"subskills/lifecycle/references/git-policy.md"},
            "purge": {"subskills/lifecycle/references/purge.md"},
        }
        for kind, files in specific.items():
            with self.subTest(kind=kind):
                dependencies = set(UPGRADE.plan_policy_dependencies(kind))
                self.assertEqual(common | files, dependencies)
                self.assertNotIn("scripts/upgrade_workspace.py", dependencies)

    def test_plan_staleness_tracks_related_but_ignores_unrelated_policy_files(self) -> None:
        cases = {
            "artifact-policy": (
                "subskills/lifecycle/references/artifact-policy-linking.md",
                "subskills/lifecycle/references/collect-classification.md",
            ),
            "collect": (
                "subskills/lifecycle/references/collect-classification.md",
                "subskills/lifecycle/references/git-policy.md",
            ),
            "cleanup": (
                "subskills/lifecycle/references/cleanup-classification.md",
                "subskills/lifecycle/references/artifact-policy-linking.md",
            ),
            "git-policy": (
                "subskills/lifecycle/references/git-policy.md",
                "subskills/lifecycle/references/cleanup-classification.md",
            ),
            "purge": (
                "subskills/lifecycle/references/purge.md",
                "subskills/lifecycle/references/collect-classification.md",
            ),
        }
        policy_files = {relative for kind in cases for relative in UPGRADE.plan_policy_dependencies(kind)}
        policy_files.update(unrelated for _, unrelated in cases.values())
        with tempfile.TemporaryDirectory() as fake_dir:
            fake_root = Path(fake_dir)
            for relative in policy_files:
                target = fake_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"baseline:{relative}\n", encoding="utf-8")
            with patch.object(UPGRADE, "SKILL_ROOT", fake_root):
                for kind, (related, unrelated) in cases.items():
                    with self.subTest(kind=kind):
                        plan = UPGRADE.build_review_plan(self.root, kind, [])
                        plan_path = self.root / f"{kind}-scoped-plan.json"
                        plan_path.write_text(json.dumps(plan), encoding="utf-8")
                        for relative, stale in ((unrelated, False), (related, True)):
                            target = fake_root / relative
                            original = target.read_text(encoding="utf-8")
                            target.write_text(original + "changed\n", encoding="utf-8")
                            try:
                                if stale:
                                    with self.assertRaises(UPGRADE.StalePlanError):
                                        UPGRADE.load_plan(plan_path)
                                else:
                                    self.assertEqual(plan, UPGRADE.load_plan(plan_path))
                            finally:
                                target.write_text(original, encoding="utf-8")

    def test_review_plan_is_bound_to_current_policy_implementation(self) -> None:
        self.initialize()
        source = self.root / "tmp_upgrade.txt"
        source.write_text("scratch", encoding="utf-8")
        plan = self.root / "plan-versioned.json"
        review, _ = self.run_cli("collect-review", "--plan-out", str(plan))
        payload = json.loads(plan.read_text(encoding="utf-8"))
        self.assertEqual(UPGRADE.PLAN_SCHEMA_VERSION, payload["schema_version"])
        self.assertEqual(UPGRADE.SKILL_VERSION, payload["skill_version"])
        self.assertEqual(UPGRADE.POLICY_VERSION, payload["policy_version"])
        payload["implementation_sha256"] = "0" * 64
        payload["plan_id"] = UPGRADE.plan_id(payload)
        plan.write_text(json.dumps(payload), encoding="utf-8")
        rejected, _ = self.run_cli(
            "collect-apply", "--plan", str(plan), "--approve", payload["plan_id"], expected=3,
        )
        self.assertIn("implementation changed", rejected["errors"][0])

    def test_cleanup_delete_is_recoverable_then_purge_is_permanent(self) -> None:
        self.initialize()
        source = self.root / ".upgrade" / "tmp" / "scratch.txt"
        source.write_text("recover me", encoding="utf-8")
        cleanup_plan = self.root / "cleanup.json"
        review, _ = self.run_cli("cleanup-review", "--plan-out", str(cleanup_plan))
        applied, _ = self.run_cli("cleanup-apply", "--plan", str(cleanup_plan), "--approve", review["plan_id"])
        self.assertFalse(source.exists())
        trashed = [item for item in applied["changed"] if item["action"] == "trashed"]
        self.assertEqual(1, len(trashed))
        trash_path = self.root / trashed[0]["destination"]
        self.assertTrue(trash_path.is_file())
        self.assertEqual("recover me", trash_path.read_text(encoding="utf-8"))

        purge_plan = self.root / "purge.json"
        purge_review, _ = self.run_cli("purge-review", "--plan-out", str(purge_plan))
        self.assertEqual(1, purge_review["result"]["summary"].get("purge", 0))
        self.run_cli("purge-apply", "--plan", str(purge_plan), "--approve", purge_review["plan_id"])
        self.assertFalse(trash_path.exists())

    def test_cleanup_scans_heavy_named_directories_inside_managed_tmp(self) -> None:
        self.initialize()
        source = self.root / ".upgrade" / "tmp" / "node_modules" / "pkg" / "cache.bin"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"cache")
        review, _ = self.run_cli("cleanup-review")
        entry = next(item for item in review["result"]["plan"]["entries"] if item["source"].endswith("node_modules/pkg/cache.bin"))
        self.assertEqual("delete", entry["action"])

    def test_purge_runtime_failure_reports_partial_and_preserves_remaining_trash(self) -> None:
        self.initialize()
        trash = self.root / ".upgrade" / "tmp" / ".trash"
        first = trash / "a.txt"
        second = trash / "b.txt"
        first.write_text("a", encoding="utf-8")
        second.write_text("b", encoding="utf-8")
        plan_path = self.root / "purge-partial.json"
        review, _ = self.run_cli("purge-review", "--plan-out", str(plan_path))

        original_unlink = Path.unlink

        def flaky_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path == second:
                raise OSError("simulated purge failure")
            original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", new=flaky_unlink):
            payload, _ = self.run_cli(
                "purge-apply", "--plan", str(plan_path), "--approve", review["plan_id"], expected=4,
            )
        self.assertEqual("partial", payload["status"])
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertTrue(any("simulated purge failure" in item for item in payload["errors"]))
        self.assertTrue(any(item["path"].endswith("a.txt") and item["action"] == "purged" for item in payload["changed"]))
        trace = (self.root / ".upgrade" / "traces" / "last-maintenance.md").read_text(encoding="utf-8")
        self.assertIn("## Status\n\npartial", trace)
        self.assertIn("simulated purge failure", trace)

    def test_validation_record_appends_evidence_and_updates_state(self) -> None:
        self.initialize()
        self.run_cli(
            "validation-record", "--check", "python -m unittest", "--status", "passed",
            "--exit-code", "0", "--environment", "local", "--scope", "unit",
        )
        records = self.root / ".upgrade" / "validation" / "records.jsonl"
        lines = records.read_text(encoding="utf-8").splitlines()
        self.assertEqual(1, len(lines))
        record = json.loads(lines[0])
        self.assertEqual("passed", record["status"])
        state = (self.root / ".upgrade" / "STATE.md").read_text(encoding="utf-8")
        self.assertIn("python -m unittest", state)
        self.assertIn("passed", state)

        payload, _ = self.run_cli(
            "validation-record", "--check", "Android real device", "--status", "unverified",
            "--note", "device unavailable",
        )
        self.assertEqual("unverified", payload["result"]["record"]["status"])
        state = (self.root / ".upgrade" / "STATE.md").read_text(encoding="utf-8")
        self.assertIn("device unavailable", state)

    def test_artifact_and_git_policy_audit_can_add_ignore_rule(self) -> None:
        subprocess.run(["git", "init"], cwd=self.root, text=True, capture_output=True, check=True)
        self.initialize()
        generated = self.root / "coverage" / "result.json"
        generated.parent.mkdir()
        generated.write_text("{}", encoding="utf-8")

        audit, _ = self.run_cli("git-audit")
        finding = next(item for item in audit["result"]["entries"] if item["path"] == "coverage/result.json")
        self.assertEqual("generated", finding["classification"])
        self.assertEqual("untracked", finding["actual_git_state"])
        self.assertEqual("ignored", finding["expected_git_state"])

        plan_path = self.root / "git-policy.json"
        review, _ = self.run_cli("git-policy-review", "--plan-out", str(plan_path))
        entry = next(item for item in review["result"]["plan"]["entries"] if item["path"] == "coverage/result.json")
        self.assertEqual("add-ignore", entry["action"])
        self.run_cli("git-policy-apply", "--plan", str(plan_path), "--approve", review["plan_id"])
        ignore_text = (self.root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("# project-upgrade-maintainer:git-policy:start", ignore_text)
        self.assertIn("/coverage/", ignore_text)
        ignored = subprocess.run(["git", "check-ignore", "coverage/result.json"], cwd=self.root, text=True, capture_output=True, check=False)
        self.assertEqual(0, ignored.returncode)

    def test_permanent_context_conflict_is_not_auto_ignored(self) -> None:
        subprocess.run(["git", "init"], cwd=self.root, text=True, capture_output=True, check=True)
        self.initialize()
        source = self.root / "src" / "logs" / "worker.py"
        source.parent.mkdir(parents=True)
        source.write_text("print('worker')\n", encoding="utf-8")
        audit, _ = self.run_cli("artifact-audit", "--focus", "src/logs/worker.py")
        entry = audit["result"]["entries"][0]
        self.assertEqual("unknown", entry["classification"])
        self.assertIn("conflicting lifecycle signals", entry["reason"])
        self.assertIsNone(entry["ignore_suggestion"])
        review, _ = self.run_cli("git-policy-review", "--focus", "src/logs/worker.py")
        self.assertEqual([], review["result"]["plan"]["entries"])

    def test_gitignore_literal_rule_escapes_pattern_metacharacters(self) -> None:
        subprocess.run(["git", "init"], cwd=self.root, text=True, capture_output=True, check=True)
        self.initialize()
        source = self.root / "debug[1].log"
        source.write_text("debug\n", encoding="utf-8")
        plan = self.root / "git-special.json"
        review, _ = self.run_cli("git-policy-review", "--focus", "debug[1].log", "--plan-out", str(plan))
        entry = review["result"]["plan"]["entries"][0]
        self.assertEqual(r"/debug\[1\].log", entry["ignore_rule"])
        self.run_cli("git-policy-apply", "--plan", str(plan), "--approve", review["plan_id"])
        exact = subprocess.run(["git", "check-ignore", "debug[1].log"], cwd=self.root, text=True, capture_output=True, check=False)
        self.assertEqual(0, exact.returncode)
        lookalike = self.root / "debug1.log"
        lookalike.write_text("other\n", encoding="utf-8")
        wrong = subprocess.run(["git", "check-ignore", "debug1.log"], cwd=self.root, text=True, capture_output=True, check=False)
        self.assertNotEqual(0, wrong.returncode)

    def test_git_policy_apply_rejects_gitignore_symlink(self) -> None:
        subprocess.run(["git", "init"], cwd=self.root, text=True, capture_output=True, check=True)
        self.initialize()
        generated = self.root / "coverage" / "result.json"
        generated.parent.mkdir()
        generated.write_text("{}", encoding="utf-8")
        plan_path = self.root / "git-policy.json"
        review, _ = self.run_cli("git-policy-review", "--plan-out", str(plan_path))

        outside = self.root.parent / f"outside-ignore-{self.root.name}"
        outside.write_text("outside-content\n", encoding="utf-8")
        gitignore = self.root / ".gitignore"
        gitignore.unlink()
        try:
            try:
                gitignore.symlink_to(outside)
            except OSError:
                self.skipTest("file symlinks are not available")
            payload, _ = self.run_cli(
                "git-policy-apply", "--plan", str(plan_path), "--approve", review["plan_id"], expected=2,
            )
            self.assertIn(".gitignore must not be a symlink", payload["errors"][0])
            self.assertEqual("outside-content\n", outside.read_text(encoding="utf-8"))
        finally:
            if gitignore.is_symlink():
                gitignore.unlink()
            outside.unlink(missing_ok=True)

    def test_audit_focus_defers_hashing_and_limits_git_policy_plan(self) -> None:
        subprocess.run(["git", "init"], cwd=self.root, text=True, capture_output=True, check=True)
        self.initialize()
        focused = self.root / "focus[1]" / "coverage" / "result.bin"
        focused.parent.mkdir(parents=True)
        focused.write_bytes(b"x" * 1024)
        sibling = self.root / "focus[1]" / "coverage" / "other.bin"
        sibling.write_bytes(b"y" * 1024)
        outside = self.root / "other" / "coverage" / "result.bin"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b"z" * 1024)

        audit, _ = self.run_cli("artifact-audit", "--focus", "focus[1]/coverage/result.bin")
        entries = audit["result"]["entries"]
        self.assertEqual(["focus[1]/coverage/result.bin"], [entry["path"] for entry in entries])
        self.assertIsNone(entries[0]["sha256"])

        review, _ = self.run_cli("git-policy-review", "--focus", "focus[1]/coverage/result.bin")
        plan_entries = review["result"]["plan"]["entries"]
        self.assertEqual(1, len(plan_entries))
        self.assertEqual("add-ignore", plan_entries[0]["action"])
        self.assertEqual(hashlib.sha256(focused.read_bytes()).hexdigest(), plan_entries[0]["sha256"])

    def test_audit_focus_rejects_path_escape(self) -> None:
        payload, _ = self.run_cli("artifact-audit", "--focus", "../outside", expected=2)
        self.assertIn("outside allowed boundary", payload["errors"][0])

    def test_absolute_focus_inside_root_preserves_git_state(self) -> None:
        subprocess.run(["git", "init"], cwd=self.root, text=True, capture_output=True, check=True)
        self.initialize()
        focused = self.root / "coverage" / "absolute.bin"
        focused.parent.mkdir()
        focused.write_bytes(b"absolute")
        payload, _ = self.run_cli("artifact-audit", "--focus", str(focused))
        entries = payload["result"]["entries"]
        self.assertEqual(1, len(entries))
        self.assertEqual("coverage/absolute.bin", entries[0]["path"])
        self.assertEqual("untracked", entries[0]["actual_git_state"])

    def test_nested_git_root_uses_git_inventory_and_status(self) -> None:
        subprocess.run(["git", "init"], cwd=self.root, text=True, capture_output=True, check=True)
        nested = self.root / "nested"
        nested.mkdir()
        (self.root / ".gitignore").write_text("nested/upgrade_notes.md\n", encoding="utf-8")
        self.run_cli_at(nested, "initialize")
        ignored = nested / "upgrade_notes.md"
        ignored.write_text("migration draft", encoding="utf-8")

        review, _ = self.run_cli_at(nested, "collect-review")
        sources = [entry["source"] for entry in review["result"]["plan"]["entries"]]
        self.assertIn("upgrade_notes.md", sources)

        health, _ = self.run_cli_at(nested, "health-check")
        self.assertTrue(any("--root differs from Git repository root" in item for item in health["warnings"]))
        self.assertFalse(any("not a readable Git work tree" in item for item in health["warnings"]))

    def test_mutation_lock_blocks_concurrent_mutation(self) -> None:
        self.initialize()
        lock = self.root / ".upgrade" / ".lock"
        lock.write_text('{"pid":999,"command_started_at":"test"}\n', encoding="utf-8")
        payload, _ = self.run_cli("phase-update", "--phase", "1", "--current-task", "Work", expected=2)
        self.assertIn("workspace is locked", payload["errors"][0])

    def test_phase_update_records_plan_drift_blocker_and_unverified(self) -> None:
        self.initialize()
        self.run_cli(
            "phase-update", "--phase", "2", "--current-task", "Refactor",
            "--plan-drift", "Changed API order after review", "--blocker", "Waiting for dependency",
            "--unverified", "Production migration not run",
            "--allow-skip", "--transition-reason", "Existing project is already operating in phase 2",
        )
        state = (self.root / ".upgrade" / "STATE.md").read_text(encoding="utf-8")
        self.assertIn("Changed API order after review", state)
        self.assertIn("Waiting for dependency", state)
        self.assertIn("Production migration not run", state)
        stage = (self.root / ".upgrade" / "stages" / "phase-2.md").read_text(encoding="utf-8")
        self.assertIn("Changed API order after review", stage)

    def test_maintenance_traces_are_append_only_with_latest_pointer(self) -> None:
        self.initialize()
        self.run_cli("phase-update", "--phase", "1", "--current-task", "Work")
        traces = list((self.root / ".upgrade" / "traces").glob("*.md"))
        historical = [path for path in traces if path.name != "last-maintenance.md"]
        self.assertGreaterEqual(len(historical), 2)
        self.assertTrue((self.root / ".upgrade" / "traces" / "last-maintenance.md").is_file())

    def test_maintenance_traces_do_not_collide_with_same_second_timestamp(self) -> None:
        self.initialize()
        traces_dir = self.root / ".upgrade" / "traces"
        before = {path.name for path in traces_dir.glob("*.md") if path.name != "last-maintenance.md"}
        with patch.object(UPGRADE, "now_iso", return_value="2026-09-09T12:00:00+00:00"):
            self.run_cli("phase-update", "--phase", "1", "--current-task", "First")
            self.run_cli("phase-update", "--phase", "1", "--current-task", "Second")
        after = {path.name for path in traces_dir.glob("*.md") if path.name != "last-maintenance.md"}
        created = sorted(after - before)
        self.assertEqual(2, len(created))
        self.assertNotEqual(created[0], created[1])

    def test_unexpected_mid_mutation_failure_preserves_partial_changes(self) -> None:
        original_atomic_write = UPGRADE.atomic_write

        def flaky_atomic_write(path: Path, text: str, **kwargs: object) -> bool:
            if path == self.root / ".gitignore":
                raise OSError("simulated output device failure")
            return original_atomic_write(path, text, **kwargs)

        with patch.object(UPGRADE, "atomic_write", new=flaky_atomic_write):
            payload, _ = self.run_cli("initialize", expected=4)
        self.assertEqual("partial", payload["status"])
        self.assertTrue((self.root / ".upgrade").is_dir())
        self.assertTrue(payload["changed"])
        self.assertTrue(any("simulated output device failure" in item for item in payload["errors"]))

    def test_manifest_finalization_failure_writes_partial_trace(self) -> None:
        self.initialize()
        original_atomic_write = UPGRADE.atomic_write

        def flaky_atomic_write(path: Path, text: str, **kwargs: object) -> bool:
            if path == self.root / ".upgrade" / "MANIFEST.md":
                raise OSError("simulated manifest refresh failure")
            return original_atomic_write(path, text, **kwargs)

        with patch.object(UPGRADE, "atomic_write", new=flaky_atomic_write):
            payload, _ = self.run_cli("phase-update", "--phase", "1", "--current-task", "Work", expected=4)
        self.assertEqual("partial", payload["status"])
        trace = (self.root / ".upgrade" / "traces" / "last-maintenance.md").read_text(encoding="utf-8")
        self.assertIn("## Status\n\npartial", trace)
        self.assertIn("simulated manifest refresh failure", trace)


    def test_initialize_creates_artifact_policy_registry_and_link_sections(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        registry = self.root / ".upgrade" / "ARTIFACTS.json"
        self.assertTrue(registry.is_file())
        payload = json.loads(registry.read_text(encoding="utf-8"))
        self.assertEqual([], payload["entries"])
        config = json.loads((self.root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertEqual(UPGRADE.artifact_registry_digest(payload), config["artifact_registry_sha256"])
        self.assertIn(UPGRADE.AGENTS_ARTIFACT_START, (self.root / ".upgrade" / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertIn(UPGRADE.README_ARTIFACT_START, (self.root / ".upgrade" / "README.md").read_text(encoding="utf-8"))

    def test_artifact_policy_review_classifies_security_review_as_governance(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        payload, _ = self.run_cli(
            "artifact-policy-review", "--path", "security/SECURITY_REVIEW.md",
            "--purpose", "Security upgrade review", "--create",
        )
        classification = payload["result"]["classification"]
        policy = payload["result"]["policy_preview"]
        self.assertEqual("governance", classification["recommended_kind"])
        self.assertEqual("review", policy["module"])
        self.assertEqual("tracked", policy["git_policy"])
        self.assertTrue(policy["required"])
        self.assertTrue(policy["expose_in_agents"])
        self.assertTrue(any("authentication" in rule for rule in policy["rules"]))

    def test_artifact_policy_apply_creates_governance_artifact_and_links_context(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        plan = self.root / "artifact-plan.json"
        review, _ = self.run_cli(
            "artifact-policy-review", "--path", "security/SECURITY_REVIEW.md",
            "--purpose", "Security upgrade review", "--create", "--plan-out", str(plan),
        )
        self.run_cli("artifact-policy-apply", "--plan", str(plan), "--approve", review["plan_id"])
        artifact = self.root / ".upgrade" / "security" / "SECURITY_REVIEW.md"
        self.assertTrue(artifact.is_file())
        registry = json.loads((self.root / ".upgrade" / "ARTIFACTS.json").read_text(encoding="utf-8"))
        self.assertEqual([".upgrade/security/SECURITY_REVIEW.md"], [entry["path"] for entry in registry["entries"]])
        agents = (self.root / ".upgrade" / "AGENTS.md").read_text(encoding="utf-8")
        readme = (self.root / ".upgrade" / "README.md").read_text(encoding="utf-8")
        manifest = (self.root / ".upgrade" / "MANIFEST.md").read_text(encoding="utf-8")
        self.assertIn(".upgrade/security/SECURITY_REVIEW.md", agents)
        self.assertIn(".upgrade/security/SECURITY_REVIEW.md", readme)
        self.assertIn(".upgrade/security/SECURITY_REVIEW.md", manifest)
        config = json.loads((self.root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertIn("review", config["active_modules"])
        health, _ = self.run_cli("health-check")
        self.assertFalse(health["errors"])

    def test_runtime_artifact_links_ignore_policies_without_agent_rule_growth(self) -> None:
        (self.root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        self.run_cli("initialize", "--profile", "lean")
        plan = self.root / "runtime-plan.json"
        review, _ = self.run_cli(
            "artifact-policy-review", "--path", "runtime/migration-debug.log",
            "--purpose", "Raw migration debug output", "--create", "--plan-out", str(plan),
        )
        self.assertEqual("runtime", review["result"]["policy_preview"]["kind"])
        self.run_cli("artifact-policy-apply", "--plan", str(plan), "--approve", review["plan_id"])
        rule = "/.upgrade/runtime/migration-debug.log"
        self.assertIn(rule, (self.root / ".gitignore").read_text(encoding="utf-8"))
        self.assertIn(rule, (self.root / ".dockerignore").read_text(encoding="utf-8"))
        self.assertNotIn("migration-debug.log", (self.root / ".upgrade" / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertNotIn("migration-debug.log", (self.root / ".upgrade" / "README.md").read_text(encoding="utf-8"))
        config = json.loads((self.root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
        self.assertIn("lifecycle", config["active_modules"])

    def test_ambiguous_artifact_policy_requires_explicit_kind(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        payload, _ = self.run_cli(
            "artifact-policy-review", "--path", "security/security-debug.log", "--create",
        )
        entry = payload["result"]["plan"]["entries"][0]
        self.assertEqual("confirm", entry["action"])
        self.assertTrue(payload["result"]["classification"]["ambiguous"])
        explicit, _ = self.run_cli(
            "artifact-policy-review", "--path", "security/security-debug.log", "--create", "--kind", "runtime",
        )
        self.assertEqual("create-and-register", explicit["result"]["plan"]["entries"][0]["action"])

    def test_content_artifact_registers_existing_file_without_agent_or_ignore_growth(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        notes = self.root / ".upgrade" / "docs" / "TEAM_NOTES.md"
        notes.write_text("# Team Notes\n\nRetained upgrade context.\n", encoding="utf-8")
        plan = self.root / "content-policy.json"
        review, _ = self.run_cli(
            "artifact-policy-review", "--path", "docs/TEAM_NOTES.md",
            "--purpose", "Retained team upgrade context", "--plan-out", str(plan),
        )
        self.assertEqual("content", review["result"]["policy_preview"]["kind"])
        self.assertEqual("register-existing", review["result"]["plan"]["entries"][0]["action"])
        self.run_cli("artifact-policy-apply", "--plan", str(plan), "--approve", review["plan_id"])
        self.assertIn(".upgrade/docs/TEAM_NOTES.md", (self.root / ".upgrade" / "README.md").read_text(encoding="utf-8"))
        self.assertNotIn("TEAM_NOTES.md", (self.root / ".upgrade" / "AGENTS.md").read_text(encoding="utf-8"))
        gitignore = self.root / ".gitignore"
        if gitignore.exists():
            self.assertNotIn("TEAM_NOTES.md", gitignore.read_text(encoding="utf-8"))

    def test_explicit_governance_rule_is_plan_bound_and_rendered_in_agents(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        plan = self.root / "rule-policy.json"
        rule = "Read this checklist before changing upgrade authentication behavior."
        review, _ = self.run_cli(
            "artifact-policy-review", "--path", "security/AUTH_CHECKLIST.md", "--create",
            "--kind", "governance", "--rule", rule, "--plan-out", str(plan),
        )
        self.run_cli("artifact-policy-apply", "--plan", str(plan), "--approve", review["plan_id"])
        agents = (self.root / ".upgrade" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(".upgrade/security/AUTH_CHECKLIST.md", agents)
        self.assertIn(rule, agents)

    def test_manual_artifact_registry_edit_blocks_sync(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        registry_path = self.root / ".upgrade" / "ARTIFACTS.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["entries"].append({
            "path": ".upgrade/security/MANUAL.md", "kind": "governance", "module": "review",
            "lifecycle": "permanent", "git_policy": "tracked", "docker_policy": "exclude",
            "required": True, "expose_in_agents": True, "expose_in_readme": True,
            "purpose": "Manual bypass", "rules": ["Do something."], "classifier_reason": "manual",
        })
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        payload, _ = self.run_cli("sync", expected=2)
        self.assertIn("changed outside the approved artifact-policy lifecycle", payload["errors"][0])

    def test_unregister_removes_policy_links_but_retains_artifact(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        add_plan = self.root / "add.json"
        add, _ = self.run_cli(
            "artifact-policy-review", "--path", "decisions/ARCH_DECISION.md", "--create", "--plan-out", str(add_plan),
        )
        self.run_cli("artifact-policy-apply", "--plan", str(add_plan), "--approve", add["plan_id"])
        artifact = self.root / ".upgrade" / "decisions" / "ARCH_DECISION.md"
        self.assertTrue(artifact.is_file())
        remove_plan = self.root / "remove.json"
        remove, _ = self.run_cli(
            "artifact-policy-review", "--path", ".upgrade/decisions/ARCH_DECISION.md", "--remove", "--plan-out", str(remove_plan),
        )
        self.run_cli("artifact-policy-apply", "--plan", str(remove_plan), "--approve", remove["plan_id"])
        self.assertTrue(artifact.is_file())
        registry = json.loads((self.root / ".upgrade" / "ARTIFACTS.json").read_text(encoding="utf-8"))
        self.assertEqual([], registry["entries"])
        self.assertNotIn("ARCH_DECISION.md", (self.root / ".upgrade" / "AGENTS.md").read_text(encoding="utf-8"))

    def test_registered_governance_artifact_is_retained_from_cleanup(self) -> None:
        self.run_cli("initialize", "--profile", "governed")
        add_plan = self.root / "add.json"
        add, _ = self.run_cli(
            "artifact-policy-review", "--path", "reviews/old-risk-review.md", "--create", "--plan-out", str(add_plan),
        )
        self.run_cli("artifact-policy-apply", "--plan", str(add_plan), "--approve", add["plan_id"])
        cleanup, _ = self.run_cli("cleanup-review")
        sources = {entry["source"] for entry in cleanup["result"]["plan"]["entries"]}
        self.assertNotIn(".upgrade/reviews/old-risk-review.md", sources)

    def test_artifact_policy_plan_is_stale_after_registry_changes(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        first_plan = self.root / "first.json"
        first, _ = self.run_cli(
            "artifact-policy-review", "--path", "docs/EXTRA_NOTES.md", "--create", "--plan-out", str(first_plan),
        )
        second_plan = self.root / "second.json"
        second, _ = self.run_cli(
            "artifact-policy-review", "--path", "security/SECURITY_REVIEW.md", "--create", "--plan-out", str(second_plan),
        )
        self.run_cli("artifact-policy-apply", "--plan", str(second_plan), "--approve", second["plan_id"])
        payload, _ = self.run_cli("artifact-policy-apply", "--plan", str(first_plan), "--approve", first["plan_id"], expected=3)
        self.assertIn("registry changed since review", payload["errors"][0])

    def test_sync_adds_registered_docker_exclusion_when_docker_appears_later(self) -> None:
        self.run_cli("initialize", "--profile", "lean")
        plan = self.root / "policy.json"
        review, _ = self.run_cli(
            "artifact-policy-review", "--path", "security/SECURITY_REVIEW.md", "--create", "--plan-out", str(plan),
        )
        self.run_cli("artifact-policy-apply", "--plan", str(plan), "--approve", review["plan_id"])
        self.assertFalse((self.root / ".dockerignore").exists())
        (self.root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        self.run_cli("sync")
        self.assertIn("/.upgrade/security/SECURITY_REVIEW.md", (self.root / ".dockerignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
