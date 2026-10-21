from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


CLI = Path(__file__).resolve().parents[1] / "scripts" / "upgrade_workspace.py"


def run(root: Path, *args: str, expected: int = 0) -> dict:
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", str(CLI), "--root", str(root), *args],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode != expected:
        raise AssertionError(f"expected {expected}, got {completed.returncode}: {completed.stderr}{completed.stdout}")
    return json.loads(completed.stdout)


def release_contract() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        version = run(root, "version")
        assert version["result"]["skill_version"] == "18.0.1"
        assert version["result"]["policy_version"] == 14
        assert version["result"]["plan_schema_version"] == 3
        assert version["result"]["previous_plan_schema_versions"] == [2]
        assert not (root / ".upgrade").exists()

        run(root, "initialize", "--profile", "lean")
        legacy_plan = root / "legacy-schema2.json"
        legacy_plan.write_text(json.dumps({
            "schema_version": 2,
            "skill_version": "9.0.0",
            "policy_version": 5,
            "kind": "collect",
            "root": str(root),
            "entries": [],
            "plan_id": "legacy",
        }), encoding="utf-8")
        rejected = run(root, "collect-apply", "--plan", str(legacy_plan), "--approve", "legacy", expected=3)
        assert "plan schema 2 is obsolete" in rejected["errors"][0]

        round10_plan = root / "round10-schema3.json"
        round10_plan.write_text(json.dumps({
            "schema_version": 3,
            "skill_version": "10.0.0",
            "policy_version": 6,
            "policy_dependencies": [],
            "implementation_sha256": "legacy",
            "kind": "collect",
            "root": str(root),
            "created_at": "2026-09-10T00:00:00+00:00",
            "entries": [],
            "plan_id": "round10",
        }), encoding="utf-8")
        rejected_round10 = run(root, "collect-apply", "--plan", str(round10_plan), "--approve", "round10", expected=3)
        assert "different skill or policy version" in rejected_round10["errors"][0]


def adaptive_workspace() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "package.json").write_text('{"name":"small"}\n', encoding="utf-8")
        review = run(root, "bootstrap-review")
        assert review["result"]["read_only"] is True
        assert review["result"]["recommendation"]["profile"] == "lean"
        assert not (root / ".upgrade").exists()

        run(root, "initialize", "--profile", "lean", "--create-root-bridge")
        upgrade = root / ".upgrade"
        assert (upgrade / "AGENTS.md").is_file()
        assert (upgrade / "CONFIG.json").is_file()
        assert ".upgrade/AGENTS.md" in (root / "AGENTS.md").read_text(encoding="utf-8")
        assert not (upgrade / "stages").exists()
        assert not (upgrade / "validation").exists()
        assert not (upgrade / "tmp").exists()

        run(root, "phase-update", "--phase", "1", "--current-task", "Implement")
        assert (upgrade / "stages" / "phase-1.md").is_file()
        run(root, "validation-record", "--check", "smoke", "--status", "passed", "--exit-code", "0")
        assert (upgrade / "validation" / "records.jsonl").is_file()
        config = json.loads((upgrade / "CONFIG.json").read_text(encoding="utf-8"))
        assert config["initial_profile"] == "lean"
        assert "phase" in config["active_modules"] and "validation" in config["active_modules"]

        second = run(root, "initialize", "--create-root-bridge")
        assert second["changed"] == []
        health = run(root, "health-check")
        assert not health["errors"]


def legacy_workspace() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        upgrade = root / ".upgrade"
        (upgrade / "stages").mkdir(parents=True)
        (upgrade / "reports").mkdir()
        (upgrade / "checkpoints").mkdir()
        (upgrade / "tmp").mkdir()
        (upgrade / "logs").mkdir()
        (upgrade / "traces").mkdir()
        (upgrade / "archive").mkdir()
        (upgrade / "STATE.md").write_text(
            "# Upgrade State\n\n## current_phase\n\nPhase 1: In progress\n\n"
            "## current_task\n\nLegacy task\n\n## User Notes\n\nPreserve me.\n",
            encoding="utf-8",
        )
        (upgrade / "MANIFEST.md").write_text("# Legacy Manifest\n\nUser-owned preface.\n", encoding="utf-8")
        (upgrade / "reports" / "stage1_report.md").write_text("# Legacy Stage Report\n", encoding="utf-8")
        (upgrade / "checkpoints" / "phase-1.entered").write_text("legacy\n", encoding="utf-8")
        run(root, "sync", "--create-root-bridge")
        config = json.loads((upgrade / "CONFIG.json").read_text(encoding="utf-8"))
        assert config["initial_profile"] == "legacy-governed"
        assert (upgrade / "AGENTS.md").is_file()
        assert ".upgrade/AGENTS.md" in (root / "AGENTS.md").read_text(encoding="utf-8")
        run(root, "phase-update", "--phase", "1", "--current-task", "Legacy task")
        assert "Preserve me." in (upgrade / "STATE.md").read_text(encoding="utf-8")
        assert "User-owned preface." in (upgrade / "MANIFEST.md").read_text(encoding="utf-8")
        health = run(root, "health-check")
        assert not health["errors"]



def artifact_policy_linking() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        run(root, "initialize", "--profile", "lean", "--create-root-bridge")

        governance_plan = root / "security-policy.json"
        governance_review = run(
            root, "artifact-policy-review", "--path", "security/SECURITY_REVIEW.md",
            "--purpose", "Security upgrade review", "--create", "--plan-out", str(governance_plan),
        )
        assert governance_review["result"]["policy_preview"]["kind"] == "governance"
        run(root, "artifact-policy-apply", "--plan", str(governance_plan), "--approve", governance_review["plan_id"])
        artifact = root / ".upgrade" / "security" / "SECURITY_REVIEW.md"
        assert artifact.is_file()
        assert ".upgrade/security/SECURITY_REVIEW.md" in (root / ".upgrade" / "AGENTS.md").read_text(encoding="utf-8")
        assert ".upgrade/security/SECURITY_REVIEW.md" in (root / ".upgrade" / "README.md").read_text(encoding="utf-8")

        runtime_plan = root / "runtime-policy.json"
        runtime_review = run(
            root, "artifact-policy-review", "--path", "runtime/migration-debug.log",
            "--purpose", "Raw migration debug output", "--create", "--plan-out", str(runtime_plan),
        )
        assert runtime_review["result"]["policy_preview"]["kind"] == "runtime"
        run(root, "artifact-policy-apply", "--plan", str(runtime_plan), "--approve", runtime_review["plan_id"])
        literal_rule = "/.upgrade/runtime/migration-debug.log"
        assert literal_rule in (root / ".gitignore").read_text(encoding="utf-8")
        assert literal_rule in (root / ".dockerignore").read_text(encoding="utf-8")
        assert "migration-debug.log" not in (root / ".upgrade" / "AGENTS.md").read_text(encoding="utf-8")

        registry = json.loads((root / ".upgrade" / "ARTIFACTS.json").read_text(encoding="utf-8"))
        assert {entry["kind"] for entry in registry["entries"]} == {"governance", "runtime"}
        health = run(root, "health-check")
        assert not health["errors"]

def delivery_gate() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        run(root, "initialize", "--profile", "lean")
        (root / "release-subject.txt").write_text("release-v15\n", encoding="utf-8")
        run(
            root, "validation-record", "--check", "release-smoke", "--status", "passed", "--exit-code", "0",
            "--scope", "round15-e2e", "--subject-path", "release-subject.txt",
        )

        plan = root / "delivery-readiness.json"
        review = run(
            root, "delivery-review", "--gate", "readiness", "--target", "v15-e2e",
            "--scope", "round15-e2e", "--require-check", "release-smoke", "--subject-path", "release-subject.txt",
            "--acceptance-note", "Explicit user acceptance for E2E", "--plan-out", str(plan),
        )
        assert review["result"]["eligible"] is True
        assert review["result"]["blockers"] == []

        applied = run(root, "delivery-apply", "--plan", str(plan), "--approve", review["plan_id"])
        delivery = applied["result"]["delivery"]
        assert delivery["gate"] == "readiness"
        assert delivery["target"] == "v15-e2e"
        assert delivery["outcome"] == "accepted"
        assert delivery["authority"] == "user"
        assert delivery["approval_basis"] == "exact-plan-id"

        audit = run(root, "delivery-audit")
        assert audit["result"]["count"] == 1
        assert audit["result"]["deliveries"][0]["delivery_id"] == delivery["delivery_id"]



def delivery_policy_and_ci() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        run(root, "initialize", "--profile", "lean", "--create-root-bridge")
        (root / "release-subject.txt").write_text("release-v16\n", encoding="utf-8")

        policy_plan = root / "delivery-policy.json"
        policy_review = run(
            root, "delivery-policy-review", "--profile", "project",
            "--readiness-check", "release-smoke", "--final-check", "release-smoke",
            "--plan-out", str(policy_plan),
        )
        policy_apply = run(root, "delivery-policy-apply", "--plan", str(policy_plan), "--approve", policy_review["plan_id"])
        assert policy_apply["result"]["delivery_policy"]["gates"]["readiness"] == ["release-smoke"]

        run(
            root, "validation-record", "--check", "release-smoke", "--status", "passed", "--exit-code", "0",
            "--scope", "round16-e2e", "--environment", "prod", "--subject-path", "release-subject.txt",
        )
        delivery_plan = root / "delivery-readiness-policy.json"
        review = run(
            root, "delivery-review", "--gate", "readiness", "--target", "v16-e2e",
            "--scope", "round16-e2e", "--environment", "prod", "--subject-path", "release-subject.txt",
            "--plan-out", str(delivery_plan),
        )
        assert review["result"]["eligible"] is True
        assert review["result"]["policy_required_checks"] == ["release-smoke"]
        applied = run(root, "delivery-apply", "--plan", str(delivery_plan), "--approve", review["plan_id"])
        assert applied["result"]["delivery"]["delivery_policy_id"] == policy_apply["result"]["delivery_policy"]["policy_id"]

        ci = run(root, "health-check", "--ci")
        assert ci["result"]["ci"]["mode"] == "ci"
        assert ci["result"]["ci"]["contract"]["error"] == 5




def authority_network() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "SECURITY.md").write_text("security-v1\n", encoding="utf-8")
        bootstrap = run(root, "bootstrap-review")
        assert "SECURITY.md" in {item["path"] for item in bootstrap["result"]["authority_discovery"]["candidates"]}
        run(root, "initialize", "--profile", "lean", "--create-root-bridge")
        plan = root / "authority.json"
        review = run(root, "authority-review", "--accept", "SECURITY.md", "--plan-out", str(plan))
        run(root, "authority-apply", "--plan", str(plan), "--approve", review["plan_id"])
        registry = json.loads((root / ".upgrade" / "AUTHORITY.json").read_text(encoding="utf-8"))
        assert registry["entries"][0]["path"] == "SECURITY.md"
        assert registry["entries"][0]["managed"] is False
        (root / "SECURITY.md").write_text("security-v2\n", encoding="utf-8")
        health = run(root, "health-check")
        assert any("authority source content changed" in item for item in health["warnings"])
        old_digest = registry["entries"][0]["sha256"]
        run(root, "sync")
        registry_after_sync = json.loads((root / ".upgrade" / "AUTHORITY.json").read_text(encoding="utf-8"))
        assert registry_after_sync["entries"][0]["sha256"] == old_digest


def transaction_durability() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        run(root, "initialize", "--profile", "lean")
        run(root, "initialize", "--profile", "standard")
        journals = sorted((root / ".upgrade" / "transactions").glob("TXN-*.json"))
        assert journals
        payloads = [json.loads(path.read_text(encoding="utf-8")) for path in journals]
        committed = [item for item in payloads if item["command"] == "initialize" and item["state"] == "committed"]
        assert committed
        for item in committed:
            assert not (root / ".upgrade" / "transactions" / item["transaction_id"]).exists()
        health = run(root, "health-check", "--ci")
        assert health["result"]["ci"]["recovery_required"] is False

def stale_plan() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        run(root, "initialize", "--profile", "lean")
        source = root / "tmp_upgrade.txt"
        source.write_text("before", encoding="utf-8")
        plan = root / "collect.json"
        review = run(root, "collect-review", "--plan-out", str(plan))
        source.write_text("after", encoding="utf-8")
        rejected = run(root, "collect-apply", "--plan", str(plan), "--approve", review["plan_id"], expected=3)
        assert rejected["status"] == "error"
        assert source.exists()
        assert not (root / ".upgrade" / "tmp" / source.name).exists()


if __name__ == "__main__":
    release_contract()
    adaptive_workspace()
    artifact_policy_linking()
    legacy_workspace()
    stale_plan()
    delivery_gate()
    delivery_policy_and_ci()
    authority_network()
    transaction_durability()
    print("E2E acceptance passed: release contract, adaptive workspace, artifact policy linking, legacy adoption, stale plan, delivery gate, delivery policy, authority network, CI health, transaction durability")
