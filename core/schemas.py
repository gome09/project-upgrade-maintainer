"""Schema validation and canonical normalization for managed state."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .constants import (
    AGENTS_ARTIFACT_END,
    AGENTS_ARTIFACT_START,
    ARTIFACT_DOCKER_POLICIES,
    ARTIFACT_GIT_POLICIES,
    ARTIFACT_KINDS,
    ARTIFACT_LIFECYCLES,
    ARTIFACT_REGISTRY_NAME,
    ARTIFACT_REGISTRY_SCHEMA_VERSION,
    AUTHORITY_KINDS,
    AUTHORITY_LEVELS,
    AUTHORITY_REGISTRY_NAME,
    AUTHORITY_REGISTRY_SCHEMA_VERSION,
    GIT_POLICY_END,
    GIT_POLICY_START,
    GOVERNANCE_CONTRACT_MARKER,
    GUARD_END,
    GUARD_START,
    IGNORE_END,
    IGNORE_START,
    MANAGED_CANONICAL_DESTINATIONS,
    MODULE_DEPENDENCIES,
    MODULE_ORDER,
    README_ARTIFACT_END,
    README_ARTIFACT_START,
    WORKSPACE_CONFIG_NAME,
    WORKSPACE_CONFIG_SCHEMA_VERSION,
    PREVIOUS_WORKSPACE_CONFIG_SCHEMA_VERSIONS,
    DECISION_SCHEMA_VERSION, RISK_SCHEMA_VERSION, VALIDATION_SCHEMA_VERSION, EXCEPTION_SCHEMA_VERSION, DELIVERY_SCHEMA_VERSION, DELIVERY_POLICY_SCHEMA_VERSION,
    RISK_STATUSES, RISK_LEVELS, EXCEPTION_TYPES, EXCEPTION_STATUSES, DELIVERY_GATES,
)
from .errors import CliError
from .paths import normalize_user_path


def ordered_modules(modules: set[str] | list[str] | tuple[str, ...]) -> list[str]:
    selected = set(modules)
    unknown = selected - set(MODULE_ORDER)
    if unknown:
        raise CliError(f"unknown workspace module(s): {', '.join(sorted(unknown))}")
    selected.add("core")
    pending = list(selected)
    while pending:
        module = pending.pop()
        for dependency in MODULE_DEPENDENCIES[module]:
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return [module for module in MODULE_ORDER if module in selected]


def validate_workspace_config(payload: Any, *, allow_previous: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CliError(f"{WORKSPACE_CONFIG_NAME} must contain a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version != WORKSPACE_CONFIG_SCHEMA_VERSION:
        if not (allow_previous and schema_version in PREVIOUS_WORKSPACE_CONFIG_SCHEMA_VERSIONS):
            if schema_version in PREVIOUS_WORKSPACE_CONFIG_SCHEMA_VERSIONS:
                raise CliError(
                    f"{WORKSPACE_CONFIG_NAME} schema_version {schema_version} is obsolete; "
                    f"run sync to migrate to schema {WORKSPACE_CONFIG_SCHEMA_VERSION}"
                )
            raise CliError(f"unsupported {WORKSPACE_CONFIG_NAME} schema_version")
    if payload.get("managed_by") != "project-upgrade-maintainer":
        raise CliError(f"{WORKSPACE_CONFIG_NAME} is not owned by project-upgrade-maintainer")
    modules = payload.get("active_modules")
    if not isinstance(modules, list) or not all(isinstance(item, str) for item in modules):
        raise CliError(f"{WORKSPACE_CONFIG_NAME} active_modules must be a string list")
    payload = dict(payload)
    payload["active_modules"] = ordered_modules(modules)
    profile = payload.get("initial_profile")
    if not isinstance(profile, str) or profile.casefold() not in {"lean", "standard", "governed", "custom", "legacy-governed"}:
        raise CliError(f"{WORKSPACE_CONFIG_NAME} initial_profile is invalid")
    for field in ("artifact_registry_sha256", "authority_registry_sha256"):
        registry_digest = payload.get(field)
        if registry_digest is not None and (not isinstance(registry_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", registry_digest)):
            raise CliError(f"{WORKSPACE_CONFIG_NAME} {field} is invalid")
    if schema_version == WORKSPACE_CONFIG_SCHEMA_VERSION and payload.get("authority_registry_sha256") is None:
        raise CliError(f"{WORKSPACE_CONFIG_NAME} authority_registry_sha256 is required in schema {WORKSPACE_CONFIG_SCHEMA_VERSION}")
    return payload


def _single_line_policy_text(value: Any, label: str, *, allow_empty: bool = True) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CliError(f"artifact policy {label} must be a string")
    if any(character in value for character in ("\x00", "\n", "\r")):
        raise CliError(f"artifact policy {label} must be a single line")
    value = value.strip()
    if not allow_empty and not value:
        raise CliError(f"artifact policy {label} must not be empty")
    if len(value) > 500:
        raise CliError(f"artifact policy {label} is too long")
    forbidden_markers = (
        GOVERNANCE_CONTRACT_MARKER, AGENTS_ARTIFACT_START, AGENTS_ARTIFACT_END,
        README_ARTIFACT_START, README_ARTIFACT_END, GUARD_START, GUARD_END,
        IGNORE_START, IGNORE_END, GIT_POLICY_START, GIT_POLICY_END,
    )
    if any(marker in value for marker in forbidden_markers):
        raise CliError(f"artifact policy {label} contains a managed marker")
    return value


def normalize_registered_artifact_path(value: str) -> str:
    value = normalize_user_path(value)
    if not value:
        raise CliError("artifact policy path must not be empty")
    if Path(value).is_absolute():
        raise CliError("artifact policy path must be repository-relative")
    if not value.startswith(".upgrade/"):
        value = f".upgrade/{value}"
    if value == ".upgrade" or ".." in Path(value).parts:
        raise CliError(f"artifact policy path is outside .upgrade: {value}")
    if value in MANAGED_CANONICAL_DESTINATIONS:
        raise CliError(f"artifact policy cannot replace a managed canonical workspace file: {value}")
    return value


def validate_artifact_policy_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise CliError("artifact policy entry must be a JSON object")
    normalized_entry = dict(entry)
    normalized_entry["path"] = normalize_registered_artifact_path(str(entry.get("path", "")))
    kind = entry.get("kind")
    if kind not in ARTIFACT_KINDS:
        raise CliError(f"artifact policy kind is invalid for {normalized_entry['path']}")
    module = entry.get("module")
    if module not in MODULE_ORDER:
        raise CliError(f"artifact policy module is invalid for {normalized_entry['path']}")
    lifecycle = entry.get("lifecycle")
    if lifecycle not in ARTIFACT_LIFECYCLES:
        raise CliError(f"artifact policy lifecycle is invalid for {normalized_entry['path']}")
    git_policy = entry.get("git_policy")
    if git_policy not in ARTIFACT_GIT_POLICIES:
        raise CliError(f"artifact policy git_policy is invalid for {normalized_entry['path']}")
    docker_policy = entry.get("docker_policy")
    if docker_policy not in ARTIFACT_DOCKER_POLICIES:
        raise CliError(f"artifact policy docker_policy is invalid for {normalized_entry['path']}")
    for key in ("required", "expose_in_agents", "expose_in_readme"):
        if not isinstance(entry.get(key), bool):
            raise CliError(f"artifact policy {key} must be boolean for {normalized_entry['path']}")
    normalized_entry["purpose"] = _single_line_policy_text(entry.get("purpose", ""), "purpose", allow_empty=False)
    rules = entry.get("rules", [])
    if not isinstance(rules, list) or not all(isinstance(rule, str) for rule in rules):
        raise CliError(f"artifact policy rules must be a string list for {normalized_entry['path']}")
    normalized_entry["rules"] = [
        _single_line_policy_text(rule, "rule", allow_empty=False) for rule in rules
    ]
    normalized_entry["classifier_reason"] = _single_line_policy_text(
        entry.get("classifier_reason", "user-approved policy"), "classifier_reason", allow_empty=False
    )
    return normalized_entry


def validate_authority_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise CliError("authority entry must be a JSON object")
    path_value = str(entry.get("path", "")).strip().replace("\\", "/")
    candidate = Path(path_value)
    if not path_value or candidate.is_absolute() or ".." in candidate.parts:
        raise CliError("authority entry path must be repository-relative")
    path_value = candidate.as_posix().lstrip("./")
    if path_value == ".upgrade" or path_value.startswith(".upgrade/"):
        raise CliError("external authority entry must be outside .upgrade/")
    kind = entry.get("kind")
    if kind not in AUTHORITY_KINDS:
        raise CliError(f"authority kind is invalid for {path_value}")
    authority = entry.get("authority")
    if authority not in AUTHORITY_LEVELS:
        raise CliError(f"authority level is invalid for {path_value}")
    scope = str(entry.get("scope", ".")).strip().replace("\\", "/") or "."
    if scope != ".":
        scope_path = Path(scope)
        if scope_path.is_absolute() or ".." in scope_path.parts:
            raise CliError(f"authority scope is invalid for {path_value}")
        scope = scope_path.as_posix().strip("/") or "."
        if scope == ".upgrade" or scope.startswith(".upgrade/"):
            raise CliError(f"authority scope must not target .upgrade/ for {path_value}")
    if authority == "repository" and scope != ".":
        raise CliError(f"repository authority must use scope '.' for {path_value}")
    if authority == "scoped" and scope == ".":
        raise CliError(f"scoped authority requires a non-root scope for {path_value}")
    if not isinstance(entry.get("required"), bool):
        raise CliError(f"authority required must be boolean for {path_value}")
    if entry.get("managed") is not False:
        raise CliError(f"external authority source must remain project-owned (managed=false): {path_value}")
    digest = entry.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise CliError(f"authority sha256 is invalid for {path_value}")
    size = entry.get("size")
    if not isinstance(size, int) or size < 0:
        raise CliError(f"authority size is invalid for {path_value}")
    return {
        "path": path_value,
        "kind": kind,
        "scope": scope,
        "authority": authority,
        "required": entry["required"],
        "managed": False,
        "sha256": digest,
        "size": size,
    }


def validate_authority_registry(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CliError(f"{AUTHORITY_REGISTRY_NAME} must contain a JSON object")
    if payload.get("schema_version") != AUTHORITY_REGISTRY_SCHEMA_VERSION:
        raise CliError(f"unsupported {AUTHORITY_REGISTRY_NAME} schema_version")
    if payload.get("managed_by") != "project-upgrade-maintainer":
        raise CliError(f"{AUTHORITY_REGISTRY_NAME} is not owned by project-upgrade-maintainer")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise CliError(f"{AUTHORITY_REGISTRY_NAME} entries must be a list")
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in entries:
        entry = validate_authority_entry(raw)
        key = entry["path"].casefold()
        if key in seen:
            raise CliError(f"duplicate authority source path: {entry['path']}")
        seen.add(key)
        validated.append(entry)
    validated.sort(key=lambda item: item["path"].casefold())
    return {
        "schema_version": AUTHORITY_REGISTRY_SCHEMA_VERSION,
        "managed_by": "project-upgrade-maintainer",
        "entries": validated,
    }


def validate_artifact_registry(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CliError(f"{ARTIFACT_REGISTRY_NAME} must contain a JSON object")
    if payload.get("schema_version") != ARTIFACT_REGISTRY_SCHEMA_VERSION:
        raise CliError(f"unsupported {ARTIFACT_REGISTRY_NAME} schema_version")
    if payload.get("managed_by") != "project-upgrade-maintainer":
        raise CliError(f"{ARTIFACT_REGISTRY_NAME} is not owned by project-upgrade-maintainer")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise CliError(f"{ARTIFACT_REGISTRY_NAME} entries must be a list")
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in entries:
        entry = validate_artifact_policy_entry(raw)
        key = entry["path"].casefold()
        if key in seen:
            raise CliError(f"duplicate artifact policy path: {entry['path']}")
        seen.add(key)
        validated.append(entry)
    validated.sort(key=lambda item: item["path"].casefold())
    return {
        "schema_version": ARTIFACT_REGISTRY_SCHEMA_VERSION,
        "managed_by": "project-upgrade-maintainer",
        "entries": validated,
    }


_GOVERNANCE_ID_RE = re.compile(r"^(DEC|RISK|EXC|DLV)-[0-9A-F]{12}$")


def normalize_governance_id(value: Any, prefix: str) -> str:
    if not isinstance(value, str):
        raise CliError(f"{prefix} id must be a string")
    normalized = value.strip().upper()
    if not re.fullmatch(rf"{re.escape(prefix)}-[0-9A-F]{{12}}", normalized):
        raise CliError(f"invalid {prefix} id: {value}")
    return normalized


def _governance_text(value: Any, label: str, *, allow_empty: bool = True, single_line: bool = False, max_length: int = 8000) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise CliError(f"{label} must be a string")
    if "\x00" in value or (single_line and ("\n" in value or "\r" in value)):
        raise CliError(f"{label} contains invalid characters")
    value = value.strip()
    if not allow_empty and not value:
        raise CliError(f"{label} must not be empty")
    if len(value) > max_length:
        raise CliError(f"{label} is too long")
    return value


def _governance_timestamp(value: Any, label: str) -> str:
    import datetime as _dt
    if not isinstance(value, str) or not value.strip():
        raise CliError(f"{label} must be an ISO timestamp")
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CliError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise CliError(f"{label} must include a timezone")
    return value


def _optional_governance_timestamp(value: Any, label: str) -> str | None:
    if value in (None, ""):
        return None
    return _governance_timestamp(value, label)


def _governance_string_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CliError(f"{label} must be a string list")
    return [_governance_text(item, label, allow_empty=False) for item in value]


def _governance_id_list(value: Any, label: str, prefix: str) -> list[str]:
    values = _governance_string_list(value, label)
    return list(dict.fromkeys(normalize_governance_id(item, prefix) for item in values))


def governance_identity_id(prefix: str, semantic: Any) -> str:
    encoded = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:12].upper()}"


def governance_record_digest(payload: dict[str, Any]) -> str:
    """Return a canonical SHA-256 for a governance/evidence record.

    The self-referential ``record_digest`` field is excluded.  This provides a
    deterministic integrity seal and also lets a v2 risk revision chain to a
    Schema v1 predecessor by hashing its canonical content.
    """
    value = dict(payload)
    value.pop("record_digest", None)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _approval_plan_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise CliError(f"{label} approval_plan_id must be a Plan Schema 3 plan_id")
    return value


def _record_digest(value: Any, payload: dict[str, Any], label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise CliError(f"{label} record_digest must be a sha256 hex digest")
    if value != governance_record_digest(payload):
        raise CliError(f"{label} record_digest does not match record content")
    return value


def seal_governance_record(payload: dict[str, Any], approval_plan_id: str, *, previous_record_digest: str | None = None) -> dict[str, Any]:
    """Seal an already validated v2 proposal after exact plan approval."""
    out = dict(payload)
    _approval_plan_id(approval_plan_id, str(out.get("kind") or "governance"))
    out["approval_plan_id"] = approval_plan_id
    if out.get("kind") == "risk":
        out["previous_record_digest"] = previous_record_digest
    out["record_digest"] = governance_record_digest(out)
    return out


def validate_decision_record(payload: Any, *, allow_unsealed: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CliError("decision record must be a JSON object")
    version = payload.get("schema_version")
    legacy_expected = {"schema_version","kind","decision_id","recorded_at","title","decision","rationale","scope","evidence","supersedes","authority","approval_basis"}
    current_expected = legacy_expected | {"approval_plan_id","record_digest"}
    expected = legacy_expected if version == 1 else current_expected - ({"approval_plan_id","record_digest"} if allow_unsealed else set())
    if set(payload) != expected:
        raise CliError("decision record fields do not match the supported schema")
    if version not in {1, DECISION_SCHEMA_VERSION} or payload.get("kind") != "decision":
        raise CliError("unsupported decision record schema")
    out = dict(payload)
    out["decision_id"] = normalize_governance_id(payload.get("decision_id"), "DEC")
    out["recorded_at"] = _governance_timestamp(payload.get("recorded_at"), "decision recorded_at")
    out["title"] = _governance_text(payload.get("title"), "decision title", allow_empty=False, single_line=True, max_length=240)
    out["decision"] = _governance_text(payload.get("decision"), "decision", allow_empty=False)
    out["rationale"] = _governance_text(payload.get("rationale"), "decision rationale")
    out["scope"] = _governance_text(payload.get("scope"), "decision scope")
    out["evidence"] = _governance_string_list(payload.get("evidence"), "decision evidence")
    supersedes = payload.get("supersedes")
    out["supersedes"] = None if supersedes in (None, "") else normalize_governance_id(supersedes, "DEC")
    if out["supersedes"] == out["decision_id"]:
        raise CliError("a decision cannot supersede itself")
    if payload.get("authority") != "user":
        raise CliError("decision authority must be user")
    if payload.get("approval_basis") != "exact-plan-id":
        raise CliError("decision approval_basis must be exact-plan-id")
    if version == DECISION_SCHEMA_VERSION:
        semantic = {
            "recorded_at": out["recorded_at"], "title": out["title"], "decision": out["decision"],
            "rationale": out["rationale"], "scope": out["scope"], "evidence": out["evidence"],
            "supersedes": out["supersedes"],
        }
        if out["decision_id"] != governance_identity_id("DEC", semantic):
            raise CliError("decision_id does not match governed decision content")
    if version == DECISION_SCHEMA_VERSION and not allow_unsealed:
        out["approval_plan_id"] = _approval_plan_id(payload.get("approval_plan_id"), "decision")
        out["record_digest"] = _record_digest(payload.get("record_digest"), payload, "decision")
    return out


def validate_risk_record(payload: Any, *, allow_unsealed: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CliError("risk record must be a JSON object")
    version = payload.get("schema_version")
    legacy_expected = {"schema_version","kind","risk_id","revision","recorded_at","title","description","likelihood","impact","status","mitigation","evidence","acceptance_reason","resolution","transition_reason","authority","approval_basis"}
    current_expected = legacy_expected | {"scope","approval_plan_id","previous_record_digest","record_digest"}
    expected = legacy_expected if version == 1 else current_expected - ({"approval_plan_id","previous_record_digest","record_digest"} if allow_unsealed else set())
    if set(payload) != expected:
        raise CliError("risk record fields do not match the supported schema")
    if version not in {1, RISK_SCHEMA_VERSION} or payload.get("kind") != "risk":
        raise CliError("unsupported risk record schema")
    out = dict(payload)
    out["risk_id"] = normalize_governance_id(payload.get("risk_id"), "RISK")
    if not isinstance(payload.get("revision"), int) or payload["revision"] < 1:
        raise CliError("risk revision must be a positive integer")
    out["recorded_at"] = _governance_timestamp(payload.get("recorded_at"), "risk recorded_at")
    out["title"] = _governance_text(payload.get("title"), "risk title", allow_empty=False, single_line=True, max_length=240)
    out["description"] = _governance_text(payload.get("description"), "risk description", allow_empty=False)
    if version == RISK_SCHEMA_VERSION:
        out["scope"] = _governance_text(payload.get("scope", ""), "risk scope")
    if payload.get("likelihood") not in RISK_LEVELS or payload.get("impact") not in RISK_LEVELS:
        raise CliError("risk likelihood/impact is invalid")
    if payload.get("status") not in RISK_STATUSES:
        raise CliError(f"risk status must be one of: {', '.join(RISK_STATUSES)}")
    for key in ("mitigation","acceptance_reason","resolution","transition_reason"):
        out[key] = _governance_text(payload.get(key), f"risk {key}")
    out["evidence"] = _governance_string_list(payload.get("evidence"), "risk evidence")
    if out["status"] == "accepted" and not out["acceptance_reason"]:
        raise CliError("accepted risk requires an explicit acceptance_reason")
    if out["status"] == "mitigated" and not (out["mitigation"] or out["evidence"]):
        raise CliError("mitigated risk requires mitigation or evidence")
    if out["status"] == "closed" and not out["resolution"]:
        raise CliError("closed risk requires an explicit resolution")
    if payload.get("authority") != "user":
        raise CliError("risk authority must be user")
    if payload.get("approval_basis") != "exact-plan-id":
        raise CliError("risk approval_basis must be exact-plan-id")
    if version == RISK_SCHEMA_VERSION and out["revision"] == 1:
        semantic = {
            "recorded_at": out["recorded_at"], "title": out["title"], "description": out["description"],
            "likelihood": out["likelihood"], "impact": out["impact"], "scope": out["scope"],
            "status": out["status"], "mitigation": out["mitigation"], "evidence": out["evidence"],
            "acceptance_reason": out["acceptance_reason"], "resolution": out["resolution"],
            "transition_reason": out["transition_reason"], "seed": out["title"],
        }
        if out["risk_id"] != governance_identity_id("RISK", semantic):
            raise CliError("risk_id does not match initial governed risk content")
    if version == RISK_SCHEMA_VERSION and not allow_unsealed:
        out["approval_plan_id"] = _approval_plan_id(payload.get("approval_plan_id"), "risk")
        previous = payload.get("previous_record_digest")
        if previous is not None and (not isinstance(previous, str) or not re.fullmatch(r"[0-9a-f]{64}", previous)):
            raise CliError("risk previous_record_digest must be null or a sha256 hex digest")
        out["previous_record_digest"] = previous
        out["record_digest"] = _record_digest(payload.get("record_digest"), payload, "risk")
    return out


def validate_risk_history(records: Any) -> list[dict[str, Any]]:
    if not isinstance(records, list) or not records:
        raise CliError("risk history must contain at least one revision")
    out = [validate_risk_record(item) for item in records]
    risk_id = out[0]["risk_id"]
    for index, item in enumerate(out, start=1):
        if item["risk_id"] != risk_id or item["revision"] != index:
            raise CliError("risk history revisions are inconsistent")
        if index > 1 and not item["transition_reason"]:
            raise CliError("risk state revision requires an explicit transition_reason")
        if item.get("schema_version") == RISK_SCHEMA_VERSION:
            expected_previous = None if index == 1 else governance_record_digest(records[index - 2])
            if item.get("previous_record_digest") != expected_previous:
                raise CliError("risk revision integrity chain is inconsistent")
    return out


def validate_validation_record(payload: Any, *, allow_legacy: bool = True) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CliError("validation record must be a JSON object")
    version = payload.get("schema_version")
    legacy_expected = {"timestamp","check","status","exit_code","environment","scope","note"}
    current_expected = legacy_expected | {"schema_version","subject_type","subject_ref","subject_digest","previous_record_digest","record_digest"}
    if version is None and allow_legacy:
        if set(payload) != legacy_expected:
            raise CliError("unversioned validation record fields do not match the supported schema")
    else:
        if set(payload) != current_expected or version != VALIDATION_SCHEMA_VERSION:
            raise CliError("validation record fields do not match the supported schema")
    out = dict(payload)
    out["timestamp"] = _governance_timestamp(payload.get("timestamp"), "validation timestamp")
    out["check"] = _governance_text(payload.get("check"), "validation check", allow_empty=False, single_line=True, max_length=500)
    if payload.get("status") not in {"passed", "failed", "unverified"}:
        raise CliError("validation status is invalid")
    code = payload.get("exit_code")
    if code is not None and not isinstance(code, int):
        raise CliError("validation exit_code must be an integer or null")
    if payload.get("status") == "passed" and code not in {None, 0}:
        raise CliError("passed validation cannot have a non-zero exit code")
    if payload.get("status") == "failed" and code == 0:
        raise CliError("failed validation cannot have exit code 0")
    out["environment"] = _governance_text(payload.get("environment") or "", "validation environment", single_line=True, max_length=500)
    out["scope"] = _governance_text(payload.get("scope") or "", "validation scope", max_length=2000)
    out["note"] = _governance_text(payload.get("note") or "", "validation note")
    if version == VALIDATION_SCHEMA_VERSION:
        if payload.get("subject_type") not in {"path", "git-worktree", "external-digest", "unbound"}:
            raise CliError("validation subject_type is invalid")
        out["subject_ref"] = _governance_text(payload.get("subject_ref"), "validation subject_ref", allow_empty=False, single_line=True, max_length=1000)
        digest = payload.get("subject_digest")
        if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise CliError("validation subject_digest must be null or a sha256 hex digest")
        if payload.get("subject_type") == "unbound" and digest is not None:
            raise CliError("unbound validation subject cannot contain a digest")
        if payload.get("subject_type") != "unbound" and digest is None:
            raise CliError("bound validation subject requires a digest")
        previous = payload.get("previous_record_digest")
        if previous is not None and (not isinstance(previous, str) or not re.fullmatch(r"[0-9a-f]{64}", previous)):
            raise CliError("validation previous_record_digest must be null or a sha256 hex digest")
        out["record_digest"] = _record_digest(payload.get("record_digest"), payload, "validation")
    return out


def validate_validation_history(records: Any) -> list[dict[str, Any]]:
    if not isinstance(records, list):
        raise CliError("validation history must be a list")
    out: list[dict[str, Any]] = []
    previous_raw: dict[str, Any] | None = None
    for item in records:
        record = validate_validation_record(item)
        if record.get("schema_version") == VALIDATION_SCHEMA_VERSION:
            expected = governance_record_digest(previous_raw) if previous_raw is not None else None
            if record.get("previous_record_digest") != expected:
                raise CliError("validation evidence integrity chain is inconsistent")
        out.append(record)
        previous_raw = item
    return out


def validate_exception_record(payload: Any, *, allow_unsealed: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CliError("exception record must be a JSON object")
    version = payload.get("schema_version")
    legacy_expected = {"schema_version","kind","exception_id","recorded_at","exception_type","status","title","scope","requirement","justification","impact","compensating_controls","evidence","linked_decisions","linked_risks","expires_at","resolution","supersedes","authority","approval_basis"}
    current_expected = legacy_expected | {"approval_plan_id","record_digest"}
    expected = legacy_expected if version == 1 else current_expected - ({"approval_plan_id","record_digest"} if allow_unsealed else set())
    if set(payload) != expected:
        raise CliError("exception record fields do not match the supported schema")
    if version not in {1, EXCEPTION_SCHEMA_VERSION} or payload.get("kind") != "exception":
        raise CliError("unsupported exception record schema")
    out = dict(payload)
    out["exception_id"] = normalize_governance_id(payload.get("exception_id"), "EXC")
    out["recorded_at"] = _governance_timestamp(payload.get("recorded_at"), "exception recorded_at")
    if payload.get("exception_type") not in EXCEPTION_TYPES:
        raise CliError(f"exception type must be one of: {', '.join(EXCEPTION_TYPES)}")
    if payload.get("status") not in EXCEPTION_STATUSES:
        raise CliError(f"exception status must be one of: {', '.join(EXCEPTION_STATUSES)}")
    for key, required in (("title",True),("scope",True),("requirement",True),("justification",True),("impact",False),("resolution",False)):
        out[key] = _governance_text(payload.get(key), f"exception {key}", allow_empty=not required, single_line=(key=="title"), max_length=8000)
    out["compensating_controls"] = _governance_string_list(payload.get("compensating_controls"), "exception compensating controls")
    out["evidence"] = _governance_string_list(payload.get("evidence"), "exception evidence")
    out["linked_decisions"] = _governance_id_list(payload.get("linked_decisions"), "exception linked decisions", "DEC")
    out["linked_risks"] = _governance_id_list(payload.get("linked_risks"), "exception linked risks", "RISK")
    out["expires_at"] = _optional_governance_timestamp(payload.get("expires_at"), "exception expires_at")
    supersedes = payload.get("supersedes")
    if supersedes in (None, ""):
        out["supersedes"] = None
    elif not isinstance(supersedes, str):
        raise CliError("exception supersedes must be an exception id or null")
    else:
        out["supersedes"] = normalize_governance_id(supersedes, "EXC")
        if out["supersedes"] == out["exception_id"]:
            raise CliError("an exception cannot supersede itself")
    if out["status"] in {"resolved","revoked"} and not out["resolution"]:
        raise CliError(f"{out['status']} exception requires an explicit resolution")
    if out["status"] in {"resolved","revoked"} and not out["supersedes"]:
        raise CliError(f"{out['status']} exception must supersede an existing exception")
    if payload.get("authority") != "user":
        raise CliError("exception authority must be user")
    if payload.get("approval_basis") != "exact-plan-id":
        raise CliError("exception approval_basis must be exact-plan-id")
    if version == EXCEPTION_SCHEMA_VERSION:
        semantic = {
            "recorded_at": out["recorded_at"], "exception_type": out["exception_type"], "status": out["status"],
            "title": out["title"], "scope": out["scope"], "requirement": out["requirement"],
            "justification": out["justification"], "impact": out["impact"],
            "compensating_controls": out["compensating_controls"], "evidence": out["evidence"],
            "linked_decisions": out["linked_decisions"], "linked_risks": out["linked_risks"],
            "expires_at": out["expires_at"], "resolution": out["resolution"], "supersedes": out["supersedes"],
        }
        if out["exception_id"] != governance_identity_id("EXC", semantic):
            raise CliError("exception_id does not match governed exception content")
    if version == EXCEPTION_SCHEMA_VERSION and not allow_unsealed:
        out["approval_plan_id"] = _approval_plan_id(payload.get("approval_plan_id"), "exception")
        out["record_digest"] = _record_digest(payload.get("record_digest"), payload, "exception")
    return out



def validate_delivery_policy(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CliError("delivery policy must be a JSON object")
    expected = {"schema_version","kind","policy_id","recorded_at","profile","gates","note","authority","approval_basis","approval_plan_id","record_digest"}
    if set(payload) != expected or payload.get("schema_version") != DELIVERY_POLICY_SCHEMA_VERSION or payload.get("kind") != "delivery-policy":
        raise CliError("delivery policy fields do not match the supported schema")
    out = dict(payload)
    out["recorded_at"] = _governance_timestamp(payload.get("recorded_at"), "delivery policy recorded_at")
    out["profile"] = _governance_text(payload.get("profile"), "delivery policy profile", allow_empty=False, single_line=True, max_length=200)
    out["note"] = _governance_text(payload.get("note") or "", "delivery policy note", max_length=8000)
    gates = payload.get("gates")
    if not isinstance(gates, dict) or set(gates) != set(DELIVERY_GATES):
        raise CliError("delivery policy gates must define readiness, final-acceptance, and rollback-readiness")
    normalized_gates: dict[str, list[str]] = {}
    for gate in DELIVERY_GATES:
        normalized_gates[gate] = list(dict.fromkeys(_governance_string_list(gates.get(gate), f"delivery policy {gate} required checks")))
    if not any(normalized_gates.values()):
        raise CliError("delivery policy must require at least one validation check across its gates")
    out["gates"] = normalized_gates
    semantic = {"profile": out["profile"], "gates": out["gates"], "note": out["note"]}
    out["policy_id"] = normalize_governance_id(payload.get("policy_id"), "DLP")
    if out["policy_id"] != governance_identity_id("DLP", semantic):
        raise CliError("delivery policy_id does not match governed policy content")
    if payload.get("authority") != "user":
        raise CliError("delivery policy authority must be user")
    if payload.get("approval_basis") != "exact-plan-id":
        raise CliError("delivery policy approval_basis must be exact-plan-id")
    out["approval_plan_id"] = _approval_plan_id(payload.get("approval_plan_id"), "delivery policy")
    out["record_digest"] = _record_digest(payload.get("record_digest"), payload, "delivery policy")
    return out

def _validate_subject(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CliError(f"{label} subject must be a JSON object")
    expected = {"type", "ref", "digest", "file_count"}
    if set(value) != expected:
        raise CliError(f"{label} subject fields do not match the supported schema")
    if value.get("type") not in {"path", "git-worktree", "external-digest", "unbound"}:
        raise CliError(f"{label} subject type is invalid")
    out = dict(value)
    out["ref"] = _governance_text(value.get("ref"), f"{label} subject ref", allow_empty=False, single_line=True, max_length=1000)
    digest = value.get("digest")
    if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise CliError(f"{label} subject digest must be null or a sha256 hex digest")
    if value.get("type") == "unbound" and digest is not None:
        raise CliError(f"{label} unbound subject cannot contain a digest")
    if value.get("type") != "unbound" and digest is None:
        raise CliError(f"{label} bound subject requires a digest")
    file_count = value.get("file_count")
    if file_count is not None and (not isinstance(file_count, int) or file_count < 0):
        raise CliError(f"{label} subject file_count must be a non-negative integer or null")
    return out


def _validate_delivery_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CliError("delivery evidence_summary must be a JSON object")
    expected = {"decisions","risks","exceptions","validation","recovery","workspace"}
    if set(value) != expected:
        raise CliError("delivery evidence_summary fields do not match the supported schema")
    out = {}
    for key in sorted(expected):
        if not isinstance(value.get(key), dict):
            raise CliError(f"delivery evidence_summary {key} must be a JSON object")
        out[key] = dict(value[key])
    return out


def validate_delivery_record(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CliError("delivery record must be a JSON object")
    version = payload.get("schema_version")
    legacy_expected = {"schema_version","kind","delivery_id","recorded_at","gate","target","scope","evidence_digest","evidence_summary","blockers","warnings","acceptance_note","outcome","authority","approval_basis","approval_plan_id"}
    v2_expected = legacy_expected | {"environment","subject","required_checks","record_digest"}
    v3_expected = v2_expected | {"delivery_policy_id","delivery_policy_digest","policy_required_checks"}
    expected = legacy_expected if version == 1 else (v2_expected if version == 2 else v3_expected)
    if set(payload) != expected:
        raise CliError("delivery record fields do not match the supported schema")
    if version not in {1, 2, DELIVERY_SCHEMA_VERSION} or payload.get("kind") != "delivery":
        raise CliError("unsupported delivery record schema")
    out = dict(payload)
    out["delivery_id"] = normalize_governance_id(payload.get("delivery_id"), "DLV")
    out["recorded_at"] = _governance_timestamp(payload.get("recorded_at"), "delivery recorded_at")
    if payload.get("gate") not in DELIVERY_GATES:
        raise CliError(f"delivery gate must be one of: {', '.join(DELIVERY_GATES)}")
    out["target"] = _governance_text(payload.get("target"), "delivery target", allow_empty=False, single_line=True, max_length=500)
    out["scope"] = _governance_text(payload.get("scope"), "delivery scope", max_length=2000)
    digest = payload.get("evidence_digest")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise CliError("delivery evidence_digest must be a sha256 hex digest")
    if version == 1:
        summary = payload.get("evidence_summary")
        if not isinstance(summary, dict) or set(summary) != {"decisions","risks","exceptions","validation","recovery"}:
            raise CliError("delivery evidence_summary fields do not match Schema v1")
        out["evidence_summary"] = {key: dict(summary[key]) for key in sorted(summary) if isinstance(summary[key], dict)}
        if len(out["evidence_summary"]) != 5:
            raise CliError("delivery Schema v1 evidence_summary entries must be JSON objects")
    else:
        out["environment"] = _governance_text(payload.get("environment"), "delivery environment", single_line=True, max_length=500)
        out["subject"] = _validate_subject(payload.get("subject"), "delivery")
        out["required_checks"] = list(dict.fromkeys(_governance_string_list(payload.get("required_checks"), "delivery required_checks")))
        if not out["required_checks"]:
            raise CliError("delivery required_checks must contain at least one check")
        out["evidence_summary"] = _validate_delivery_summary(payload.get("evidence_summary"))
        if version == DELIVERY_SCHEMA_VERSION:
            policy_id = payload.get("delivery_policy_id")
            if policy_id is not None:
                out["delivery_policy_id"] = normalize_governance_id(policy_id, "DLP")
            policy_digest = payload.get("delivery_policy_digest")
            if policy_digest is not None and (not isinstance(policy_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", policy_digest)):
                raise CliError("delivery delivery_policy_digest must be null or a sha256 hex digest")
            out["policy_required_checks"] = list(dict.fromkeys(_governance_string_list(payload.get("policy_required_checks"), "delivery policy_required_checks")))
            if not set(out["policy_required_checks"]) <= set(out["required_checks"]):
                raise CliError("delivery policy_required_checks must be included in required_checks")
            if (out.get("delivery_policy_id") is None) != (policy_digest is None):
                raise CliError("delivery policy id/digest must either both be present or both be null")
    out["blockers"] = _governance_string_list(payload.get("blockers"), "delivery blockers")
    if out["blockers"]:
        raise CliError("accepted delivery record cannot contain blockers")
    out["warnings"] = _governance_string_list(payload.get("warnings"), "delivery warnings")
    out["acceptance_note"] = _governance_text(payload.get("acceptance_note"), "delivery acceptance_note")
    if payload.get("outcome") != "accepted":
        raise CliError("delivery outcome must be accepted")
    if payload.get("authority") != "user":
        raise CliError("delivery authority must be user")
    if payload.get("approval_basis") != "exact-plan-id":
        raise CliError("delivery approval_basis must be exact-plan-id")
    out["approval_plan_id"] = _approval_plan_id(payload.get("approval_plan_id"), "delivery")
    if version in {2, DELIVERY_SCHEMA_VERSION}:
        out["record_digest"] = _record_digest(payload.get("record_digest"), payload, "delivery")
    return out
