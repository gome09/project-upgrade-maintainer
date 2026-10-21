from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cli.main import run


class Round14MigrationCase(unittest.TestCase):
    def cli(self, root: Path, *args: str, expected: int = 0) -> dict:
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(["--root", str(root), "--format", "json", *args])
        self.assertEqual(expected, code, err.getvalue() + out.getvalue())
        return json.loads(out.getvalue())

    def test_round13_shaped_workspace_syncs_to_round14_without_governance_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.cli(root, "initialize", "--profile", "governed")

            decision_review = self.cli(
                root,
                "decision-review",
                "--title", "Round 13 direction",
                "--decision", "Keep the compatibility boundary",
                "--plan-out", "decision.json",
            )
            self.cli(
                root,
                "decision-apply",
                "--plan", str(root / "decision.json"),
                "--approve", decision_review["plan_id"],
            )
            risk_review = self.cli(
                root,
                "risk-review",
                "--title", "Round 13 accepted risk",
                "--description", "Known bounded exposure",
                "--status", "accepted",
                "--acceptance-reason", "User explicitly accepted the exposure",
                "--plan-out", "risk.json",
            )
            self.cli(
                root,
                "risk-apply",
                "--plan", str(root / "risk.json"),
                "--approve", risk_review["plan_id"],
            )

            upgrade = root / ".upgrade"
            for round14_dir in ("exceptions", "delivery"):
                path = upgrade / round14_dir
                if path.exists():
                    shutil.rmtree(path)

            config_path = upgrade / "CONFIG.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["last_synced_skill_version"] = "13.0.0"
            config["last_synced_policy_version"] = 9
            config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            state_path = upgrade / "STATE.md"
            state_path.write_text(
                state_path.read_text(encoding="utf-8") + "\n## User Round 13 Note\nPreserve this text.\n",
                encoding="utf-8",
            )
            decision_path = next((upgrade / "decisions").glob("DEC-*.json"))
            risk_path = next((upgrade / "risks").glob("RISK-*.jsonl"))
            decision_bytes = decision_path.read_bytes()
            risk_bytes = risk_path.read_bytes()

            self.cli(root, "sync")

            synced = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual("18.0.1", synced["last_synced_skill_version"])
            self.assertEqual(14, synced["last_synced_policy_version"])
            self.assertTrue((upgrade / "exceptions").is_dir())
            self.assertTrue((upgrade / "delivery").is_dir())
            self.assertEqual(decision_bytes, decision_path.read_bytes())
            self.assertEqual(risk_bytes, risk_path.read_bytes())
            self.assertIn("Preserve this text.", state_path.read_text(encoding="utf-8"))

            health = self.cli(root, "health-check")
            self.assertEqual([], health["errors"])


if __name__ == "__main__":
    unittest.main()
