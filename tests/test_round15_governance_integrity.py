from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cli.main import run
from core.schemas import governance_record_digest


class Round15GovernanceIntegrityCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *args: str, expected: int = 0) -> dict:
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(["--root", str(self.root), "--format", "json", *args])
        self.assertEqual(expected, code, err.getvalue() + out.getvalue())
        return json.loads(out.getvalue())

    def initialize(self) -> Path:
        self.cli("initialize", "--profile", "lean")
        subject = self.root / "src"
        subject.mkdir()
        (subject / "app.txt").write_text("alpha\n", encoding="utf-8")
        return subject

    def validation(self, check: str = "release-suite", *, scope: str = "release", environment: str = "prod") -> dict:
        return self.cli(
            "validation-record", "--check", check, "--status", "passed", "--exit-code", "0",
            "--scope", scope, "--environment", environment, "--subject-path", "src",
        )

    def delivery_review(self, *extra: str, gate: str = "readiness", check: str = "release-suite") -> dict:
        return self.cli(
            "delivery-review", "--gate", gate, "--target", "v15", "--scope", "release",
            "--environment", "prod", "--require-check", check, "--subject-path", "src", *extra,
        )

    def test_delivery_plan_is_stale_when_bound_subject_changes_after_review(self) -> None:
        self.initialize()
        self.validation()
        review = self.delivery_review("--plan-out", ".upgrade/delivery.json")
        self.assertTrue(review["result"]["eligible"])
        (self.root / "src" / "app.txt").write_text("beta\n", encoding="utf-8")
        rejected = self.cli(
            "delivery-apply", "--plan", str(self.root / ".upgrade" / "delivery.json"),
            "--approve", review["plan_id"], expected=3,
        )
        self.assertTrue(any("subject changed" in message for message in rejected["errors"]))

    def test_required_validation_must_match_check_subject_scope_and_environment(self) -> None:
        self.initialize()
        self.validation(check="smoke", scope="release", environment="staging")
        review = self.delivery_review(check="release-suite")
        self.assertFalse(review["result"]["eligible"])
        self.assertTrue(any("required validation 'release-suite' has no evidence" in item for item in review["result"]["blockers"]))

        self.validation(check="release-suite", scope="release", environment="prod")
        ready = self.delivery_review(check="release-suite")
        self.assertTrue(ready["result"]["eligible"])

    def test_decision_persists_exact_approval_plan_and_detects_tampering(self) -> None:
        self.initialize()
        review = self.cli(
            "decision-review", "--title", "Direction", "--decision", "Use subject-bound delivery",
            "--scope", "release", "--plan-out", ".upgrade/decision.json",
        )
        applied = self.cli(
            "decision-apply", "--plan", str(self.root / ".upgrade" / "decision.json"),
            "--approve", review["plan_id"],
        )
        record = applied["result"]["decision"]
        self.assertEqual(review["plan_id"], record["approval_plan_id"])
        self.assertEqual(64, len(record["record_digest"]))

        path = self.root / ".upgrade" / "decisions" / f"{record['decision_id']}.json"
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["decision"] = "Out-of-band replacement"
        path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
        audit = self.cli("decision-audit", expected=2)
        self.assertTrue(any("decision_id does not match" in message or "record_digest does not match" in message for message in audit["errors"]))

    def test_risk_revision_requires_reason_and_hash_chain_catches_local_reseal(self) -> None:
        self.initialize()
        first_review = self.cli(
            "risk-review", "--title", "Release risk", "--description", "Bounded exposure",
            "--scope", "release", "--status", "open", "--plan-out", ".upgrade/risk1.json",
        )
        first = self.cli(
            "risk-apply", "--plan", str(self.root / ".upgrade" / "risk1.json"),
            "--approve", first_review["plan_id"],
        )["result"]["risk"]

        rejected = self.cli("risk-review", "--risk-id", first["risk_id"], "--mitigation", "More tests", expected=2)
        self.assertTrue(any("revision requires --transition-reason" in message for message in rejected["errors"]))

        second_review = self.cli(
            "risk-review", "--risk-id", first["risk_id"], "--mitigation", "More tests",
            "--transition-reason", "Mitigation evidence updated", "--plan-out", ".upgrade/risk2.json",
        )
        second = self.cli(
            "risk-apply", "--plan", str(self.root / ".upgrade" / "risk2.json"),
            "--approve", second_review["plan_id"],
        )["result"]["risk"]
        self.assertEqual(second_review["plan_id"], second["approval_plan_id"])
        self.assertEqual(first["record_digest"], second["previous_record_digest"])
        self.assertEqual(2, self.cli("risk-audit")["result"]["risks"][0]["history_length"])

        path = self.root / ".upgrade" / "risks" / f"{first['risk_id']}.jsonl"
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        lines[0]["approval_plan_id"] = "d" * 64
        lines[0]["record_digest"] = governance_record_digest(lines[0])
        path.write_text("\n".join(json.dumps(item, sort_keys=True) for item in lines) + "\n", encoding="utf-8")
        audit = self.cli("risk-audit", expected=2)
        self.assertTrue(any("integrity chain is inconsistent" in message for message in audit["errors"]))

    def test_scoped_risk_does_not_block_unrelated_delivery_scope(self) -> None:
        self.initialize()
        self.validation()
        risk_review = self.cli(
            "risk-review", "--title", "Other service risk", "--description", "Unrelated",
            "--scope", "other-service", "--status", "open", "--plan-out", ".upgrade/other-risk.json",
        )
        self.cli(
            "risk-apply", "--plan", str(self.root / ".upgrade" / "other-risk.json"),
            "--approve", risk_review["plan_id"],
        )
        review = self.delivery_review()
        self.assertTrue(review["result"]["eligible"])
        self.assertEqual(0, review["result"]["evidence_summary"]["risks"]["count"])

    def test_final_acceptance_blocks_uncleared_workspace_governance_state(self) -> None:
        self.initialize()
        self.validation()
        self.cli("phase-update", "--phase", "0", "--current-task", "Finalize", "--blocker", "Approval pending")
        review = self.delivery_review(gate="final-acceptance")
        self.assertFalse(review["result"]["eligible"])
        self.assertIn("STATE.md current_blockers is not cleared", review["result"]["blockers"])

    def test_corrupt_transaction_journal_is_reported_as_governance_error(self) -> None:
        self.initialize()
        journal = self.root / ".upgrade" / "transactions" / "TXN-AAAAAAAAAAAA.json"
        journal.write_text('{"schema_version":1,"state":"failed"}\n', encoding="utf-8")
        audit = self.cli("recovery-audit", expected=2)
        self.assertTrue(any("invalid transaction journal schema" in message for message in audit["errors"]))

    def test_validation_integrity_chain_detects_tampering(self) -> None:
        self.initialize()
        self.validation(check="one")
        self.validation(check="two")
        path = self.root / ".upgrade" / "validation" / "records.jsonl"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        records[0]["note"] = "tampered"
        records[0]["record_digest"] = governance_record_digest(records[0])
        path.write_text("\n".join(json.dumps(item, sort_keys=True) for item in records) + "\n", encoding="utf-8")
        health = self.cli("health-check")
        self.assertEqual("error", health["status"])
        self.assertTrue(any("validation evidence integrity chain" in item for item in health["result"]["错误项"]))

    def test_git_worktree_subject_is_automatic_and_saved_plan_does_not_stale_it(self) -> None:
        import subprocess
        self.cli("initialize", "--profile", "lean")
        (self.root / "app.py").write_text("print('alpha')\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Round15 Test"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "app.py"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.root, check=True)

        validation = self.cli(
            "validation-record", "--check", "git-suite", "--status", "passed", "--exit-code", "0",
            "--scope", "release", "--environment", "prod",
        )
        self.assertEqual("git-worktree", validation["result"]["subject"]["type"])
        review = self.cli(
            "delivery-review", "--gate", "readiness", "--target", "git-v15", "--scope", "release",
            "--environment", "prod", "--require-check", "git-suite", "--plan-out", "delivery-plan.json",
        )
        self.assertTrue(review["result"]["eligible"])
        applied = self.cli(
            "delivery-apply", "--plan", str(self.root / "delivery-plan.json"), "--approve", review["plan_id"],
        )
        self.assertEqual("git-worktree", applied["result"]["delivery"]["subject"]["type"])
        audit = self.cli("delivery-audit")
        self.assertEqual(1, audit["result"]["count"])
        self.assertEqual(applied["result"]["delivery"]["record_digest"], audit["result"]["deliveries"][0]["record_digest"])

    def test_legacy_v1_governance_remains_readable_but_blocks_final_acceptance(self) -> None:
        self.initialize()
        self.cli("sync", "--enable-module", "review")
        legacy = {
            "schema_version": 1,
            "kind": "decision",
            "decision_id": "DEC-AAAAAAAAAAAA",
            "recorded_at": "2026-09-10T12:00:00+00:00",
            "title": "Legacy direction",
            "decision": "Legacy accepted direction",
            "rationale": "Pre-Round-15 record",
            "scope": "release",
            "evidence": [],
            "supersedes": None,
            "authority": "user",
            "approval_basis": "exact-plan-id",
        }
        path = self.root / ".upgrade" / "decisions" / "DEC-AAAAAAAAAAAA.json"
        path.write_text(json.dumps(legacy, sort_keys=True) + "\n", encoding="utf-8")
        self.assertEqual(1, self.cli("decision-audit")["result"]["count"])
        self.validation()
        review = self.delivery_review(gate="final-acceptance")
        self.assertFalse(review["result"]["eligible"])
        self.assertTrue(any("earlier unsealed governance schema" in item for item in review["result"]["blockers"]))

    def test_v2_risk_revision_can_extend_legacy_v1_history_without_breaking_chain(self) -> None:
        self.initialize()
        self.cli("sync", "--enable-module", "review")
        legacy_id = "RISK-AAAAAAAAAAAA"
        legacy = {
            "schema_version": 1,
            "kind": "risk",
            "risk_id": legacy_id,
            "revision": 1,
            "recorded_at": "2026-09-10T12:00:00+00:00",
            "title": "Legacy risk",
            "description": "Pre-Round-15 exposure",
            "likelihood": "medium",
            "impact": "medium",
            "status": "open",
            "mitigation": "",
            "evidence": [],
            "acceptance_reason": "",
            "resolution": "",
            "transition_reason": "",
            "authority": "user",
            "approval_basis": "exact-plan-id",
        }
        path = self.root / ".upgrade" / "risks" / f"{legacy_id}.jsonl"
        path.write_text(json.dumps(legacy, sort_keys=True) + "\n", encoding="utf-8")
        review = self.cli(
            "risk-review", "--risk-id", legacy_id, "--status", "accepted",
            "--acceptance-reason", "Explicit bounded acceptance", "--transition-reason", "Round 15 adoption",
            "--scope", "release", "--plan-out", ".upgrade/legacy-risk-v2.json",
        )
        applied = self.cli(
            "risk-apply", "--plan", str(self.root / ".upgrade" / "legacy-risk-v2.json"),
            "--approve", review["plan_id"],
        )["result"]["risk"]
        self.assertEqual(2, applied["revision"])
        self.assertEqual(64, len(applied["previous_record_digest"]))
        audit = self.cli("risk-audit", "--risk-id", legacy_id)
        self.assertEqual(2, audit["result"]["risks"][0]["history_length"])


if __name__ == "__main__":
    unittest.main()
