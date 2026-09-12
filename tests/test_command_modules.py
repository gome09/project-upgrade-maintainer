from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

from cli.main import HANDLERS
from cli.parser import build_parser
from core import policy


SKILL_ROOT = Path(__file__).resolve().parents[1]
COMMAND_ROOT = SKILL_ROOT / "commands"
SCRIPT = SKILL_ROOT / "scripts" / "upgrade_workspace.py"

PUBLIC_COMMAND_MODULES = {
    "bootstrap",
    "sync",
    "authority",
    "artifact_policy",
    "collect",
    "cleanup",
    "git_policy",
    "phase",
    "validation",
    "purge",
    "diagnostics",
    "decision", "risk", "recovery", "exception", "delivery_policy", "delivery", "inspect",
}

EXPECTED_HANDLER_MODULES = {
    "version": "commands.diagnostics",
    "inspect": "commands.inspect",
    "bootstrap-review": "commands.bootstrap",
    "initialize": "commands.bootstrap",
    "sync": "commands.sync",
    "authority-review": "commands.authority",
    "authority-apply": "commands.authority",
    "authority-audit": "commands.authority",
    "artifact-policy-review": "commands.artifact_policy",
    "artifact-policy-apply": "commands.artifact_policy",
    "collect-review": "commands.collect",
    "collect-apply": "commands.collect",
    "phase-update": "commands.phase",
    "cleanup-review": "commands.cleanup",
    "cleanup-apply": "commands.cleanup",
    "artifact-audit": "commands.diagnostics",
    "git-audit": "commands.diagnostics",
    "git-policy-review": "commands.git_policy",
    "git-policy-apply": "commands.git_policy",
    "validation-record": "commands.validation",
    "purge-review": "commands.purge",
    "purge-apply": "commands.purge",
    "decision-review": "commands.decision", "decision-apply": "commands.decision", "decision-audit": "commands.decision",
    "risk-review": "commands.risk", "risk-apply": "commands.risk", "risk-audit": "commands.risk",
    "recovery-audit": "commands.recovery", "recovery-review": "commands.recovery", "recovery-apply": "commands.recovery",
    "exception-review": "commands.exception", "exception-apply": "commands.exception", "exception-audit": "commands.exception",
    "delivery-policy-review": "commands.delivery_policy", "delivery-policy-apply": "commands.delivery_policy", "delivery-policy-audit": "commands.delivery_policy",
    "delivery-review": "commands.delivery", "delivery-apply": "commands.delivery", "delivery-audit": "commands.delivery",
    "health-check": "commands.diagnostics",
}


class CommandModuleBoundaryCase(unittest.TestCase):
    def test_all_round10_handlers_are_owned_by_command_modules(self) -> None:
        self.assertEqual(set(EXPECTED_HANDLER_MODULES), set(HANDLERS))
        for command, module_name in EXPECTED_HANDLER_MODULES.items():
            with self.subTest(command=command):
                self.assertEqual(module_name, HANDLERS[command].__module__)

    def test_public_command_modules_do_not_import_sibling_command_modules_or_scripts(self) -> None:
        for module_name in sorted(PUBLIC_COMMAND_MODULES):
            path = COMMAND_ROOT / f"{module_name}.py"
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                    self.assertFalse(
                        any(name == "scripts" or name.startswith("scripts.") for name in imported),
                        f"{module_name} imports compatibility scripts: {imported}",
                    )
                    self.assertFalse(
                        any(name.startswith("commands.") and name.split(".", 1)[1].split(".", 1)[0] in PUBLIC_COMMAND_MODULES for name in imported),
                        f"{module_name} imports sibling command module: {imported}",
                    )
                elif isinstance(node, ast.ImportFrom):
                    imported_module = node.module or ""
                    self.assertFalse(
                        imported_module == "scripts" or imported_module.startswith("scripts."),
                        f"{module_name} imports compatibility scripts: {imported_module}",
                    )
                    if node.level and imported_module in PUBLIC_COMMAND_MODULES:
                        self.fail(f"{module_name} imports sibling command module: {imported_module}")
                    if imported_module.startswith("commands."):
                        tail = imported_module.split(".", 1)[1].split(".", 1)[0]
                        self.assertNotIn(tail, PUBLIC_COMMAND_MODULES, f"{module_name} imports sibling command module")

    def test_command_package_relative_import_graph_is_acyclic(self) -> None:
        graph: dict[str, set[str]] = {}
        module_names = {path.stem for path in COMMAND_ROOT.glob("*.py") if path.name != "__init__.py"}
        for path in COMMAND_ROOT.glob("*.py"):
            if path.name == "__init__.py":
                continue
            module = path.stem
            graph[module] = set()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module in module_names:
                    graph[module].add(node.module)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(module: str, trail: tuple[str, ...] = ()) -> None:
            if module in visiting:
                self.fail("command import cycle: " + " -> ".join((*trail, module)))
            if module in visited:
                return
            visiting.add(module)
            for dependency in sorted(graph[module]):
                visit(dependency, (*trail, module))
            visiting.remove(module)
            visited.add(module)

        for module in sorted(graph):
            visit(module)

    def test_compatibility_script_contains_no_command_handler_or_parser_implementation(self) -> None:
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
        definitions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        forbidden = {
            "build_parser",
            "version_info",
            "bootstrap_review",
            "initialize",
            "sync",
            "authority_review", "authority_apply", "authority_audit",
            "artifact_policy_review",
            "artifact_policy_apply",
            "collect_review",
            "collect_apply",
            "phase_update",
            "cleanup_review",
            "cleanup_apply",
            "artifact_audit",
            "git_audit",
            "git_policy_review",
            "git_policy_apply",
            "validation_record",
            "purge_review",
            "purge_apply",
            "health_check", "decision_review", "decision_apply", "decision_audit", "risk_review", "risk_apply", "risk_audit",
            "recovery_audit", "recovery_review", "recovery_apply", "exception_review", "exception_apply", "exception_audit", "delivery_review", "delivery_apply", "delivery_audit",
        }
        self.assertFalse(definitions & forbidden, f"command/parser implementation leaked into compatibility script: {definitions & forbidden}")
        self.assertLessEqual(len(SCRIPT.read_text(encoding="utf-8").splitlines()), 100)

    def test_cli_parser_preserves_exact_round10_command_surface(self) -> None:
        parser = build_parser()
        subparser_action = next(
            action for action in parser._actions if action.__class__.__name__ == "_SubParsersAction"
        )
        self.assertEqual(set(EXPECTED_HANDLER_MODULES), set(subparser_action.choices))

    def test_plan_implementation_digest_scope_tracks_own_command_not_unrelated_commands(self) -> None:
        expected = {
            "authority": "commands/authority.py",
            "artifact-policy": "commands/artifact_policy.py",
            "collect": "commands/collect.py",
            "cleanup": "commands/cleanup.py",
            "git-policy": "commands/git_policy.py",
            "purge": "commands/purge.py",
            "decision": "commands/decision.py", "risk": "commands/risk.py", "recovery": "commands/recovery.py",
            "exception": "commands/exception.py", "delivery-policy": "commands/delivery_policy.py", "delivery": "commands/delivery.py",
        }
        all_specific = set(expected.values())
        for kind, own in expected.items():
            with self.subTest(kind=kind):
                files = set(policy.implementation_files_for_kind(kind))
                self.assertIn(own, files)
                self.assertIn("scripts/upgrade_workspace.py", files)
                self.assertIn("cli/main.py", files)
                self.assertIn("cli/parser.py", files)
                self.assertIn("commands/_runtime.py", files)
                self.assertTrue(set(policy.CORE_IMPLEMENTATION_FILES) <= files)
                self.assertFalse((all_specific - {own}) & files)


if __name__ == "__main__":
    unittest.main()
