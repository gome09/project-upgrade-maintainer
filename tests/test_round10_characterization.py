from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
CLI = SKILL_ROOT / "scripts" / "upgrade_workspace.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "round10"

RESULT_ENVELOPE_KEYS = {
    "schema_version",
    "command",
    "status",
    "root",
    "changed",
    "skipped",
    "warnings",
    "errors",
    "result",
    "plan_id",
}

ROUND10_COMMANDS = {
    "version",
    "bootstrap-review",
    "initialize",
    "sync",
    "artifact-policy-review",
    "artifact-policy-apply",
    "collect-review",
    "collect-apply",
    "phase-update",
    "cleanup-review",
    "cleanup-apply",
    "artifact-audit",
    "git-audit",
    "git-policy-review",
    "git-policy-apply",
    "validation-record",
    "purge-review",
    "purge-apply",
    "health-check",
}


class Round10CharacterizationCase(unittest.TestCase):
    """Black-box contracts that must survive the Round 11 internal refactor."""

    def run_cli(
        self,
        root: Path,
        *args: str,
        expected: int = 0,
    ) -> tuple[dict, subprocess.CompletedProcess[str]]:
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", str(CLI), "--root", str(root), *args],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(expected, completed.returncode, completed.stderr + completed.stdout)
        payload = json.loads(completed.stdout) if completed.stdout.strip() else {}
        return payload, completed

    def assert_result_envelope(self, payload: dict, *, command: str, root: Path) -> None:
        self.assertEqual(RESULT_ENVELOPE_KEYS, set(payload))
        self.assertEqual(1, payload["schema_version"])
        self.assertEqual(command, payload["command"])
        self.assertEqual(str(root.resolve()), payload["root"])
        self.assertIsInstance(payload["changed"], list)
        self.assertIsInstance(payload["skipped"], list)
        self.assertIsInstance(payload["warnings"], list)
        self.assertIsInstance(payload["errors"], list)
        self.assertIsInstance(payload["result"], dict)
        self.assertTrue(payload["plan_id"] is None or isinstance(payload["plan_id"], str))

    def test_cli_command_surface_and_result_envelope_contract(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", str(CLI), "--help"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        command_choice = next(
            match.group(1)
            for match in re.finditer(r"\{([^{}]+)\}", completed.stdout)
            if "version" in match.group(1)
        )
        self.assertTrue(ROUND10_COMMANDS <= set(command_choice.split(",")))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload, _ = self.run_cli(root, "version")
            self.assert_result_envelope(payload, command="version", root=root)
            self.assertEqual("ok", payload["status"])
            self.assertEqual([], payload["changed"])
            self.assertEqual([], payload["skipped"])
            self.assertEqual([], payload["warnings"])
            self.assertEqual([], payload["errors"])
            self.assertIsNone(payload["plan_id"])
            self.assertEqual(
                {
                    "skill_version",
                    "policy_version",
                    "plan_schema_version",
                    "previous_plan_schema_versions",
                    "workspace_config_schema_version",
                    "previous_workspace_config_schema_versions",
                    "authority_registry_schema_version",
                    "artifact_registry_schema_version",
                    "decision_schema_version", "risk_schema_version", "validation_schema_version", "transaction_schema_version",
                    "exception_schema_version", "delivery_schema_version", "delivery_policy_schema_version",
                    "health_ci_exit_codes", "plan_policy_scope",
                },
                set(payload["result"]),
            )
            self.assertEqual("18.0.1", payload["result"]["skill_version"])
            self.assertEqual(14, payload["result"]["policy_version"])
            self.assertEqual(3, payload["result"]["plan_schema_version"])
            self.assertEqual([2], payload["result"]["previous_plan_schema_versions"])
            self.assertEqual(2, payload["result"]["workspace_config_schema_version"])
            self.assertEqual([1], payload["result"]["previous_workspace_config_schema_versions"])
            self.assertEqual(1, payload["result"]["authority_registry_schema_version"])
            self.assertEqual(1, payload["result"]["artifact_registry_schema_version"])
            self.assertEqual("per-kind-policy-dependencies", payload["result"]["plan_policy_scope"])

    def test_exit_code_contract_zero_two_three_four(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            ok, _ = self.run_cli(root, "version", expected=0)
            self.assertEqual("ok", ok["status"])

            parser_error = subprocess.run(
                [sys.executable, "-X", "utf8", str(CLI), "--root", str(root), "phase-update"],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, parser_error.returncode)
            self.assertEqual("", parser_error.stdout)
            self.assertIn("the following arguments are required", parser_error.stderr)

            self.run_cli(root, "initialize", "--profile", "lean")
            source = root / "upgrade-notes.txt"
            source.write_text("before\n", encoding="utf-8")
            plan_path = root / "collect-plan.json"
            review, _ = self.run_cli(
                root,
                "collect-review",
                "--focus",
                source.name,
                "--decision",
                f"{source.name}=copy",
                "--plan-out",
                str(plan_path),
            )

            wrong_approval, _ = self.run_cli(
                root,
                "collect-apply",
                "--plan",
                str(plan_path),
                "--approve",
                "wrong",
                expected=2,
            )
            self.assertEqual("error", wrong_approval["status"])
            self.assertIn("approval token does not match plan_id", wrong_approval["errors"])

            source.write_text("after\n", encoding="utf-8")
            stale, _ = self.run_cli(
                root,
                "collect-apply",
                "--plan",
                str(plan_path),
                "--approve",
                review["plan_id"],
                expected=3,
            )
            self.assertEqual("error", stale["status"])
            self.assertTrue(any("changed or disappeared" in item for item in stale["errors"]))

        # Lock the current partial-result exit code using a post-mutation output
        # collision: initialize creates .upgrade/STATE.md before emit() tries to
        # use that same path as --output.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            partial, _ = self.run_cli(
                root,
                "--output",
                ".upgrade/STATE.md",
                "initialize",
                "--profile",
                "lean",
                expected=4,
            )
            self.assertEqual("partial", partial["status"])
            self.assertTrue(partial["changed"])
            self.assertTrue((root / ".upgrade" / "STATE.md").is_file())
            self.assertIn("refusing to overwrite output file", partial["errors"][-1])

    def test_health_error_status_is_still_exit_zero_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload, _ = self.run_cli(root, "health-check", expected=0)
            self.assert_result_envelope(payload, command="health-check", root=root)
            self.assertEqual("error", payload["status"])
            self.assertEqual([], payload["errors"])
            self.assertEqual(
                {"正常项", "警告项", "错误项", "建议动作"},
                set(payload["result"]),
            )
            self.assertIn(".upgrade directory is missing", payload["result"]["错误项"])

    def test_collect_plan_json_shape_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_cli(root, "initialize", "--profile", "lean")
            source = root / "upgrade-notes.txt"
            source.write_text("notes\n", encoding="utf-8")
            plan_path = root / "collect-plan.json"
            review, _ = self.run_cli(
                root,
                "collect-review",
                "--focus",
                source.name,
                "--decision",
                f"{source.name}=copy",
                "--plan-out",
                str(plan_path),
            )

            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "schema_version",
                    "skill_version",
                    "policy_version",
                    "policy_dependencies",
                    "implementation_sha256",
                    "kind",
                    "root",
                    "created_at",
                    "entries",
                    "plan_id",
                },
                set(plan),
            )
            self.assertEqual(3, plan["schema_version"])
            self.assertEqual("18.0.1", plan["skill_version"])
            self.assertEqual(14, plan["policy_version"])
            self.assertEqual("collect", plan["kind"])
            self.assertEqual(str(root.resolve()), plan["root"])
            self.assertRegex(plan["implementation_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(plan["plan_id"], r"^[0-9a-f]{64}$")
            dt.datetime.fromisoformat(plan["created_at"])
            self.assertEqual(
                [
                    "references/shared/safety-and-lifecycle.md",
                    "subskills/lifecycle/references/collect-classification.md",
                ],
                plan["policy_dependencies"],
            )
            self.assertEqual(plan["plan_id"], review["plan_id"])
            self.assertEqual(1, len(plan["entries"]))
            entry = plan["entries"][0]
            self.assertEqual(
                {
                    "source",
                    "destination",
                    "action",
                    "classification",
                    "reason",
                    "protected",
                    "protected_authorized",
                    "sensitive",
                    "sensitive_authorized",
                    "confirmation_required",
                    "referenced",
                    "references_checked",
                    "sha256",
                    "size",
                    "mtime_ns",
                },
                set(entry),
            )
            self.assertEqual("upgrade-notes.txt", entry["source"])
            self.assertEqual(".upgrade/tmp/upgrade-notes.txt", entry["destination"])
            self.assertEqual("copy", entry["action"])
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
            self.assertIsInstance(entry["size"], int)
            self.assertIsInstance(entry["mtime_ns"], int)

    def test_health_json_shape_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            self.run_cli(root, "initialize", "--profile", "governed")
            payload, _ = self.run_cli(root, "health-check")
            self.assert_result_envelope(payload, command="health-check", root=root)
            self.assertIn(payload["status"], {"ok", "warning"})
            self.assertEqual([], payload["changed"])
            self.assertEqual([], payload["skipped"])
            self.assertEqual([], payload["errors"])

            result = payload["result"]
            self.assertEqual(
                {"正常项", "警告项", "错误项", "建议动作", "thresholds", "git_status", "workspace", "governance"},
                set(result),
            )
            self.assertEqual(
                {"suggested", "tmp", "trash", "logs", "old_traces", "old_checkpoints"},
                set(result["thresholds"]),
            )
            for key in ("tmp", "trash", "logs"):
                self.assertEqual({"files", "bytes"}, set(result["thresholds"][key]))

            workspace = result["workspace"]
            self.assertEqual(
                {"config_status", "config_schema_version", "active_modules", "inactive_modules", "initial_profile", "selection_source", "artifact_registry"},
                set(workspace),
            )
            self.assertEqual(
                {"status", "sha256", "entries"},
                set(workspace["artifact_registry"]),
            )
            self.assertRegex(workspace["artifact_registry"]["sha256"], r"^[0-9a-f]{64}$")

            governance = result["governance"]
            self.assertEqual(
                {"contract", "authority", "root_bridges", "nested_bridges", "nested_bridge_candidates"},
                set(governance),
            )
            self.assertEqual({"status"}, set(governance["contract"]))

    def test_config_json_shape_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_cli(root, "initialize", "--profile", "governed")
            config = json.loads((root / ".upgrade" / "CONFIG.json").read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "schema_version",
                    "managed_by",
                    "initial_profile",
                    "active_modules",
                    "selection_source",
                    "bootstrap_recommendation",
                    "created_at",
                    "updated_at",
                    "last_synced_skill_version",
                    "last_synced_policy_version",
                    "artifact_registry_sha256",
                    "authority_registry_sha256",
                },
                set(config),
            )
            self.assertEqual(2, config["schema_version"])
            self.assertEqual("project-upgrade-maintainer", config["managed_by"])
            self.assertEqual("governed", config["initial_profile"])
            self.assertEqual(
                ["core", "planning", "phase", "validation", "reporting", "review", "lifecycle"],
                config["active_modules"],
            )
            self.assertEqual("explicit", config["selection_source"])
            self.assertIsNone(config["bootstrap_recommendation"])
            self.assertEqual("18.0.1", config["last_synced_skill_version"])
            self.assertEqual(14, config["last_synced_policy_version"])
            self.assertRegex(config["artifact_registry_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(config["authority_registry_sha256"], r"^[0-9a-f]{64}$")
            dt.datetime.fromisoformat(config["created_at"])
            dt.datetime.fromisoformat(config["updated_at"])

    def test_artifact_registry_json_shape_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_cli(root, "initialize", "--profile", "lean")
            registry_path = root / ".upgrade" / "ARTIFACTS.json"
            initial = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual({"schema_version", "managed_by", "entries"}, set(initial))
            self.assertEqual(1, initial["schema_version"])
            self.assertEqual("project-upgrade-maintainer", initial["managed_by"])
            self.assertEqual([], initial["entries"])

            plan_path = root / "artifact-policy.json"
            review, _ = self.run_cli(
                root,
                "artifact-policy-review",
                "--path",
                "runtime/debug.log",
                "--purpose",
                "Raw debug output",
                "--create",
                "--plan-out",
                str(plan_path),
            )
            self.run_cli(
                root,
                "artifact-policy-apply",
                "--plan",
                str(plan_path),
                "--approve",
                review["plan_id"],
            )
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual({"schema_version", "managed_by", "entries"}, set(registry))
            self.assertEqual(1, len(registry["entries"]))
            entry = registry["entries"][0]
            self.assertEqual(
                {
                    "classifier_reason",
                    "docker_policy",
                    "expose_in_agents",
                    "expose_in_readme",
                    "git_policy",
                    "kind",
                    "lifecycle",
                    "module",
                    "path",
                    "purpose",
                    "required",
                    "rules",
                },
                set(entry),
            )
            self.assertEqual(".upgrade/runtime/debug.log", entry["path"])
            self.assertEqual("runtime", entry["kind"])
            self.assertEqual("temporary", entry["lifecycle"])
            self.assertEqual("lifecycle", entry["module"])
            self.assertEqual("ignore", entry["git_policy"])
            self.assertEqual("exclude", entry["docker_policy"])
            self.assertFalse(entry["required"])
            self.assertFalse(entry["expose_in_agents"])
            self.assertFalse(entry["expose_in_readme"])
            self.assertEqual([], entry["rules"])

    def test_managed_text_outputs_match_round10_golden_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            self.run_cli(root, "initialize", "--profile", "governed")

            actual_to_golden = {
                root / ".upgrade" / "AGENTS.md": FIXTURES / "AGENTS.md.golden",
                root / ".upgrade" / "README.md": FIXTURES / "README.md.golden",
                root / ".gitignore": FIXTURES / "gitignore.golden",
                root / ".dockerignore": FIXTURES / "dockerignore.golden",
            }
            for actual, golden in actual_to_golden.items():
                with self.subTest(path=actual.name):
                    expected = golden.read_text(encoding="utf-8")
                    observed = actual.read_text(encoding="utf-8")
                    if actual.name in {".gitignore", ".dockerignore"}:
                        observed = observed.replace(".upgrade/transactions/*\n!.upgrade/transactions/.gitkeep\n", "")
                    self.assertEqual(expected, observed, f"Round 10 managed output changed: {actual}")


if __name__ == "__main__":
    unittest.main()
