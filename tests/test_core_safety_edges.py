from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import paths, plans, policy, schemas
from core.audit import Result
from core.errors import CliError, StalePlanError


class CoreSafetyEdgeCase(unittest.TestCase):
    def test_path_boundary_and_normalization_error_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.assertEqual("alpha/beta", paths.normalize_user_path("././alpha/beta/"))
            self.assertEqual(["a", "b"], paths.normalize_focuses(root, ["a", "./a/", "b/"]))
            self.assertEqual([], paths.normalize_focuses(root, [".", "a"]))
            self.assertEqual([":(literal)a", ":(literal)b"], paths.literal_git_pathspecs(["a", "b"]))
            with self.assertRaisesRegex(CliError, "repository root is not a directory"):
                paths.root_from(str(root / "missing"))
            with self.assertRaisesRegex(CliError, r"\.upgrade does not exist"):
                paths.verified_upgrade(root)
            (root / ".upgrade").write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(CliError, "not a directory"):
                paths.verified_upgrade(root)

        with patch("core.paths.os.path.commonpath", side_effect=ValueError("cross-device")):
            self.assertFalse(paths.is_within(Path("/tmp/a"), Path("/tmp")))
        with patch("core.paths.os.path.relpath", side_effect=ValueError("cross-device")):
            with self.assertRaisesRegex(CliError, "focus resolves outside allowed boundary"):
                paths.normalize_focuses(Path("/tmp"), ["x"])

    def test_verified_upgrade_rejects_symlink(self) -> None:
        if os.name == "nt":
            self.skipTest("symlink setup is not reliable on Windows without privileges")
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            root = Path(directory).resolve()
            (root / ".upgrade").symlink_to(Path(other), target_is_directory=True)
            with self.assertRaisesRegex(CliError, "real local directory"):
                paths.verified_upgrade(root, must_exist=False)

    def test_plan_validation_error_edges_and_output_guards(self) -> None:
        digest = lambda _kind: "d" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            base = plans.create_plan(root, "collect", [], clock=lambda: "2026-09-10T00:00:00+00:00", policy_digest=digest)

            invalid_schema = dict(base, schema_version=99)
            with self.assertRaisesRegex(CliError, "unsupported or invalid"):
                plans.validate_plan(invalid_schema, policy_digest=digest)
            old_version = dict(base, skill_version="10.0.0", policy_version=6)
            with self.assertRaisesRegex(StalePlanError, "different skill or policy version"):
                plans.validate_plan(old_version, policy_digest=digest)
            missing_kind = dict(base)
            missing_kind["kind"] = None
            with self.assertRaisesRegex(CliError, "plan kind is missing"):
                plans.validate_plan(missing_kind, policy_digest=digest)
            with self.assertRaisesRegex(CliError, "unsupported or invalid"):
                plans.validate_plan([], policy_digest=digest)

            bad_json = root / "bad.json"
            bad_json.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(CliError, "cannot read plan"):
                plans.load_plan(bad_json, policy_digest=digest)

            mismatch = dict(base)
            with self.assertRaisesRegex(CliError, "kind or repository root"):
                plans.validate_apply_plan(root, mismatch, mismatch["plan_id"], "cleanup")
            no_entries = dict(base)
            no_entries["entries"] = "bad"
            no_entries["plan_id"] = plans.plan_id(no_entries)
            with self.assertRaisesRegex(CliError, "plan entries must be a list"):
                plans.validate_apply_plan(root, no_entries, no_entries["plan_id"], "collect")

            result = Result(command="collect-review", root=root)
            plans.write_plan_output(root, None, base, result)
            absolute = root / "plan.json"
            plans.write_plan_output(root, str(absolute), base, result)
            self.assertTrue(absolute.is_file())
            with self.assertRaisesRegex(CliError, "refusing to overwrite"):
                plans.resolve_output_target(root, str(absolute))

    def test_policy_registry_error_edges_and_deduplication(self) -> None:
        with self.assertRaisesRegex(CliError, "unsupported plan kind"):
            policy.policy_dependency_ids("nope")
        with self.assertRaisesRegex(CliError, "unknown logical policy dependency"):
            policy.resolve_policy_dependencies(("shared", "nope"))
        with self.assertRaisesRegex(CliError, "unsupported plan kind"):
            policy.implementation_files_for_kind("nope")
        resolved = policy.resolve_policy_dependencies(("shared", "shared"))
        self.assertEqual(policy.POLICY_FILES_BY_ID["shared"], resolved)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CliError, "plan policy file is missing"):
                policy.policy_implementation_digest("collect", skill_root=Path(directory), implementation_files=())

    def test_schema_validation_rejects_invalid_managed_state(self) -> None:
        bad_configs = [
            None,
            {"schema_version": 99},
            {"schema_version": 1, "managed_by": "other"},
            {"schema_version": 1, "managed_by": "project-upgrade-maintainer", "active_modules": 1},
            {"schema_version": 1, "managed_by": "project-upgrade-maintainer", "active_modules": ["core"], "initial_profile": "bad"},
            {"schema_version": 1, "managed_by": "project-upgrade-maintainer", "active_modules": ["core"], "initial_profile": "lean", "artifact_registry_sha256": "bad"},
        ]
        for payload in bad_configs:
            with self.subTest(payload=payload), self.assertRaises(CliError):
                schemas.validate_workspace_config(payload)
        with self.assertRaisesRegex(CliError, "unknown workspace module"):
            schemas.ordered_modules(["future"])

        for value in (1, "x\ny", "x\x00y", "x" * 501):
            with self.subTest(value=repr(value)), self.assertRaises(CliError):
                schemas._single_line_policy_text(value, "purpose", allow_empty=False)
        with self.assertRaises(CliError):
            schemas._single_line_policy_text("   ", "purpose", allow_empty=False)
        with self.assertRaises(CliError):
            schemas._single_line_policy_text("<!-- project-upgrade-maintainer:start -->", "purpose")

        for value in ("/absolute", "../escape", ".upgrade/STATE.md"):
            with self.subTest(value=value), self.assertRaises(CliError):
                schemas.normalize_registered_artifact_path(value)

        base = {
            "path": "notes/a.md", "kind": "content", "module": "core", "lifecycle": "permanent",
            "git_policy": "tracked", "docker_policy": "include", "required": True,
            "expose_in_agents": False, "expose_in_readme": False, "purpose": "test", "rules": [],
            "classifier_reason": "test",
        }
        invalid_mutations = {
            "kind": "bad", "module": "bad", "lifecycle": "bad", "git_policy": "bad", "docker_policy": "bad",
            "required": "yes", "expose_in_agents": "yes", "expose_in_readme": "yes", "rules": "bad",
        }
        with self.assertRaises(CliError):
            schemas.validate_artifact_policy_entry([])
        for key, value in invalid_mutations.items():
            entry = dict(base, **{key: value})
            with self.subTest(key=key), self.assertRaises(CliError):
                schemas.validate_artifact_policy_entry(entry)

        bad_registries = [
            None,
            {"schema_version": 99},
            {"schema_version": 1, "managed_by": "other"},
            {"schema_version": 1, "managed_by": "project-upgrade-maintainer", "entries": "bad"},
        ]
        for payload in bad_registries:
            with self.subTest(payload=payload), self.assertRaises(CliError):
                schemas.validate_artifact_registry(payload)
        duplicate = {"schema_version": 1, "managed_by": "project-upgrade-maintainer", "entries": [base, dict(base)]}
        with self.assertRaisesRegex(CliError, "duplicate artifact policy path"):
            schemas.validate_artifact_registry(duplicate)


if __name__ == "__main__":
    unittest.main()
