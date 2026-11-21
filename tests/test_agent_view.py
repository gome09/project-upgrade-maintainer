from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cli.main import run
from core.agent_view import agent_payload
from core.audit import Result


class AgentViewCase(unittest.TestCase):
    def run_json(self, root: Path, *args: str, expected: int = 0) -> dict:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run(["--root", str(root), "--format", "json", *args])
        self.assertEqual(expected, code, stderr.getvalue() or stdout.getvalue())
        return json.loads(stdout.getvalue())

    def initialized_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        self.run_json(root, "initialize", "--profile", "standard")
        return temp, root

    def test_default_full_view_remains_backward_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = self.run_json(root, "version")
            self.assertNotIn("view", payload)
            self.assertIn("result", payload)
            self.assertIn("changed", payload)

    def test_agent_review_omits_full_entries_but_surfaces_decisions(self) -> None:
        temp, root = self.initialized_root()
        with temp:
            candidate = root / ".upgrade" / "tmp" / "demo.tmp"
            candidate.write_text("demo\n", encoding="utf-8")
            payload = self.run_json(
                root,
                "--view", "agent",
                "cleanup-review",
                "--plan-out", ".upgrade/tmp/cleanup-plan.json",
            )
            self.assertEqual("agent", payload["view"])
            self.assertEqual("cleanup", payload["plan"]["kind"])
            self.assertTrue(payload["plan"]["persisted"])
            self.assertEqual(1, payload["plan"]["action_counts"]["delete"])
            self.assertNotIn("entries", payload["plan"])
            deletion = next(item for item in payload["attention"] if item.get("action") == "delete")
            self.assertEqual(".upgrade/tmp/demo.tmp", deletion["source"])
            self.assertIn("recoverable-delete", deletion["codes"])
            self.assertEqual("entry:0", deletion["detail_ref"])

    def test_inspect_returns_bounded_full_entry(self) -> None:
        temp, root = self.initialized_root()
        with temp:
            candidate = root / ".upgrade" / "tmp" / "demo.tmp"
            candidate.write_text("demo\n", encoding="utf-8")
            review = self.run_json(
                root,
                "--view", "agent",
                "cleanup-review",
                "--plan-out", ".upgrade/tmp/cleanup-plan.json",
            )
            detail = self.run_json(
                root,
                "--view", "agent",
                "inspect",
                "--plan", review["plan"]["path"],
                "--index", "0",
                "--limit", "1",
            )
            self.assertEqual("inspect", detail["command"])
            self.assertEqual(1, detail["result"]["returned"])
            entry = detail["result"]["entries"][0]
            self.assertEqual(".upgrade/tmp/demo.tmp", entry["source"])
            self.assertIn("sha256", entry)
            self.assertEqual("recoverable-delete", entry["_attention"][0]["code"])

    def test_collect_move_and_archive_are_decisions_but_copy_is_routine(self) -> None:
        cases = (
            ("move", "debug.log", ".upgrade/logs/debug.log", "review-attention-before-approval"),
            ("archive", "upgrade-plan.md", ".upgrade/archive/upgrade-plan.md", "review-attention-before-approval"),
            ("copy", "upgrade-plan.md", ".upgrade/docs/upgrade-plan.md", "review-summary-before-approval"),
        )
        for action, source, dest, next_action in cases:
            with self.subTest(action=action):
                temp, root = self.initialized_root()
                with temp:
                    (root / source).write_text("test content only\n", encoding="utf-8")
                    review = self.run_json(root, "--view", "agent", "collect-review", "--focus", source,
                                           "--decision", f"{source}={action}", "--plan-out", "collect.json")
                    self.assertEqual(next_action, review["next"]["action"])
                    self.assertEqual("exact-plan-id", review["decision"]["approval_scope"])
                    self.assertEqual(1, review["plan"]["entry_count"])
                    detail = self.run_json(root, "--view", "agent", "inspect", "--plan", "collect.json",
                                           "--section", "attention")["result"]
                    if action == "copy":
                        self.assertEqual([], review["attention"])
                        self.assertEqual([], detail["entries"])
                        continue
                    self.assertEqual(1, detail["total"])
                    self.assertEqual(1, len(review["attention"]))
                    item, entry = review["attention"][0], detail["entries"][0]
                    self.assertEqual((action, source, dest), (item["action"], item["source"], item["destination"]))
                    self.assertEqual((action, source, dest), (entry["action"], entry["source"], entry["destination"]))
                    self.assertEqual(f"entry:{entry['index']}", item["detail_ref"])
                    self.assertEqual([{"code": "source-relocation", "severity": "decision"}], entry["_attention"])
                    self.assertEqual(["source-relocation"], item["codes"])

    def test_sensitive_audit_entry_without_git_mismatch_remains_visible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = "fixture-placeholder-value"
            (root / ".env").write_text(secret, encoding="utf-8")
            full = self.run_json(root, "artifact-audit")["result"]["entries"][0]
            self.assertEqual("sensitive", full["classification"])
            self.assertFalse(full["git_mismatch"])
            payload = self.run_json(root, "--view", "agent", "artifact-audit")
            item, = payload["attention"]
            self.assertEqual("sensitive-material", item["code"])
            self.assertEqual("warning", item["severity"])
            self.assertEqual(".env", item["path"])
            self.assertEqual("sensitive", item["classification"])
            self.assertEqual(full["reason"], item["reason"])
            self.assertEqual("full-view", item["detail_ref"])
            self.assertEqual("review-warnings", payload["next"]["action"])
            self.assertNotIn(secret, json.dumps(payload))

    def test_attention_preserves_blockers_conflicts_and_permanent_deletion(self) -> None:
        result = Result(command="purge-review", root=Path("/repo"))
        result.result = {
            "plan": {"kind": "purge", "plan_id": "p", "entries": [
                {"source": "archived.txt", "action": "purge", "blockers": ["pending"],
                 "conflicts": ["changed"], "sensitive": True},
            ]},
            "findings": [{"path": ".env", "classification": "sensitive", "reason": "sensitive name",
                          "conflicts": ["changed"]}],
        }
        payload = agent_payload(result)
        deletion, audit = payload["attention"]
        self.assertEqual("blocker", deletion["severity"])
        self.assertEqual({"permanent-delete", "blocker", "recovery-conflict", "sensitive-material"},
                         set(deletion["codes"]))
        self.assertEqual(["pending"], deletion["blockers"])
        self.assertEqual(["changed"], deletion["conflicts"])
        self.assertEqual(("conflict", "blocker", ".env", "sensitive", "full-view"),
                         (audit["code"], audit["severity"], audit["path"], audit["classification"], audit["detail_ref"]))
        self.assertEqual("resolve-blockers-and-regenerate-plan", payload["next"]["action"])

    def test_inspect_summary_terminates_and_detail_sections_paginate_and_filter(self) -> None:
        temp, root = self.initialized_root()
        with temp:
            for name in ("debug-one.log", "debug-two.log", "upgrade-plan.md"):
                (root / name).write_text("test content\n", encoding="utf-8")
            self.run_json(root, "collect-review", "--plan-out", "collect.json")

            def inspect(section: str, *args: str) -> dict:
                return self.run_json(root, "inspect", "--plan", "collect.json",
                                     "--section", section, "--limit", "1", *args)["result"]

            for filters, total in (((), 3), (("--offset", "1"), 3), (("--action", "move"), 2),
                                   (("--class", "no-match"), 0)):
                with self.subTest(filters=filters):
                    summary = inspect("summary", *filters)
                    self.assertEqual(total, summary["total"])
                    self.assertEqual((0, False, None, []), (summary["returned"], summary["has_more"],
                                                            summary["next_offset"], summary["entries"]))
            for section, total in (("entries", 3), ("attention", 2)):
                indexes = []
                for offset in range(total):
                    page = inspect(section, "--offset", str(offset))
                    self.assertEqual(total, page["total"])
                    self.assertEqual(1, page["returned"])
                    self.assertEqual(offset < total - 1, page["has_more"])
                    self.assertEqual(offset + 1 if offset < total - 1 else None, page["next_offset"])
                    indexes.append(page["entries"][0]["index"])
                self.assertEqual(sorted(set(indexes)), indexes)
            page = inspect("attention", "--path", ".upgrade/logs/debug-two.log",
                           "--class", "move", "--action", "move")
            self.assertEqual(1, page["total"])
            entry = page["entries"][0]
            self.assertEqual("debug-two.log", entry["source"])
            self.assertEqual([entry], inspect("entries", "--index", str(entry["index"]))["entries"])

    def test_review_inspect_and_apply_plan_paths_from_other_directory(self) -> None:
        temp, root = self.initialized_root()
        with temp, tempfile.TemporaryDirectory() as other:
            cli = Path(__file__).resolve().parents[1] / "scripts" / "upgrade_workspace.py"

            def call(*args, expected=0):
                completed = subprocess.run([sys.executable, "-B", str(cli), "--root", str(root), *args],
                                           cwd=other, capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(expected, completed.returncode, completed.stderr + completed.stdout)
                return json.loads(completed.stdout)

            (root / "debug.log").write_text("test content\n", encoding="utf-8")
            review = call("collect-review", "--plan-out", "collect.json")
            detail = call("inspect", "--plan", "collect.json")
            self.assertEqual(review["plan_id"], detail["plan_id"])
            self.assertTrue((root / "collect.json").is_file())
            self.assertFalse((Path(other) / "collect.json").exists())
            call("inspect", "--plan", str(Path(other) / "collect.json"), expected=2)
            call("collect-apply", "--plan", "collect.json", "--approve", review["plan_id"], expected=2)
            call("collect-apply", "--plan", str(root / "collect.json"), "--approve", review["plan_id"])
            self.assertTrue((root / ".upgrade/logs/debug.log").is_file())

    def test_unpersisted_agent_plan_requires_persistence_before_apply(self) -> None:
        temp, root = self.initialized_root()
        with temp:
            candidate = root / ".upgrade" / "tmp" / "demo.tmp"
            candidate.write_text("demo\n", encoding="utf-8")
            payload = self.run_json(root, "--view", "agent", "cleanup-review")
            self.assertFalse(payload["plan"]["persisted"])
            self.assertEqual("persist-plan-before-apply", payload["next"]["action"])

    def test_purge_decision_surface_never_hides_permanent_delete_subjects(self) -> None:
        root = Path("/repo")
        result = Result(command="purge-review", root=root)
        plan = {
            "kind": "purge",
            "plan_id": "p",
            "entries": [
                {"source": f".upgrade/tmp/.trash/{index}.tmp", "action": "purge", "classification": "permanent-delete", "confirmation_required": True}
                for index in range(25)
            ],
        }
        result.plan_id = "p"
        result.result = {"plan": plan, "summary": {"purge": 25}}
        payload = agent_payload(result)
        self.assertEqual(25, len(payload["attention"]))
        self.assertEqual(25, payload["attention_summary"]["permanent-delete"])
        self.assertEqual(25, payload["plan"]["entry_count"])


if __name__ == "__main__":
    unittest.main()
