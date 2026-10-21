"""Shared constants and immutable policy metadata."""

from __future__ import annotations

import re
from pathlib import Path

SCHEMA_VERSION = 1
PLAN_SCHEMA_VERSION = 3
PREVIOUS_PLAN_SCHEMA_VERSIONS = (2,)
SKILL_VERSION = "18.0.1"
POLICY_VERSION = 14
WORKSPACE_CONFIG_SCHEMA_VERSION = 2
PREVIOUS_WORKSPACE_CONFIG_SCHEMA_VERSIONS = (1,)
WORKSPACE_CONFIG_NAME = "CONFIG.json"
ARTIFACT_REGISTRY_SCHEMA_VERSION = 1
ARTIFACT_REGISTRY_NAME = "ARTIFACTS.json"
AUTHORITY_REGISTRY_SCHEMA_VERSION = 1
AUTHORITY_REGISTRY_NAME = "AUTHORITY.json"
AUTHORITY_VIEW_NAME = "AUTHORITY.md"
AUTHORITY_MAP_MARKER = "<!-- project-upgrade-maintainer:authority-map:v1 -->"
AUTHORITY_KINDS = ("agent_instruction", "project_overview", "contribution_policy", "security_policy", "ownership_policy", "architecture", "api_contract", "delivery_policy", "migration_guidance", "ci_policy", "reference")
AUTHORITY_LEVELS = ("repository", "scoped", "reference")
DECISION_SCHEMA_VERSION = 2
RISK_SCHEMA_VERSION = 2
VALIDATION_SCHEMA_VERSION = 2
TRANSACTION_SCHEMA_VERSION = 1
EXCEPTION_SCHEMA_VERSION = 2
DELIVERY_SCHEMA_VERSION = 3
DELIVERY_POLICY_SCHEMA_VERSION = 1
DELIVERY_POLICY_NAME = "delivery/POLICY.json"
RISK_STATUSES = ("open", "accepted", "mitigated", "closed")
RISK_LEVELS = ("unknown", "low", "medium", "high")
EXCEPTION_TYPES = ("exception", "waiver")
EXCEPTION_STATUSES = ("active", "resolved", "revoked")
DELIVERY_GATES = ("readiness", "final-acceptance", "rollback-readiness")
MANIFEST_START = "<!-- project-upgrade-maintainer:manifest:start -->"
MANIFEST_END = "<!-- project-upgrade-maintainer:manifest:end -->"
GUARD_START = "<!-- project-upgrade-maintainer:start -->"
GUARD_END = "<!-- project-upgrade-maintainer:end -->"
GOVERNANCE_CONTRACT_MARKER = "<!-- project-upgrade-maintainer:governance-contract:v1 -->"
AGENTS_ARTIFACT_START = "<!-- project-upgrade-maintainer:artifact-policies:start -->"
AGENTS_ARTIFACT_END = "<!-- project-upgrade-maintainer:artifact-policies:end -->"
README_ARTIFACT_START = "<!-- project-upgrade-maintainer:artifact-index:start -->"
README_ARTIFACT_END = "<!-- project-upgrade-maintainer:artifact-index:end -->"
IGNORE_START = "# project-upgrade-maintainer:start"
IGNORE_END = "# project-upgrade-maintainer:end"
GIT_POLICY_START = "# project-upgrade-maintainer:git-policy:start"
GIT_POLICY_END = "# project-upgrade-maintainer:git-policy:end"
SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = SKILL_ROOT / "assets" / "templates"
AGENT_INSTRUCTION_NAMES = (
    "AGENTS.md", "CLAUDE.md", "GEMINI.md", "CURSOR_RULES", ".clinerules",
    ".ai-instructions", ".windsurfrules", ".windsurf",
)
ARTIFACT_KINDS = ("governance", "content", "runtime")
ARTIFACT_LIFECYCLES = ("permanent", "temporary", "generated", "historical")
ARTIFACT_GIT_POLICIES = ("tracked", "ignore")
ARTIFACT_DOCKER_POLICIES = ("include", "exclude")

MODULE_ORDER = ("core", "planning", "phase", "validation", "reporting", "review", "lifecycle")
MODULE_SPECS: dict[str, dict[str, tuple[str, ...] | dict[str, str]]] = {
    "core": {
        "dirs": ("traces", "transactions",),
        "placeholders": ("traces/.gitkeep", "transactions/.gitkeep",),
        "templates": {
            "README.md": "README.md",
            "STATE.md": "STATE.md",
            "MANIFEST.md": "MANIFEST.md",
            "ARTIFACTS.json": "ARTIFACTS.json",
            "AUTHORITY.json": "AUTHORITY.json",
            "AUTHORITY.md": "AUTHORITY.md",
        },
    },
    "planning": {
        "dirs": ("docs",),
        "placeholders": (),
        "templates": {
            "UPGRADE_REQUIREMENTS.md": "docs/UPGRADE_REQUIREMENTS.md",
            "UPGRADE_PLAN.md": "docs/UPGRADE_PLAN.md",
        },
    },
    "phase": {
        "dirs": ("stages", "checkpoints",),
        "placeholders": (),
        "templates": {"IMPLEMENTATION_PLAN.md": "docs/IMPLEMENTATION_PLAN.md"},
    },
    "validation": {
        "dirs": ("validation",),
        "placeholders": ("validation/.gitkeep",),
        "templates": {},
    },
    "reporting": {
        "dirs": ("reports",),
        "placeholders": (),
        "templates": {"FINAL_REPORT.md": "reports/FINAL_REPORT.md"},
    },
    "review": {
        "dirs": ("decisions", "risks", "exceptions", "delivery", "reviews",),
        "placeholders": (),
        "templates": {"POST_UPGRADE_REVIEW.md": "reviews/POST_UPGRADE_REVIEW.md"},
    },
    "lifecycle": {
        "dirs": ("prompts", "logs", "tmp", "tmp/scripts", "tmp/.trash", "archive", "archive/scripts"),
        "placeholders": ("logs/.gitkeep", "tmp/.gitkeep", "tmp/.trash/.gitkeep", "archive/.gitkeep"),
        "templates": {},
    },
}
MODULE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "core": (),
    "planning": ("core",),
    "phase": ("planning",),
    "validation": ("core",),
    "reporting": ("planning",),
    "review": ("planning",),
    "lifecycle": ("core",),
}
PROFILE_MODULES: dict[str, tuple[str, ...]] = {
    "lean": ("core", "planning"),
    "standard": ("core", "planning", "phase", "validation", "reporting", "lifecycle"),
    "governed": MODULE_ORDER,
}
ALL_MANAGED_DIRS = tuple(dict.fromkeys(
    relative for module in MODULE_ORDER for relative in MODULE_SPECS[module]["dirs"]  # type: ignore[index]
))
ALL_PLACEHOLDERS = tuple(dict.fromkeys(
    relative for module in MODULE_ORDER for relative in MODULE_SPECS[module]["placeholders"]  # type: ignore[index]
))
ALL_TEMPLATE_TARGETS: dict[str, str] = {}
for _module in MODULE_ORDER:
    ALL_TEMPLATE_TARGETS.update(MODULE_SPECS[_module]["templates"])  # type: ignore[arg-type]
EXCLUDED_DIRS = {
    ".git", ".upgrade", "node_modules", "vendor", "dist", "build", ".venv",
    "venv", ".next", ".cache", "__pycache__", ".tox", ".mypy_cache",
}
COLLECT_SIGNAL_TERMS = (
    "upgrade", "migration", "migrate", "refactor", "cleanup", "rollout", "rollback",
    "phase", "stage", "report", "draft", "temp", "tmp", "scratch", "debug",
    "experiment", "oneoff", "one-off", "one_off", "one off", "adhoc", "ad-hoc",
    "ad_hoc", "ad hoc", "analysis", "prompt", "note", "checklist", "升级", "迁移",
    "重构", "清理", "阶段", "报告", "草稿", "临时", "调试", "实验", "分析", "提示词",
    "笔记", "清单",
)
SIGNALS = re.compile("|".join(re.escape(term) for term in COLLECT_SIGNAL_TERMS), re.IGNORECASE)
COLLECT_CONTENT_EXTENSIONS = (".md", ".txt", ".rst", ".log")
TEXT_EXTENSIONS = {
    ".md", ".txt", ".rst", ".log", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".py", ".ps1", ".sh", ".bat", ".cmd", ".js", ".ts", ".tsx", ".jsx",
}
SCRIPT_EXTENSIONS = {".py", ".ps1", ".sh", ".bat", ".cmd", ".js", ".ts"}
PROTECTED_NAMES = {
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "package.json",
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pyproject.toml",
    "requirements.txt", "cargo.toml", "go.mod", "readme.md", "changelog.md", "license",
}
PROTECTED_PARTS = {"src", "app", "lib", "tests", "test", "__tests__", ".github", "docs"}
CORE_UPGRADE_FILES = {
    "AGENTS.md", WORKSPACE_CONFIG_NAME, ARTIFACT_REGISTRY_NAME, AUTHORITY_REGISTRY_NAME, AUTHORITY_VIEW_NAME, "README.md", "STATE.md", "MANIFEST.md", "docs/UPGRADE_REQUIREMENTS.md",
    "docs/UPGRADE_PLAN.md", "docs/IMPLEMENTATION_PLAN.md", "reports/FINAL_REPORT.md",
    "reviews/POST_UPGRADE_REVIEW.md", "delivery/POLICY.json",
}
COLLECT_DESTINATION_PREFIXES = (
    ".upgrade/docs/", ".upgrade/reports/", ".upgrade/decisions/", ".upgrade/risks/", ".upgrade/exceptions/", ".upgrade/delivery/", ".upgrade/reviews/",
    ".upgrade/prompts/", ".upgrade/logs/", ".upgrade/tmp/", ".upgrade/archive/",
)
MANAGED_CANONICAL_DESTINATIONS = {
    ".upgrade/AGENTS.md", f".upgrade/{WORKSPACE_CONFIG_NAME}", f".upgrade/{ARTIFACT_REGISTRY_NAME}", f".upgrade/{AUTHORITY_REGISTRY_NAME}", f".upgrade/{AUTHORITY_VIEW_NAME}", ".upgrade/README.md", ".upgrade/STATE.md",
    ".upgrade/MANIFEST.md", ".upgrade/traces/last-maintenance.md", ".upgrade/delivery/POLICY.json",
    *(f".upgrade/{target}" for target in ALL_TEMPLATE_TARGETS.values()),
}


AUDIT_EXCLUDED_DIRS = {".git", ".upgrade", "node_modules", "vendor", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache"}
GENERATED_PARTS = {"coverage", "test-results", "test_results", "dist", "build", ".cache", "miniprogram_npm", ".next", "target", "out"}
TEMPORARY_PARTS = {"tmp", "temp", ".tmp", "scratch", "debug", "debug-output", "logs", ".project-tmp"}
HISTORICAL_PARTS = {"archive", "archives", "old", "obsolete", "superseded", "backup", "backups"}
PERMANENT_PARTS = {"src", "app", "lib", "tests", "test", "__tests__", "docs", "cloudfunctions", "miniprogram"}
SENSITIVE_NAME_RE = re.compile(
    r"(^|[._-])(secret|secrets|credential|credentials|private[-_]?key|api[-_]?key|access[-_]?token|refresh[-_]?token)([._-]|$)|"
    r"^\.env(?:\.|$)|^id_(?:rsa|dsa|ecdsa|ed25519)(?:\.|$)",
    re.IGNORECASE,
)
SENSITIVE_CONTENT_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?:AWS_SECRET_ACCESS_KEY|OPENAI_API_KEY|API_KEY|ACCESS_TOKEN|REFRESH_TOKEN|CLIENT_SECRET)\s*[:=]\s*[^\s]{8,}|"
    r"Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/-]{12,}",
    re.IGNORECASE,
)
MUTATION_COMMANDS = {
    "initialize", "sync", "authority-apply", "artifact-policy-apply", "collect-apply", "phase-update", "cleanup-apply", "purge-apply", "validation-record", "git-policy-apply",
    "decision-apply", "risk-apply", "recovery-apply", "exception-apply", "delivery-policy-apply", "delivery-apply",
}
TRANSACTIONAL_MUTATION_COMMANDS = MUTATION_COMMANDS - {"recovery-apply"}

GUARD_BLOCK = """<!-- project-upgrade-maintainer:start -->
## Project Upgrade Governance

This repository contains a governed upgrade workspace at `.upgrade/`. Before reading, planning, or modifying upgrade-workflow artifacts, read `.upgrade/AGENTS.md`, which is the canonical scoped governance contract for `.upgrade/**`.

Canonical context: `.upgrade/CONFIG.json` (active modules), `.upgrade/AUTHORITY.md` / `.upgrade/AUTHORITY.json` (approved project authority map), `.upgrade/ARTIFACTS.json` (registered artifact policies), `.upgrade/STATE.md` (current state), `.upgrade/MANIFEST.md` (inventory), `.upgrade/docs/UPGRADE_REQUIREMENTS.md` (requirements), and `.upgrade/docs/UPGRADE_PLAN.md` (upgrade plan). When configured, `.upgrade/delivery/POLICY.json` defines approved minimum delivery evidence. Additional phase/evidence/lifecycle resources materialize only when their modules are active.

Do not bypass the review -> exact approval -> apply lifecycle for governed mutations. Protected or sensitive collection requires exact-path authorization again at apply time. Repository/local instructions remain authoritative for their own scope; surface conflicts instead of silently resolving them.
<!-- project-upgrade-maintainer:end -->"""

GITIGNORE_BLOCK = """# project-upgrade-maintainer:start
# Upgrade workflow temporary and generated artifacts
.upgrade/tmp/*
!.upgrade/tmp/.gitkeep
.upgrade/logs/*
!.upgrade/logs/.gitkeep
.upgrade/archive/*
!.upgrade/archive/.gitkeep
.upgrade/checkpoints/*.tmp
.upgrade/checkpoints/*.bak
.upgrade/reports/*.tmp
.upgrade/reports/*.bak
.upgrade/transactions/*
!.upgrade/transactions/.gitkeep
# project-upgrade-maintainer:end"""

DOCKERIGNORE_BLOCK = """# project-upgrade-maintainer:start
# Exclude upgrade workflow workspace from Docker build context by default
.upgrade/tmp
.upgrade/logs
.upgrade/archive
.upgrade/checkpoints
.upgrade/prompts
.upgrade/traces
.upgrade/transactions/*
!.upgrade/transactions/.gitkeep
# project-upgrade-maintainer:end"""

__all__ = [name for name in globals() if name.isupper()]
