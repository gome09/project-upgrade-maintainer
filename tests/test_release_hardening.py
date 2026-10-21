from __future__ import annotations

import ast
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cli.main import run
from core import constants, policy


ROOT = Path(__file__).resolve().parents[1]


class ReleaseHardeningCase(unittest.TestCase):
    def test_round11_release_metadata_is_consistent(self) -> None:
        self.assertEqual("18.0.1", constants.SKILL_VERSION)
        self.assertEqual(14, constants.POLICY_VERSION)
        self.assertEqual(3, constants.PLAN_SCHEMA_VERSION)
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Skill `18.0.1`, Policy `14`, Plan Schema `3`", skill)
        self.assertNotIn("transitional release metadata", skill)

    def test_definition_of_done_structure_is_present(self) -> None:
        for relative in ("core", "commands", "cli", "subskills/bootstrap", "subskills/lifecycle", "subskills/governance", "subskills/delivery"):
            self.assertTrue((ROOT / relative).is_dir(), relative)
        script = ROOT / "scripts" / "upgrade_workspace.py"
        tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
        functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertEqual(set(), functions)
        imported_main = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "cli.main"
            and any(alias.name == "main" for alias in node.names)
            for node in tree.body
        )
        self.assertTrue(imported_main)
        self.assertLessEqual(len(script.read_text(encoding="utf-8").splitlines()), 20)

    def test_all_persisted_policy_dependencies_exist_and_are_unique(self) -> None:
        for kind in policy.POLICY_DEPENDENCIES:
            with self.subTest(kind=kind):
                dependencies = policy.plan_policy_dependencies(kind)
                self.assertEqual(len(dependencies), len(set(dependencies)))
                self.assertTrue(all((ROOT / relative).is_file() for relative in dependencies))

    def test_router_documents_current_capability_boundaries_without_roadmap_copy(self) -> None:
        governance = (ROOT / "subskills/governance/SKILL.md").read_text(encoding="utf-8")
        delivery = (ROOT / "subskills/delivery/SKILL.md").read_text(encoding="utf-8")
        main = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("`decision-review`, `decision-apply`, `decision-audit`", governance)
        self.assertIn("Delivery consumes Core-validated Decision, Risk, Validation, Exception, and Recovery state", delivery)
        self.assertIn("`recovery-*` | Core recovery", main)
        self.assertIn("references/shared/transactions-and-recovery.md", main)
        self.assertNotIn("## Future Extension Map", main)
        self.assertNotIn("Optional MCP/Plugin integrations", main)

    def test_round10_workspace_fixture_syncs_in_place_without_user_data_loss(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "round10_workspace_lean"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            shutil.copy2(fixture / "AGENTS.md", root / "AGENTS.md")
            shutil.copy2(fixture / "gitignore", root / ".gitignore")
            shutil.copytree(fixture / "upgrade", root / ".upgrade")
            state_before = (root / ".upgrade" / "STATE.md").read_text(encoding="utf-8")
            note_before = (root / ".upgrade" / "docs" / "USER_NOTE.md").read_bytes()

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = run(["--root", str(root), "--format", "json", "sync", "--create-root-bridge"])
            self.assertEqual(0, code, stdout.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertEqual("ok", payload["status"])

            config = json.loads((root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
            self.assertEqual("18.0.1", config["last_synced_skill_version"])
            self.assertEqual(14, config["last_synced_policy_version"])
            self.assertEqual("lean", config["initial_profile"])
            self.assertIn("Preserve this Round 10 state extension.", (root / ".upgrade" / "STATE.md").read_text(encoding="utf-8"))
            self.assertIn("Preserve this Round 10 state extension.", state_before)
            self.assertIn("Preserve this Round 10 manifest tail.", (root / ".upgrade" / "MANIFEST.md").read_text(encoding="utf-8"))
            self.assertEqual(note_before, (root / ".upgrade" / "docs" / "USER_NOTE.md").read_bytes())

            health_out = io.StringIO()
            with contextlib.redirect_stdout(health_out):
                health_code = run(["--root", str(root), "--format", "json", "health-check"])
            self.assertEqual(0, health_code)
            health = json.loads(health_out.getvalue())
            self.assertEqual([], health["errors"])

    def test_release_gate_redlines_have_named_regression_tests(self) -> None:
        required = {
            "test_forged_sensitive_plan_cannot_bypass_apply_time_authorization",
            "test_protected_collect_requires_path_and_action",
            "test_gitignore_literal_rule_escapes_pattern_metacharacters",
            "test_upgrade_symlink_is_rejected_before_lock_write",
            "test_phase_transition_requires_explicit_completion_and_handles_regression",
            "test_manifest_finalization_failure_writes_partial_trace",
            "test_cleanup_archive_destination_is_collision_safe_across_repeated_paths",
            "test_cleanup_trash_destination_is_collision_safe_across_repeated_paths",
            "test_manual_artifact_registry_edit_blocks_sync",
            "test_registered_governance_artifact_is_retained_from_cleanup",
            "test_runtime_artifact_links_ignore_policies_without_agent_rule_growth",
            "test_artifact_policy_apply_creates_governance_artifact_and_links_context",
            "test_initialize_without_profile_uses_safe_lean_footprint",
            "test_missing_adaptive_config_blocks_mutation_until_sync_repairs_it",
            "test_plan_staleness_tracks_related_but_ignores_unrelated_policy_files",
            "test_shared_safety_change_stales_every_current_mutation_plan_kind",
            "test_schema2_plan_is_explicitly_stale_and_requires_regeneration",
            "test_round10_workspace_fixture_syncs_in_place_without_user_data_loss",
            "test_delivery_policy_enforces_minimum_checks_without_explicit_require_check",
            "test_delivery_plan_stales_when_delivery_policy_changes",
            "test_delivery_policy_is_a_canonical_retained_workspace_file",
            "test_health_ci_and_strict_exit_contract_preserves_default_compatibility",
            "test_health_ci_returns_recovery_required_for_unresolved_transaction",
            "test_source_change_after_review_makes_apply_plan_stale",
            "test_authority_drift_is_not_auto_rebound_by_sync",
            "test_manual_authority_registry_edit_blocks_sync",
            "test_previous_schema_sync_migrates_structure_without_auto_registering_candidates",
        }
        available: set[str] = set()
        for source in (ROOT / "tests").glob("test_*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            available.update(
                node.name
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
            )
        self.assertTrue(required <= available, sorted(required - available))


if __name__ == "__main__":
    unittest.main()
