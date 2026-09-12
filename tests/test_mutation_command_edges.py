from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.main import run
from commands import collect as collect_command
from core.errors import CliError


class MutationCommandEdgeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *args: str, expected: int = 0) -> dict:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run(["--root", str(self.root), "--format", "json", *args])
        self.assertEqual(expected, code, stderr.getvalue() + stdout.getvalue())
        return json.loads(stdout.getvalue())

    def test_collect_destination_routing_and_reference_detection_edges(self) -> None:
        cases = {
            ("scratch.py", "archive"): ".upgrade/archive/scripts/scratch.py",
            ("upgrade-plan.md", "copy"): ".upgrade/docs/upgrade-plan.md",
            ("phase-report.md", "copy"): ".upgrade/reports/phase-report.md",
            ("decision-note.md", "copy"): ".upgrade/decisions/decision-note.md",
            ("security-review.md", "copy"): ".upgrade/reviews/security-review.md",
            ("prompt-note.txt", "copy"): ".upgrade/prompts/prompt-note.txt",
            ("debug.log", "copy"): ".upgrade/logs/debug.log",
            ("helper.py", "copy"): ".upgrade/tmp/scripts/helper.py",
            ("notes.txt", "copy"): ".upgrade/tmp/notes.txt",
        }
        for (relative, action), expected in cases.items():
            with self.subTest(relative=relative, action=action):
                self.assertEqual(expected, collect_command.choose_collect_destination(relative, action))

        candidate = self.root / "upgrade-note.txt"
        candidate.write_text("candidate", encoding="utf-8")
        events = "\n".join([
            "not-json",
            json.dumps({"type": "begin", "data": {}}),
            json.dumps({"type": "match", "data": {"path": {"text": "README.md"}, "submatches": [{"match": {"text": candidate.name}}]}}),
        ])
        completed = type("Completed", (), {"returncode": 0, "stdout": events})()
        with patch("commands.collect.shutil.which", return_value="rg"), patch("commands.collect.subprocess.run", return_value=completed):
            self.assertEqual({candidate.name}, collect_command.referenced_candidates(self.root, [candidate]))

        big = self.root / "big.md"
        with big.open("wb") as stream:
            stream.seek(2 * 1024 * 1024 + 1)
            stream.write(b"x")
        with patch("commands.collect.shutil.which", return_value=None):
            self.assertEqual(set(), collect_command.referenced_candidates(self.root, [candidate], [big]))
        small = self.root / "small.md"
        small.write_text("x", encoding="utf-8")
        with patch("commands.collect.shutil.which", return_value=None), patch("commands.collect.read_text_file", side_effect=CliError("bad text")):
            self.assertEqual(set(), collect_command.referenced_candidates(self.root, [candidate], [small]))

    def test_artifact_policy_review_preflight_and_nonexecuting_edges(self) -> None:
        self.cli("initialize", "--profile", "lean")

        missing_plan = self.root / "missing-plan.json"
        review = self.cli(
            "artifact-policy-review", "--path", "notes/missing.md", "--purpose", "Missing content",
            "--plan-out", str(missing_plan),
        )
        self.assertEqual("confirm", review["result"]["plan"]["entries"][0]["action"])
        applied = self.cli("artifact-policy-apply", "--plan", str(missing_plan), "--approve", review["plan_id"])
        self.assertTrue(applied["skipped"])

        remove_plan = self.root / "remove-plan.json"
        remove = self.cli(
            "artifact-policy-review", "--path", "notes/not-registered.md", "--remove", "--plan-out", str(remove_plan)
        )
        self.assertEqual("keep", remove["result"]["plan"]["entries"][0]["action"])
        self.cli("artifact-policy-apply", "--plan", str(remove_plan), "--approve", remove["plan_id"])

        rejected = self.cli(
            "artifact-policy-review", "--path", "notes/x.md", "--purpose", "x", "--remove", "--create", expected=2
        )
        self.assertIn("--remove cannot be combined with --create", rejected["errors"][0])

        notes = self.root / ".upgrade" / "notes"
        notes.mkdir(parents=True, exist_ok=True)
        directory_target = notes / "directory"
        directory_target.mkdir()
        payload = self.cli(
            "artifact-policy-review", "--path", "notes/directory", "--purpose", "x", expected=2
        )
        self.assertIn("target is not a file", payload["errors"][0])

        existing = notes / "existing.md"
        existing.write_text("existing\n", encoding="utf-8")
        payload = self.cli(
            "artifact-policy-review", "--path", "notes/existing.md", "--purpose", "x", "--create", expected=2
        )
        self.assertIn("cannot overwrite", payload["errors"][0])

        registry_path = self.root / ".upgrade" / "ARTIFACTS.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["entries"].append({
            "path": ".upgrade/notes/manual.md", "kind": "content", "module": "core", "lifecycle": "permanent",
            "git_policy": "tracked", "docker_policy": "include", "required": False,
            "expose_in_agents": False, "expose_in_readme": False, "purpose": "Manual edit", "rules": [],
            "classifier_reason": "manual",
        })
        registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        payload = self.cli(
            "artifact-policy-review", "--path", "notes/next.md", "--purpose", "x", expected=2
        )
        self.assertIn("changed outside the approved artifact-policy lifecycle", payload["errors"][0])

    def test_artifact_policy_review_rejects_symlink_target(self) -> None:
        if os.name == "nt":
            self.skipTest("symlink setup is not reliable on Windows without privileges")
        self.cli("initialize", "--profile", "lean")
        notes = self.root / ".upgrade" / "notes"
        notes.mkdir(parents=True, exist_ok=True)
        outside = self.root / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        (notes / "link.md").symlink_to(outside)
        payload = self.cli(
            "artifact-policy-review", "--path", "notes/link.md", "--purpose", "x", expected=2
        )
        self.assertIn("resolves outside allowed boundary", payload["errors"][0])

    def test_cleanup_review_exercises_confirm_archive_and_replacement_edges(self) -> None:
        self.cli("initialize", "--profile", "governed")
        self.cli(
            "phase-update", "--phase", "4", "--current-task", "Hardening",
            "--allow-skip", "--transition-reason", "Release fixture advances directly for cleanup coverage",
        )
        upgrade = self.root / ".upgrade"
        (upgrade / "logs" / "raw.log").write_text("raw\n", encoding="utf-8")
        (upgrade / "checkpoints" / "phase-1-old.note").write_text("old\n", encoding="utf-8")
        (upgrade / "docs" / "draft-old.md").write_text("draft\n", encoding="utf-8")
        (upgrade / "tmp" / "update-me.md").write_text("update\n", encoding="utf-8")
        payload = self.cli(
            "cleanup-review", "--decision", ".upgrade/tmp/update-me.md=update"
        )
        entries = {item["source"]: item for item in payload["result"]["plan"]["entries"]}
        self.assertEqual("confirm", entries[".upgrade/logs/raw.log"]["action"])
        self.assertEqual("archive", entries[".upgrade/checkpoints/phase-1-old.note"]["action"])
        self.assertEqual("archive", entries[".upgrade/docs/draft-old.md"]["action"])
        self.assertEqual("confirm", entries[".upgrade/tmp/update-me.md"]["action"])

    def test_git_policy_review_requires_git_worktree(self) -> None:
        self.cli("initialize", "--profile", "lean")
        payload = self.cli("git-policy-review", expected=2)
        self.assertIn("requires a Git work tree", payload["errors"][0])


if __name__ == "__main__":
    unittest.main()
