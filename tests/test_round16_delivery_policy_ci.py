from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cli.main import run
from core.transactions import Transaction


class Round16DeliveryPolicyCiCase(unittest.TestCase):
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
        (self.root / "src").mkdir()
        (self.root / "src" / "app.txt").write_text("alpha\n", encoding="utf-8")

    def validation(self, check: str) -> dict:
        return self.cli(
            "validation-record", "--check", check, "--status", "passed", "--exit-code", "0",
            "--scope", "release", "--environment", "prod", "--subject-path", "src",
        )

    def configure_policy(self, *extra: str, plan_name: str = "delivery-policy-plan.json") -> dict:
        review = self.cli(
            "delivery-policy-review", "--profile", "project", *extra,
            "--plan-out", f".upgrade/{plan_name}",
        )
        return self.cli(
            "delivery-policy-apply", "--plan", str(self.root / ".upgrade" / plan_name),
            "--approve", review["plan_id"],
        )["result"]["delivery_policy"]


    def test_round16_release_contract(self) -> None:
        version = self.cli("version")
        result = version["result"]
        self.assertEqual("18.0.0", result["skill_version"])
        self.assertEqual(14, result["policy_version"])
        self.assertEqual(3, result["plan_schema_version"])
        self.assertEqual(3, result["delivery_schema_version"])
        self.assertEqual(1, result["delivery_policy_schema_version"])
        self.assertEqual({"ok": 0, "error": 5, "recovery_required": 6, "strict_warning": 7}, result["health_ci_exit_codes"])

    def test_delivery_policy_enforces_minimum_checks_without_explicit_require_check(self) -> None:
        self.initialize()
        policy = self.configure_policy("--readiness-check", "release-suite", "--final-check", "release-suite")
        self.assertEqual("user", policy["authority"])
        self.assertEqual(64, len(policy["record_digest"]))
        self.validation("release-suite")
        review = self.cli(
            "delivery-review", "--gate", "readiness", "--target", "v16", "--scope", "release",
            "--environment", "prod", "--subject-path", "src",
        )
        self.assertTrue(review["result"]["eligible"])
        self.assertEqual(["release-suite"], review["result"]["policy_required_checks"])
        self.assertEqual(["release-suite"], review["result"]["required_checks"])
        self.assertEqual(policy["policy_id"], review["result"]["delivery_policy"]["policy_id"])

    def test_delivery_review_can_add_but_not_remove_policy_checks(self) -> None:
        self.initialize()
        self.configure_policy("--readiness-check", "release-suite")
        self.validation("smoke")
        blocked = self.cli(
            "delivery-review", "--gate", "readiness", "--target", "v16", "--scope", "release",
            "--environment", "prod", "--subject-path", "src", "--require-check", "smoke",
        )
        self.assertFalse(blocked["result"]["eligible"])
        self.assertEqual(["release-suite", "smoke"], blocked["result"]["required_checks"])
        self.assertTrue(any("release-suite" in item for item in blocked["result"]["blockers"]))
        self.validation("release-suite")
        ready = self.cli(
            "delivery-review", "--gate", "readiness", "--target", "v16", "--scope", "release",
            "--environment", "prod", "--subject-path", "src", "--require-check", "smoke",
        )
        self.assertTrue(ready["result"]["eligible"])

    def test_delivery_plan_stales_when_delivery_policy_changes(self) -> None:
        self.initialize()
        self.configure_policy("--readiness-check", "release-suite", plan_name="p1.json")
        self.validation("release-suite")
        review = self.cli(
            "delivery-review", "--gate", "readiness", "--target", "v16", "--scope", "release",
            "--environment", "prod", "--subject-path", "src", "--plan-out", ".upgrade/delivery.json",
        )
        self.configure_policy("--readiness-check", "release-suite", "--readiness-check", "security", plan_name="p2.json")
        rejected = self.cli(
            "delivery-apply", "--plan", str(self.root / ".upgrade" / "delivery.json"),
            "--approve", review["plan_id"], expected=3,
        )
        self.assertTrue(any("delivery policy changed" in item for item in rejected["errors"]))

    def test_delivery_policy_audit_and_health_detect_tampering(self) -> None:
        self.initialize()
        policy = self.configure_policy("--readiness-check", "release-suite")
        audit = self.cli("delivery-policy-audit")
        self.assertTrue(audit["result"]["configured"])
        self.assertEqual(policy["policy_id"], audit["result"]["delivery_policy"]["policy_id"])
        path = self.root / ".upgrade" / "delivery" / "POLICY.json"
        raw = json.loads(path.read_text(encoding="utf-8")); raw["note"] = "tampered"
        path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
        health = self.cli("health-check")
        self.assertEqual("error", health["status"])
        self.assertTrue(any("delivery policy" in item for item in health["result"]["错误项"]))


    def test_delivery_policy_is_a_canonical_retained_workspace_file(self) -> None:
        self.initialize()
        self.configure_policy("--readiness-check", "release-suite")
        cleanup = self.cli("cleanup-review", "--decision", ".upgrade/delivery/POLICY.json=delete")
        sources = [entry["source"] for entry in cleanup["result"]["plan"]["entries"]]
        self.assertNotIn(".upgrade/delivery/POLICY.json", sources)
        rejected = self.cli(
            "artifact-policy-review", "--path", "delivery/POLICY.json", "--kind", "governance",
            "--purpose", "attempt to claim canonical policy", expected=2,
        )
        self.assertTrue(any("managed canonical workspace file" in item for item in rejected["errors"]))

    def test_health_ci_and_strict_exit_contract_preserves_default_compatibility(self) -> None:
        default = self.cli("health-check")
        self.assertEqual("error", default["status"])
        ci = self.cli("health-check", "--ci", expected=5)
        self.assertEqual(5, ci["result"]["ci"]["contract"]["error"])

        self.initialize()
        # A non-Git workspace produces warning-only health; CI tolerates it while strict fails it.
        ci_warning = self.cli("health-check", "--ci", expected=0)
        self.assertEqual("warning", ci_warning["status"])
        self.assertFalse(ci_warning["result"]["ci"]["recovery_required"])
        strict_warning = self.cli("health-check", "--strict", expected=7)
        self.assertEqual("warning", strict_warning["status"])

    def test_health_ci_returns_recovery_required_for_unresolved_transaction(self) -> None:
        self.initialize()
        tx = Transaction.start(self.root, "test-interruption")
        tx.close()  # Leave a valid active journal without keeping it as the current transaction.
        payload = self.cli("health-check", "--ci", expected=6)
        self.assertTrue(payload["result"]["ci"]["recovery_required"])
        self.assertEqual(1, payload["result"]["ci"]["unresolved_transactions"])


if __name__ == "__main__":
    unittest.main()
