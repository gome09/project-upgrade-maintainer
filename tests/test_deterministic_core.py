from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from core import plans, policy
from core.errors import CliError, StalePlanError


SKILL_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = SKILL_ROOT / "core"


class DeterministicCoreCase(unittest.TestCase):
    def test_core_has_no_reverse_dependency_on_compatibility_or_future_command_layers(self) -> None:
        forbidden = ("scripts", "commands", "cli", "subskills")
        for source in sorted(CORE_ROOT.glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            imports: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            with self.subTest(module=source.name):
                self.assertFalse(
                    any(name == item or name.startswith(item + ".") for name in imports for item in forbidden),
                    f"reverse dependency found in {source}: {imports}",
                )

    def test_workspace_infrastructure_is_owned_by_core(self) -> None:
        for relative in ("config.py", "artifacts.py", "manifests.py"):
            self.assertTrue((CORE_ROOT / relative).is_file(), relative)
        self.assertFalse((SKILL_ROOT / "commands" / "_manifest.py").exists())

        workspace_helper = SKILL_ROOT / "commands" / "_workspace.py"
        tree = ast.parse(workspace_helper.read_text(encoding="utf-8"), filename=str(workspace_helper))
        functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertEqual(
            {"modules_for_profile", "module_for_upgrade_destination", "normalized_profile_name"},
            functions,
        )

        implementation_files = set(policy.CORE_IMPLEMENTATION_FILES)
        for relative in ("core/config.py", "core/artifacts.py", "core/manifests.py"):
            self.assertIn(relative, implementation_files)
        shared = set(policy._SHARED_COMMAND_IMPLEMENTATION_FILES)
        self.assertNotIn("commands/_manifest.py", shared)
        self.assertNotIn("commands/_workspace.py", shared)
        self.assertIn("commands/_workspace.py", set(policy.COMMAND_IMPLEMENTATION_FILES_BY_KIND["collect"]))
        self.assertIn("commands/_workspace.py", set(policy.COMMAND_IMPLEMENTATION_FILES_BY_KIND["artifact-policy"]))

    def test_policy_digest_binds_extracted_core_while_plan_schema3_uses_canonical_paths(self) -> None:
        declared = policy.plan_policy_dependencies("collect")
        self.assertEqual(
            (
                "references/shared/safety-and-lifecycle.md",
                "subskills/lifecycle/references/collect-classification.md",
            ),
            declared,
        )
        self.assertNotIn("scripts/upgrade_workspace.py", declared)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in declared:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"baseline:{relative}\n", encoding="utf-8")
            core_path = root / "core" / "paths.py"
            core_path.parent.mkdir(parents=True, exist_ok=True)
            core_path.write_text("baseline core\n", encoding="utf-8")

            baseline = policy.policy_implementation_digest(
                "collect", skill_root=root, implementation_files=("core/paths.py",)
            )
            unrelated = root / "subskills" / "lifecycle" / "references" / "git-policy.md"
            unrelated.parent.mkdir(parents=True, exist_ok=True)
            unrelated.write_text("unrelated change\n", encoding="utf-8")
            self.assertEqual(
                baseline,
                policy.policy_implementation_digest(
                    "collect", skill_root=root, implementation_files=("core/paths.py",)
                ),
            )
            core_path.write_text("changed core\n", encoding="utf-8")
            self.assertNotEqual(
                baseline,
                policy.policy_implementation_digest(
                    "collect", skill_root=root, implementation_files=("core/paths.py",)
                ),
            )

    def test_plan_engine_create_validate_load_and_exact_approval(self) -> None:
        digest = lambda _kind: "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            plan = plans.create_plan(
                root,
                "collect",
                [],
                clock=lambda: "2026-09-10T00:00:00+00:00",
                policy_digest=digest,
            )
            self.assertEqual(3, plan["schema_version"])
            self.assertEqual("collect", plan["kind"])
            self.assertEqual(plans.plan_id(plan), plan["plan_id"])
            plans.assert_exact_approval(plan, plan["plan_id"])
            with self.assertRaisesRegex(CliError, "approval token does not match plan_id"):
                plans.assert_exact_approval(plan, "wrong")

            path = root / "plan.json"
            path.write_text(json.dumps(plan), encoding="utf-8")
            self.assertEqual(plan, plans.load_plan(path, policy_digest=digest))
            self.assertEqual(plan, plans.validate_plan(plan, policy_digest=digest))

    def test_plan_engine_file_snapshot_freshness_uses_round10_hash_and_mtime_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.txt"
            path.write_text("before\n", encoding="utf-8")
            stat = path.stat()
            entry = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "mtime_ns": stat.st_mtime_ns,
            }
            plans.assert_file_snapshot_fresh(path, entry, message="stale")
            path.write_text("after\n", encoding="utf-8")
            with self.assertRaisesRegex(StalePlanError, "stale"):
                plans.assert_file_snapshot_fresh(path, entry, message="stale")


if __name__ == "__main__":
    unittest.main()
