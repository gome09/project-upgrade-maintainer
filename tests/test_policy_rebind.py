from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from core import plans, policy
from core.errors import StalePlanError


PLAN_KINDS = tuple(policy.POLICY_DEPENDENCIES)


class PolicyDependencyRebindCase(unittest.TestCase):
    def _materialize_policy_tree(self, root: Path) -> None:
        files = {
            relative
            for kind in PLAN_KINDS
            for relative in policy.plan_policy_dependencies(kind)
        }
        for relative in sorted(files):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"baseline:{relative}\n", encoding="utf-8")

    @staticmethod
    def _digest_for(skill_root: Path):
        return lambda kind: policy.policy_implementation_digest(
            kind,
            skill_root=skill_root,
            implementation_files=(),
        )

    def _plan(self, skill_root: Path, kind: str) -> dict[str, object]:
        return plans.create_plan(
            Path("/repo").resolve(),
            kind,
            [],
            clock=lambda: "2026-09-10T00:00:00+00:00",
            policy_digest=self._digest_for(skill_root),
        )

    def test_logical_policy_ids_resolve_to_canonical_semantic_paths(self) -> None:
        self.assertEqual(
            ("shared", "lifecycle.collect"),
            policy.policy_dependency_ids("collect"),
        )
        dependencies = policy.plan_policy_dependencies("collect")
        self.assertEqual(
            (
                "references/shared/safety-and-lifecycle.md",
                "subskills/lifecycle/references/collect-classification.md",
            ),
            dependencies,
        )
        self.assertEqual(len(dependencies), len(set(dependencies)))
        self.assertNotIn("scripts/upgrade_workspace.py", dependencies)
        self.assertFalse(any(path.startswith("references/") and not path.startswith("references/shared/") for path in dependencies))

    def test_router_copy_change_does_not_stale_reviewed_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_root = Path(directory)
            self._materialize_policy_tree(skill_root)
            collect = self._plan(skill_root, "collect")
            for relative in ("SKILL.md", "subskills/lifecycle/SKILL.md"):
                target = skill_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("router baseline\n", encoding="utf-8")
                target.write_text("router copy changed\n", encoding="utf-8")
                self.assertEqual(
                    collect,
                    plans.validate_plan(collect, policy_digest=self._digest_for(skill_root)),
                )

    def test_logical_policy_registry_has_no_unused_ids(self) -> None:
        used = {dependency_id for ids in policy.POLICY_DEPENDENCIES.values() for dependency_id in ids}
        self.assertEqual(used, set(policy.POLICY_FILES_BY_ID))

    def test_lifecycle_policy_change_stales_related_plan_but_not_other_lifecycle_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_root = Path(directory)
            self._materialize_policy_tree(skill_root)
            collect = self._plan(skill_root, "collect")
            cleanup = self._plan(skill_root, "cleanup")
            target = skill_root / "subskills/lifecycle/references/collect-classification.md"
            target.write_text(target.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

            with self.assertRaises(StalePlanError):
                plans.validate_plan(collect, policy_digest=self._digest_for(skill_root))
            self.assertEqual(cleanup, plans.validate_plan(cleanup, policy_digest=self._digest_for(skill_root)))

    def test_governance_phase_policy_change_does_not_stale_cleanup_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_root = Path(directory)
            self._materialize_policy_tree(skill_root)
            cleanup = self._plan(skill_root, "cleanup")
            target = skill_root / "subskills/governance/references/phase-governance.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("unrelated phase policy changed\n", encoding="utf-8")
            self.assertEqual(cleanup, plans.validate_plan(cleanup, policy_digest=self._digest_for(skill_root)))

    def test_shared_safety_change_stales_every_current_mutation_plan_kind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_root = Path(directory)
            self._materialize_policy_tree(skill_root)
            created = {kind: self._plan(skill_root, kind) for kind in PLAN_KINDS}
            target = skill_root / "references/shared/safety-and-lifecycle.md"
            target.write_text(target.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

            for kind, plan in created.items():
                with self.subTest(kind=kind):
                    with self.assertRaises(StalePlanError):
                        plans.validate_plan(plan, policy_digest=self._digest_for(skill_root))

    def test_decision_policy_change_stales_decision_and_delivery_only_among_governance_peers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_root = Path(directory)
            self._materialize_policy_tree(skill_root)
            decision = self._plan(skill_root, "decision")
            risk = self._plan(skill_root, "risk")
            exception = self._plan(skill_root, "exception")
            delivery = self._plan(skill_root, "delivery")
            target = skill_root / "subskills/governance/references/decision.md"
            target.write_text(target.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

            with self.assertRaises(StalePlanError):
                plans.validate_plan(decision, policy_digest=self._digest_for(skill_root))
            with self.assertRaises(StalePlanError):
                plans.validate_plan(delivery, policy_digest=self._digest_for(skill_root))
            self.assertEqual(risk, plans.validate_plan(risk, policy_digest=self._digest_for(skill_root)))
            self.assertEqual(exception, plans.validate_plan(exception, policy_digest=self._digest_for(skill_root)))

    def test_risk_policy_change_stales_risk_and_delivery_but_not_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_root = Path(directory)
            self._materialize_policy_tree(skill_root)
            decision = self._plan(skill_root, "decision")
            risk = self._plan(skill_root, "risk")
            delivery = self._plan(skill_root, "delivery")
            target = skill_root / "subskills/governance/references/risk.md"
            target.write_text(target.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

            with self.assertRaises(StalePlanError):
                plans.validate_plan(risk, policy_digest=self._digest_for(skill_root))
            with self.assertRaises(StalePlanError):
                plans.validate_plan(delivery, policy_digest=self._digest_for(skill_root))
            self.assertEqual(decision, plans.validate_plan(decision, policy_digest=self._digest_for(skill_root)))

    def test_exception_policy_change_stales_exception_and_delivery_but_not_risk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_root = Path(directory)
            self._materialize_policy_tree(skill_root)
            risk = self._plan(skill_root, "risk")
            exception = self._plan(skill_root, "exception")
            delivery = self._plan(skill_root, "delivery")
            target = skill_root / "subskills/delivery/references/exception.md"
            target.write_text(target.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

            with self.assertRaises(StalePlanError):
                plans.validate_plan(exception, policy_digest=self._digest_for(skill_root))
            with self.assertRaises(StalePlanError):
                plans.validate_plan(delivery, policy_digest=self._digest_for(skill_root))
            self.assertEqual(risk, plans.validate_plan(risk, policy_digest=self._digest_for(skill_root)))

    def test_delivery_gate_policy_change_only_stales_delivery_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_root = Path(directory)
            self._materialize_policy_tree(skill_root)
            exception = self._plan(skill_root, "exception")
            delivery_policy = self._plan(skill_root, "delivery-policy")
            delivery = self._plan(skill_root, "delivery")
            target = skill_root / "subskills/delivery/references/delivery-gate.md"
            target.write_text(target.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

            with self.assertRaises(StalePlanError):
                plans.validate_plan(delivery_policy, policy_digest=self._digest_for(skill_root))
            with self.assertRaises(StalePlanError):
                plans.validate_plan(delivery, policy_digest=self._digest_for(skill_root))
            self.assertEqual(exception, plans.validate_plan(exception, policy_digest=self._digest_for(skill_root)))

    def test_delivery_policy_reference_change_stales_policy_and_delivery_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_root = Path(directory)
            self._materialize_policy_tree(skill_root)
            delivery_policy = self._plan(skill_root, "delivery-policy")
            delivery = self._plan(skill_root, "delivery")
            exception = self._plan(skill_root, "exception")
            risk = self._plan(skill_root, "risk")
            target = skill_root / "subskills/delivery/references/delivery-policy.md"
            target.write_text(target.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

            with self.assertRaises(StalePlanError):
                plans.validate_plan(delivery_policy, policy_digest=self._digest_for(skill_root))
            with self.assertRaises(StalePlanError):
                plans.validate_plan(delivery, policy_digest=self._digest_for(skill_root))
            self.assertEqual(exception, plans.validate_plan(exception, policy_digest=self._digest_for(skill_root)))
            self.assertEqual(risk, plans.validate_plan(risk, policy_digest=self._digest_for(skill_root)))

    def test_round10_physical_dependency_plan_is_explicitly_stale_after_rebind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_root = Path(directory)
            self._materialize_policy_tree(skill_root)
            plan = self._plan(skill_root, "collect")
            plan["policy_dependencies"] = [
                "scripts/upgrade_workspace.py",
                "SKILL.md",
                "references/safety-and-lifecycle.md",
                "references/collect-classification.md",
                "references/adaptive-bootstrap.md",
            ]
            plan["plan_id"] = plans.plan_id(plan)
            with self.assertRaisesRegex(StalePlanError, "dependency scope changed"):
                plans.validate_plan(plan, policy_digest=self._digest_for(skill_root))


if __name__ == "__main__":
    unittest.main()
