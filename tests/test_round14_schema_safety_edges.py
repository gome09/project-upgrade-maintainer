from __future__ import annotations

import copy
import unittest

from core.constants import (
    DECISION_SCHEMA_VERSION,
    DELIVERY_SCHEMA_VERSION,
    EXCEPTION_SCHEMA_VERSION,
    RISK_SCHEMA_VERSION,
)
from core.errors import CliError
from core.schemas import (
    governance_identity_id,
    governance_record_digest,
    validate_decision_record,
    validate_delivery_record,
    validate_exception_record,
    validate_risk_history,
    validate_risk_record,
)


TS = "2026-09-10T12:00:00+00:00"


def _seal(record: dict) -> dict:
    record = dict(record)
    record["record_digest"] = governance_record_digest(record)
    return record


def decision_record() -> dict:
    semantic = {
        "recorded_at": TS,
        "title": "Direction",
        "decision": "Use the governed path",
        "rationale": "Evidence supports it",
        "scope": "release",
        "evidence": ["validation:release"],
        "supersedes": None,
    }
    return _seal({
        "schema_version": DECISION_SCHEMA_VERSION,
        "kind": "decision",
        "decision_id": governance_identity_id("DEC", semantic),
        **semantic,
        "authority": "user",
        "approval_basis": "exact-plan-id",
        "approval_plan_id": "b" * 64,
    })


def risk_record() -> dict:
    semantic = {
        "recorded_at": TS,
        "title": "Bounded risk",
        "description": "Known release exposure",
        "likelihood": "medium",
        "impact": "medium",
        "scope": "release",
        "status": "open",
        "mitigation": "",
        "evidence": [],
        "acceptance_reason": "",
        "resolution": "",
        "transition_reason": "",
    }
    return _seal({
        "schema_version": RISK_SCHEMA_VERSION,
        "kind": "risk",
        "risk_id": governance_identity_id("RISK", {**semantic, "seed": semantic["title"]}),
        "revision": 1,
        **semantic,
        "authority": "user",
        "approval_basis": "exact-plan-id",
        "approval_plan_id": "b" * 64,
        "previous_record_digest": None,
    })


def exception_record() -> dict:
    semantic = {
        "recorded_at": TS,
        "exception_type": "waiver",
        "status": "active",
        "title": "Compatibility waiver",
        "scope": "release",
        "requirement": "Legacy requirement",
        "justification": "Temporary compatibility need",
        "impact": "bounded",
        "compensating_controls": ["extra validation"],
        "evidence": ["validation:release"],
        "linked_decisions": [],
        "linked_risks": [],
        "expires_at": None,
        "resolution": "",
        "supersedes": None,
    }
    return _seal({
        "schema_version": EXCEPTION_SCHEMA_VERSION,
        "kind": "exception",
        "exception_id": governance_identity_id("EXC", semantic),
        **semantic,
        "authority": "user",
        "approval_basis": "exact-plan-id",
        "approval_plan_id": "b" * 64,
    })


def delivery_record() -> dict:
    return _seal({
        "schema_version": DELIVERY_SCHEMA_VERSION,
        "kind": "delivery",
        "delivery_id": "DLV-AAAAAAAAAAAA",
        "recorded_at": TS,
        "gate": "readiness",
        "target": "v15",
        "scope": "release",
        "environment": "prod",
        "subject": {"type": "external-digest", "ref": "artifact:v15", "digest": "c" * 64, "file_count": None},
        "required_checks": ["release-suite"],
        "evidence_digest": "a" * 64,
        "evidence_summary": {
            "decisions": {},
            "risks": {},
            "exceptions": {},
            "validation": {},
            "recovery": {},
            "workspace": {},
        },
        "blockers": [],
        "warnings": [],
        "acceptance_note": "Explicit acceptance",
        "outcome": "accepted",
        "authority": "user",
        "approval_basis": "exact-plan-id",
        "approval_plan_id": "b" * 64,
    })


class Round14SchemaSafetyEdgeCase(unittest.TestCase):
    def assert_bad(self, validator, payload) -> None:
        with self.assertRaises(CliError):
            validator(payload)

    def test_decision_schema_rejects_malformed_authority_identity_time_and_text(self) -> None:
        self.assert_bad(validate_decision_record, [])
        cases = []
        p = decision_record(); p["extra"] = True; cases.append(p)
        p = decision_record(); p["schema_version"] = 999; cases.append(p)
        p = decision_record(); p["decision_id"] = 1; cases.append(p)
        p = decision_record(); p["decision_id"] = "DEC-bad"; cases.append(p)
        p = decision_record(); p["recorded_at"] = 1; cases.append(p)
        p = decision_record(); p["recorded_at"] = "not-a-time"; cases.append(p)
        p = decision_record(); p["recorded_at"] = "2026-09-10T12:00:00"; cases.append(p)
        p = decision_record(); p["title"] = 1; cases.append(p)
        p = decision_record(); p["title"] = "bad\nline"; cases.append(p)
        p = decision_record(); p["title"] = ""; cases.append(p)
        p = decision_record(); p["title"] = "x" * 241; cases.append(p)
        p = decision_record(); p["evidence"] = [1]; cases.append(p)
        p = decision_record(); p["supersedes"] = p["decision_id"]; cases.append(p)
        p = decision_record(); p["authority"] = "cli"; cases.append(p)
        p = decision_record(); p["approval_basis"] = "implicit"; cases.append(p)
        for payload in cases:
            with self.subTest(payload=payload):
                self.assert_bad(validate_decision_record, payload)

    def test_risk_schema_rejects_invalid_state_acceptance_and_revision_history(self) -> None:
        self.assert_bad(validate_risk_record, None)
        cases = []
        p = risk_record(); p.pop("impact"); cases.append(p)
        p = risk_record(); p["kind"] = "decision"; cases.append(p)
        p = risk_record(); p["revision"] = 0; cases.append(p)
        p = risk_record(); p["likelihood"] = "certain"; cases.append(p)
        p = risk_record(); p["status"] = "ignored"; cases.append(p)
        p = risk_record(); p["status"] = "accepted"; cases.append(p)
        p = risk_record(); p["status"] = "mitigated"; cases.append(p)
        p = risk_record(); p["status"] = "closed"; cases.append(p)
        p = risk_record(); p["authority"] = "system"; cases.append(p)
        p = risk_record(); p["approval_basis"] = "none"; cases.append(p)
        for payload in cases:
            with self.subTest(payload=payload):
                self.assert_bad(validate_risk_record, payload)
        self.assert_bad(validate_risk_history, [])
        first = risk_record()
        second = copy.deepcopy(first); second["revision"] = 3; second["transition_reason"] = "change"
        self.assert_bad(validate_risk_history, [first, second])
        second = copy.deepcopy(first); second["revision"] = 2
        self.assert_bad(validate_risk_history, [first, second])

    def test_exception_schema_rejects_unsafe_lifecycle_and_supersedes_states(self) -> None:
        self.assert_bad(validate_exception_record, "not-an-object")
        cases = []
        p = exception_record(); p["unknown"] = 1; cases.append(p)
        p = exception_record(); p["schema_version"] = 0; cases.append(p)
        p = exception_record(); p["exception_type"] = "override"; cases.append(p)
        p = exception_record(); p["status"] = "accepted"; cases.append(p)
        p = exception_record(); p["supersedes"] = 42; cases.append(p)
        p = exception_record(); p["supersedes"] = p["exception_id"]; cases.append(p)
        p = exception_record(); p["status"] = "resolved"; p["supersedes"] = "EXC-BBBBBBBBBBBB"; cases.append(p)
        p = exception_record(); p["status"] = "resolved"; p["resolution"] = "done"; cases.append(p)
        p = exception_record(); p["authority"] = "agent"; cases.append(p)
        p = exception_record(); p["approval_basis"] = "manual-note"; cases.append(p)
        for payload in cases:
            with self.subTest(payload=payload):
                self.assert_bad(validate_exception_record, payload)

    def test_delivery_schema_rejects_tampered_evidence_and_non_user_acceptance(self) -> None:
        self.assert_bad(validate_delivery_record, [])
        cases = []
        p = delivery_record(); p.pop("scope"); cases.append(p)
        p = delivery_record(); p["schema_version"] = 0; cases.append(p)
        p = delivery_record(); p["gate"] = "deploy"; cases.append(p)
        p = delivery_record(); p["evidence_digest"] = "bad"; cases.append(p)
        p = delivery_record(); p["evidence_summary"] = []; cases.append(p)
        p = delivery_record(); p["evidence_summary"] = {"decisions": {}, "risks": {}, "exceptions": {}, "validation": {}}; cases.append(p)
        p = delivery_record(); p["evidence_summary"]["risks"] = []; cases.append(p)
        p = delivery_record(); p["blockers"] = ["still blocked"]; cases.append(p)
        p = delivery_record(); p["outcome"] = "ready"; cases.append(p)
        p = delivery_record(); p["authority"] = "cli"; cases.append(p)
        p = delivery_record(); p["approval_basis"] = "implicit"; cases.append(p)
        p = delivery_record(); p["approval_plan_id"] = "short"; cases.append(p)
        for payload in cases:
            with self.subTest(payload=payload):
                self.assert_bad(validate_delivery_record, payload)


if __name__ == "__main__":
    unittest.main()
