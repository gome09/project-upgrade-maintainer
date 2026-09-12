from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from cli.main import run
from core.authority import authority_registry_digest, empty_authority_registry
from core.constants import AUTHORITY_MAP_MARKER


class AuthorityGraphCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *args: str, expected: int = 0) -> dict:
        out = io.StringIO(); err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(["--root", str(self.root), "--format", "json", *args])
        self.assertEqual(expected, code, err.getvalue() + out.getvalue())
        return json.loads(out.getvalue())

    def initialize(self) -> None:
        self.cli("initialize", "--profile", "lean", "--create-root-bridge")

    def review_apply(self, *review_args: str, plan_name: str = "authority-plan.json") -> dict:
        review = self.cli("authority-review", *review_args, "--plan-out", plan_name)
        return self.cli(
            "authority-apply", "--plan", str(self.root / plan_name), "--approve", review["plan_id"],
        )

    def test_version_and_initialize_create_authority_control_plane(self) -> None:
        version = self.cli("version")["result"]
        self.assertEqual("18.0.0", version["skill_version"])
        self.assertEqual(14, version["policy_version"])
        self.assertEqual(2, version["workspace_config_schema_version"])
        self.assertEqual([1], version["previous_workspace_config_schema_versions"])
        self.assertEqual(1, version["authority_registry_schema_version"])

        self.initialize()
        upgrade = self.root / ".upgrade"
        config = json.loads((upgrade / "CONFIG.json").read_text(encoding="utf-8"))
        registry = json.loads((upgrade / "AUTHORITY.json").read_text(encoding="utf-8"))
        self.assertEqual(2, config["schema_version"])
        self.assertEqual(authority_registry_digest(registry), config["authority_registry_sha256"])
        self.assertEqual([], registry["entries"])
        self.assertTrue((upgrade / "AUTHORITY.md").read_text(encoding="utf-8").startswith(AUTHORITY_MAP_MARKER + "\n"))
        self.assertIn(".upgrade/AUTHORITY.md", (self.root / "AGENTS.md").read_text(encoding="utf-8"))

    def test_bootstrap_discovers_candidates_without_granting_authority(self) -> None:
        (self.root / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.root / "SECURITY.md").write_text("# Security\n", encoding="utf-8")
        (self.root / "docs" / "adr").mkdir(parents=True)
        (self.root / "docs" / "adr" / "0001.md").write_text("# ADR\n", encoding="utf-8")
        review = self.cli("bootstrap-review")
        discovery = review["result"]["authority_discovery"]
        paths = {item["path"] for item in discovery["candidates"]}
        self.assertEqual({"README.md", "SECURITY.md", "docs/adr/0001.md"}, paths)
        self.assertFalse((self.root / ".upgrade").exists())
        security = next(item for item in discovery["candidates"] if item["path"] == "SECURITY.md")
        self.assertEqual("security_policy", security["recommended_kind"])
        self.assertEqual("repository", security["recommended_authority"])
        self.assertIn("advisory", discovery["note"].casefold())

    def test_authority_review_without_selection_is_non_executable(self) -> None:
        (self.root / "SECURITY.md").write_text("# Security\n", encoding="utf-8")
        self.initialize()
        review = self.cli("authority-review")
        entry = next(item for item in review["result"]["plan"]["entries"] if item["path"] == "SECURITY.md")
        self.assertEqual("confirm", entry["action"])
        self.assertTrue(review["warnings"])
        registry = json.loads((self.root / ".upgrade" / "AUTHORITY.json").read_text(encoding="utf-8"))
        self.assertEqual([], registry["entries"])

    def test_approved_registration_does_not_modify_project_source(self) -> None:
        source = self.root / "SECURITY.md"
        source.write_text("# Security\nDo not weaken authentication.\n", encoding="utf-8")
        before = source.read_bytes()
        self.initialize()
        applied = self.review_apply("--accept", "SECURITY.md")
        self.assertEqual("ok", applied["status"])
        self.assertEqual(before, source.read_bytes())
        registry = json.loads((self.root / ".upgrade" / "AUTHORITY.json").read_text(encoding="utf-8"))
        self.assertEqual(1, len(registry["entries"]))
        entry = registry["entries"][0]
        self.assertEqual("security_policy", entry["kind"])
        self.assertEqual("repository", entry["authority"])
        self.assertFalse(entry["managed"])
        self.assertTrue(entry["required"])
        view = (self.root / ".upgrade" / "AUTHORITY.md").read_text(encoding="utf-8")
        self.assertIn("`SECURITY.md`", view)

    def test_source_change_after_review_makes_apply_plan_stale(self) -> None:
        source = self.root / "SECURITY.md"
        source.write_text("v1\n", encoding="utf-8")
        self.initialize()
        review = self.cli("authority-review", "--accept", "SECURITY.md", "--plan-out", "authority-plan.json")
        source.write_text("v2\n", encoding="utf-8")
        rejected = self.cli(
            "authority-apply", "--plan", str(self.root / "authority-plan.json"), "--approve", review["plan_id"], expected=3,
        )
        self.assertIn("changed since review", rejected["errors"][0])
        registry = json.loads((self.root / ".upgrade" / "AUTHORITY.json").read_text(encoding="utf-8"))
        self.assertEqual([], registry["entries"])

    def test_authority_drift_is_not_auto_rebound_by_sync(self) -> None:
        source = self.root / "SECURITY.md"
        source.write_text("v1\n", encoding="utf-8")
        self.initialize()
        self.review_apply("--accept", "SECURITY.md")
        registry_path = self.root / ".upgrade" / "AUTHORITY.json"
        before = json.loads(registry_path.read_text(encoding="utf-8"))
        before_digest = before["entries"][0]["sha256"]
        source.write_text("v2\n", encoding="utf-8")

        health = self.cli("health-check")
        self.assertTrue(any("content changed and requires review" in item for item in health["warnings"]))
        self.cli("sync")
        after = json.loads(registry_path.read_text(encoding="utf-8"))
        self.assertEqual(before_digest, after["entries"][0]["sha256"])

        self.review_apply("--accept", "SECURITY.md", plan_name="rebind-plan.json")
        rebound = json.loads(registry_path.read_text(encoding="utf-8"))
        self.assertNotEqual(before_digest, rebound["entries"][0]["sha256"])

    def test_manual_authority_registry_edit_blocks_sync(self) -> None:
        source = self.root / "SECURITY.md"
        source.write_text("v1\n", encoding="utf-8")
        self.initialize()
        self.review_apply("--accept", "SECURITY.md")
        registry_path = self.root / ".upgrade" / "AUTHORITY.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["entries"][0]["required"] = False
        registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        blocked = self.cli("sync", expected=2)
        self.assertIn("changed outside the approved authority lifecycle", blocked["errors"][0])

    def test_required_missing_authority_is_health_error(self) -> None:
        source = self.root / "SECURITY.md"
        source.write_text("v1\n", encoding="utf-8")
        self.initialize()
        self.review_apply("--accept", "SECURITY.md")
        source.unlink()
        health = self.cli("health-check")
        self.assertEqual("error", health["status"])
        self.assertTrue(any("registered authority source is missing: SECURITY.md" in item for item in health["errors"]))

    def test_symlink_authority_source_is_rejected(self) -> None:
        self.initialize()
        target = self.root / "real-security.md"
        target.write_text("v1\n", encoding="utf-8")
        link = self.root / "SECURITY.md"
        try:
            os.symlink(target.name, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        rejected = self.cli("authority-review", "--accept", "SECURITY.md", expected=2)
        self.assertIn("must not be a symlink", rejected["errors"][0])

    def test_conflicting_normative_authorities_are_health_errors(self) -> None:
        (self.root / "SECURITY.md").write_text("one\n", encoding="utf-8")
        (self.root / "SECURITY-POLICY.md").write_text("two\n", encoding="utf-8")
        self.initialize()
        review = self.cli(
            "authority-review",
            "--accept", "SECURITY.md",
            "--accept", "SECURITY-POLICY.md",
            "--kind", "SECURITY-POLICY.md=security_policy",
            "--authority", "SECURITY-POLICY.md=repository",
            "--scope", "SECURITY-POLICY.md=.",
            "--required", "SECURITY-POLICY.md=yes",
            "--plan-out", "conflict-plan.json",
        )
        self.cli("authority-apply", "--plan", str(self.root / "conflict-plan.json"), "--approve", review["plan_id"])
        health = self.cli("health-check")
        self.assertEqual("error", health["status"])
        self.assertTrue(any("conflicting normative authority sources" in item for item in health["errors"]))

    def test_previous_schema_sync_migrates_structure_without_auto_registering_candidates(self) -> None:
        (self.root / "SECURITY.md").write_text("security\n", encoding="utf-8")
        self.initialize()
        upgrade = self.root / ".upgrade"
        config_path = upgrade / "CONFIG.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["schema_version"] = 1
        config.pop("authority_registry_sha256")
        config["last_synced_skill_version"] = "17.0.0"
        config["last_synced_policy_version"] = 13
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (upgrade / "AUTHORITY.json").unlink()
        (upgrade / "AUTHORITY.md").unlink()
        state_before = (upgrade / "STATE.md").read_bytes()

        legacy_health = self.cli("health-check")
        self.assertFalse(legacy_health["errors"])
        self.assertTrue(any("schema 1 requires sync migration" in item for item in legacy_health["warnings"]))
        self.cli("sync")

        migrated = json.loads(config_path.read_text(encoding="utf-8"))
        registry = json.loads((upgrade / "AUTHORITY.json").read_text(encoding="utf-8"))
        self.assertEqual(2, migrated["schema_version"])
        self.assertEqual("18.0.0", migrated["last_synced_skill_version"])
        self.assertEqual(14, migrated["last_synced_policy_version"])
        self.assertEqual([], registry["entries"])
        self.assertEqual(state_before, (upgrade / "STATE.md").read_bytes())
        audit = self.cli("authority-audit")
        self.assertIn("SECURITY.md", {item["path"] for item in audit["result"]["unregistered_candidates"]})

    def test_sync_repairs_generated_authority_view_but_not_registry(self) -> None:
        self.initialize()
        registry_path = self.root / ".upgrade" / "AUTHORITY.json"
        before = registry_path.read_bytes()
        view = self.root / ".upgrade" / "AUTHORITY.md"
        view.write_text(view.read_text(encoding="utf-8") + "manual drift\n", encoding="utf-8")
        health = self.cli("health-check")
        self.assertTrue(any("AUTHORITY.md" in item and "stale" in item for item in health["warnings"]))
        self.cli("sync")
        self.assertEqual(before, registry_path.read_bytes())
        self.assertNotIn("manual drift", view.read_text(encoding="utf-8"))

    def test_empty_authority_registry_digest_is_domain_separated(self) -> None:
        from core.artifacts import artifact_registry_digest, empty_artifact_registry
        self.assertNotEqual(
            authority_registry_digest(empty_authority_registry()),
            artifact_registry_digest(empty_artifact_registry()),
        )


if __name__ == "__main__":
    unittest.main()
