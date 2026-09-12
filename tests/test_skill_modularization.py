from __future__ import annotations

import re
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]


class SkillModularizationCase(unittest.TestCase):
    def test_main_skill_is_router_not_domain_rule_dump(self) -> None:
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Intent Router", text)
        self.assertIn("## Shared Safety", text)
        self.assertIn("references/shared/safety-and-lifecycle.md", text)
        for domain in ("bootstrap", "lifecycle", "governance", "delivery"):
            self.assertIn(f"subskills/{domain}/SKILL.md", text)
        # Detailed Round-10 reference files must no longer be routed directly by main.
        for legacy in (
            "adaptive-bootstrap.md",
            "artifact-lifecycle.md",
            "artifact-policy-linking.md",
            "collect-classification.md",
            "cleanup-classification.md",
            "git-policy.md",
            "governance-context.md",
            "phase-governance.md",
            "validation-and-recovery.md",
        ):
            self.assertNotIn(f"references/{legacy}", text)

    def test_four_domain_subskills_exist_with_local_reference_boundaries(self) -> None:
        for domain in ("bootstrap", "lifecycle", "governance", "delivery"):
            skill = SKILL_ROOT / "subskills" / domain / "SKILL.md"
            self.assertTrue(skill.is_file(), domain)
            text = skill.read_text(encoding="utf-8")
            self.assertRegex(text, rf"name:\s+project-upgrade-maintainer-{domain}\b")
            self.assertIn("../../references/shared/safety-and-lifecycle.md", text)
            # Domain routers may only name local references, never another subskill's references.
            for other in {"bootstrap", "lifecycle", "governance", "delivery"} - {domain}:
                self.assertNotIn(f"subskills/{other}", text)
                self.assertNotIn(f"../{other}/", text)
            for reference in re.findall(r"`(references/[^`]+\.md)`", text):
                self.assertTrue((skill.parent / reference).is_file(), f"missing {domain}: {reference}")

    def test_router_assigns_existing_commands_to_one_domain(self) -> None:
        main = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        rows = [line for line in main.splitlines() if line.startswith("|")]
        joined = "\n".join(rows)
        expected = {
            "bootstrap-review": "Bootstrap",
            "initialize": "Bootstrap",
            "sync": "Bootstrap",
            "health-check": "Bootstrap",
            "artifact-audit": "Lifecycle",
            "artifact-policy-*": "Lifecycle",
            "git-audit": "Lifecycle",
            "git-policy-*": "Lifecycle",
            "collect-*": "Lifecycle",
            "cleanup-*": "Lifecycle",
            "purge-*": "Lifecycle",
            "phase-update": "Governance",
            "validation-record": "Governance",
            "decision-*": "Governance",
            "risk-*": "Governance",
            "recovery-*": "Core recovery",
            "version": "Core diagnostics",
        }
        for command, domain in expected.items():
            with self.subTest(command=command):
                matching = [row for row in rows if f"`{command}`" in row]
                self.assertEqual(1, len(matching), matching)
                self.assertIn(domain, matching[0])
        self.assertIn("`exception-*`, `delivery-*`", joined)
        self.assertIn("Delivery", joined)

    def test_phase_e_removes_legacy_reference_mirrors_after_rebind(self) -> None:
        legacy = (
            "safety-and-lifecycle.md",
            "adaptive-bootstrap.md",
            "governance-context.md",
            "artifact-lifecycle.md",
            "artifact-policy-linking.md",
            "collect-classification.md",
            "cleanup-classification.md",
            "git-policy.md",
            "phase-governance.md",
            "validation-and-recovery.md",
        )
        for name in legacy:
            with self.subTest(name=name):
                self.assertFalse((SKILL_ROOT / "references" / name).exists())

    def test_split_validation_and_purge_references_are_domain_local(self) -> None:
        validation = (SKILL_ROOT / "subskills" / "governance" / "references" / "validation.md")
        purge = (SKILL_ROOT / "subskills" / "lifecycle" / "references" / "purge.md")
        self.assertTrue(validation.is_file())
        self.assertTrue(purge.is_file())
        self.assertIn("Validation Evidence", validation.read_text(encoding="utf-8"))
        self.assertIn("Permanent Purge", purge.read_text(encoding="utf-8"))


    def test_lifecycle_router_avoids_redundant_reference_loading(self) -> None:
        skill = (SKILL_ROOT / "subskills" / "lifecycle" / "SKILL.md").read_text(encoding="utf-8")
        rows = [line for line in skill.splitlines() if line.startswith("|")]
        artifact_policy = next(row for row in rows if "`artifact-policy-review`" in row)
        git_policy = next(row for row in rows if "`git-policy-review`" in row)
        self.assertIn("artifact-policy-linking.md", artifact_policy)
        self.assertNotIn("artifact-lifecycle.md", artifact_policy)
        self.assertIn("git-policy.md", git_policy)
        self.assertNotIn("artifact-lifecycle.md", git_policy)

    def test_router_files_are_not_plan_policy_dependencies(self) -> None:
        from core import policy

        for kind in policy.POLICY_DEPENDENCIES:
            with self.subTest(kind=kind):
                dependencies = policy.plan_policy_dependencies(kind)
                self.assertNotIn("SKILL.md", dependencies)
                self.assertFalse(any(path.endswith("/SKILL.md") for path in dependencies))

    def test_cleanup_reference_has_no_removed_validation_recovery_link(self) -> None:
        cleanup = (
            SKILL_ROOT / "subskills" / "lifecycle" / "references" / "cleanup-classification.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("validation-and-recovery.md", cleanup)
        self.assertIn("`purge-review` / `purge-apply`", cleanup)

    def test_delivery_is_implemented_without_governance_command_coupling(self) -> None:
        skill = (SKILL_ROOT / "subskills" / "delivery" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Delivery consumes Core-validated Decision, Risk, Validation, Exception, and Recovery state", skill)
        self.assertTrue((SKILL_ROOT / "subskills/delivery/references/exception.md").is_file())
        self.assertTrue((SKILL_ROOT / "subskills/delivery/references/delivery-gate.md").is_file())
        self.assertFalse((SKILL_ROOT / "subskills/delivery/references/boundary.md").exists())
        self.assertTrue((SKILL_ROOT / "commands/exception.py").is_file())
        self.assertTrue((SKILL_ROOT / "commands/delivery.py").is_file())



if __name__ == "__main__":
    unittest.main()
