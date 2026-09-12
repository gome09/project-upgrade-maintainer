from __future__ import annotations

import contextlib
import io
import json
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
