"""Directed candidate-explanation references produce bounded read-only diagnostics."""

from copy import deepcopy
import json

import pytest

from cluetide.agent import Conclusion
from cluetide.citation_validation import (
    MAX_ASSESSMENTS, MAX_ASSESSMENT_NOTES, MAX_ASSESSMENT_REFERENCES,
    citation_validation,
)


TOKEN = "0x" + "b" * 40
COLLECTION = {"transfers": []}


def assessment(*, explanation_id="event_cause", status="supported", support=None,
               counter=None, unknowns=None):
    return {
        "explanation_id": explanation_id,
        "explanation": "已观察材料为选定交易的业务原因提供线索。",
        "status": status,
        "support_evidence_ids": ["observed:support"] if support is None and status == "supported" else support or [],
        "counter_evidence_ids": ["observed:counter"] if counter is None and status == "refuted" else counter or [],
        "unknowns": ["仍需核对完整交易语义。"] if unknowns is None and status == "unknown" else unknowns or [],
        "checks": ["核对已返回的限定证据和待补查项目。"],
    }


@pytest.fixture
def report():
    return {
        "schema_version": "cluetide-report/v1",
        "conclusion": {
            "summary": "报告引用已返回的有限观察。",
            "classification": "unresolved",
            "claims": [{"text": "存在一份已返回的背景材料。", "evidence_ids": ["observed:base"]}],
            "limitations": ["来源真实性需要人工复核。"],
            "assessments": [assessment()],
        },
        "agent": {"evidence": [
            {"evidence_id": identifier, "kind": "governance_source", "status": "ok",
             "payload": {"category": "governance_proposal", "text": "有限的观察材料。"}}
            for identifier in ("observed:base", "observed:support", "observed:counter")
        ]},
    }


def codes(result):
    return {issue["code"] for issue in result["issues"]}


@pytest.mark.parametrize("status,support_count,counter_count", [("supported", 1, 0), ("refuted", 0, 1), ("unknown", 0, 0)])
def test_each_assessment_status_checks_its_directed_references(report, status, support_count, counter_count):
    report["conclusion"]["assessments"] = [assessment(status=status)]

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "valid", result
    assert result["issues"] == []
    counts = result["checked_counts"]
    assert counts["assessments"] == 1
    assert counts["assessment_support_references"] == support_count
    assert counts["assessment_counter_references"] == counter_count
    assert counts["assessment_references"] == support_count + counter_count


@pytest.mark.parametrize("direction", ["support", "counter"])
def test_each_direction_reports_missing_references_at_its_exact_location(report, direction):
    supplied = "private-reference-value"
    report["conclusion"]["assessments"] = [assessment(
        status="unknown", support=[supplied] if direction == "support" else [],
        counter=[supplied] if direction == "counter" else [],
    )]

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "needs_review"
    missing = next(issue for issue in result["issues"] if issue["code"] == "missing_assessment_evidence")
    assert missing["location"] == f"conclusion.assessments[0].{direction}_evidence_ids[0]"
    assert supplied not in json.dumps(result)


@pytest.mark.parametrize("direction", ["support", "counter"])
@pytest.mark.parametrize("status", ["empty", "error"])
def test_assessment_references_require_successful_evidence(report, direction, status):
    identifier = "observed:" + direction
    report["conclusion"]["assessments"] = [assessment(
        status="unknown", support=[identifier] if direction == "support" else [],
        counter=[identifier] if direction == "counter" else [],
    )]
    next(item for item in report["agent"]["evidence"] if item["evidence_id"] == identifier)["status"] = status

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "needs_review"
    assert "unsuccessful_assessment_evidence" in codes(result)


@pytest.mark.parametrize("direction", ["support", "counter"])
@pytest.mark.parametrize("conflicting", [False, True])
def test_repeated_agent_evidence_ids_are_ambiguous_in_both_directions(report, direction, conflicting):
    identifier = "observed:" + direction
    report["conclusion"]["assessments"] = [assessment(
        status="unknown", support=[identifier] if direction == "support" else [],
        counter=[identifier] if direction == "counter" else [],
    )]
    duplicate = deepcopy(next(item for item in report["agent"]["evidence"] if item["evidence_id"] == identifier))
    if conflicting:
        duplicate.update(status="error", payload={"private": "private-evidence-payload"})
    report["agent"]["evidence"].append(duplicate)

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "needs_review"
    assert {"duplicate_evidence_id", "ambiguous_assessment_evidence"} <= codes(result)
    assert "private-evidence-payload" not in json.dumps(result)


@pytest.mark.parametrize("status,field,value", [
    ("supported", "support_evidence_ids", []),
    ("refuted", "counter_evidence_ids", []),
    ("unknown", "unknowns", []),
    ("unknown", "unknowns", ["   "]),
])
def test_status_requirements_produce_review_diagnostics(report, status, field, value):
    candidate = assessment(status=status)
    candidate[field] = value
    report["conclusion"]["assessments"] = [candidate]

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "needs_review"
    issue = next(issue for issue in result["issues"] if issue["code"] == "assessment_status_requirement")
    assert issue["location"] == "conclusion.assessments[0]." + field


@pytest.mark.parametrize("direction", ["support", "counter"])
def test_one_direction_names_each_evidence_reference_once(report, direction):
    candidate = assessment(status="unknown")
    candidate[direction + "_evidence_ids"] = ["observed:" + direction] * 2
    report["conclusion"]["assessments"] = [candidate]

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "needs_review"
    assert "duplicate_assessment_reference" in codes(result)


def test_each_candidate_explanation_has_a_distinct_identifier(report):
    report["conclusion"]["assessments"] = [assessment(), assessment()]

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "needs_review"
    assert "duplicate_assessment_id" in codes(result)


@pytest.mark.parametrize("field,value", [
    ("status", "concluded"), ("explanation_id", "private-unstructured-id"),
    ("explanation", " "), ("support_evidence_ids", "private-reference-list"),
    ("counter_evidence_ids", [True]), ("unknowns", [7]),
    ("checks", []), ("checks", [" "]), ("checks", ["x" * 601]),
    ("private_extra_field", "private-extra-value"),
])
def test_malformed_assessments_return_safe_review_diagnostics(report, field, value):
    report["conclusion"]["assessments"][0][field] = value

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "needs_review"
    assert "malformed_assessment" in codes(result)
    assert "private-" not in json.dumps(result)


@pytest.mark.parametrize("section", ["assessments", "support_evidence_ids", "counter_evidence_ids", "unknowns", "checks"])
def test_assessment_sections_have_explicit_bounds_before_typed_validation(report, section):
    if section == "assessments":
        report["conclusion"][section] *= MAX_ASSESSMENTS + 1
    elif section.endswith("evidence_ids"):
        report["conclusion"]["assessments"][0][section] = ["observed:support"] * (MAX_ASSESSMENT_REFERENCES + 1)
    else:
        report["conclusion"]["assessments"][0][section] = ["具体的观察或证据缺口。"] * (MAX_ASSESSMENT_NOTES + 1)

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "needs_review"
    assert "check_limit_exceeded" in codes(result)
    assert len(result["issues"]) <= 64


def test_many_directed_reference_issues_keep_a_fixed_output_limit(report):
    support = [f"private-support-{number}" for number in range(8)]
    counter = [f"private-counter-{number}" for number in range(8)]
    report["conclusion"]["assessments"] = [assessment(
        explanation_id=f"candidate_{number}", support=support, counter=counter,
    ) for number in range(MAX_ASSESSMENTS)]

    result = citation_validation(report, COLLECTION)

    assert result["issue_count"] == MAX_ASSESSMENTS * 16
    assert len(result["issues"]) == 64
    assert result["issues_truncated"] is True
    assert result["checked_counts"]["assessment_references"] == MAX_ASSESSMENTS * 16
    assert "private-" not in json.dumps(result)


@pytest.mark.parametrize("status,before,after,expected", [
    ("supported", "100", "99", "valid"),
    ("refuted", "100", "100", "valid"),
    ("refuted", "100", "101", "valid"),
    ("supported", "100", "100", "needs_review"),
    ("refuted", "100", "99", "needs_review"),
])
def test_supply_decrease_status_matches_its_directed_raw_observations(report, status, before, after, expected):
    identifiers = [f"state:{TOKEN}:100", f"state:{TOKEN}:101"]
    for block, identifier, raw in zip((100, 101), identifiers, (before, after)):
        report["agent"]["evidence"].append({
            "evidence_id": identifier, "kind": "token_state", "status": "ok",
            "payload": {"token_address": TOKEN, "block_number": block, "total_supply_raw": raw},
        })
    report["conclusion"]["assessments"] = [assessment(
        explanation_id="supply_decrease", status=status,
        support=identifiers if status == "supported" else [],
        counter=identifiers if status == "refuted" else [],
    )]

    result = citation_validation(report, COLLECTION)

    assert result["status"] == expected, result
    if expected == "needs_review":
        assert "assessment_binding_mismatch" in codes(result)


def test_supply_decrease_requires_a_pair_of_historical_supply_observations(report):
    report["conclusion"]["assessments"] = [assessment(explanation_id="supply_decrease")]

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "needs_review"
    assert "assessment_binding_mismatch" in codes(result)


def test_supply_integer_bounds_are_checked_before_runtime_conversion(report, monkeypatch):
    import cluetide.citation_validation as module

    identifier = f"state:{TOKEN}:100"
    report["agent"]["evidence"].append({
        "evidence_id": identifier, "kind": "token_state", "status": "ok",
        "payload": {"token_address": TOKEN, "block_number": 100, "total_supply_raw": "9" * 100_000},
    })
    report["conclusion"]["assessments"] = [assessment(explanation_id="supply_decrease", support=[identifier])]
    monkeypatch.setattr(module, "validate_assessment_bindings", lambda *args, **kwargs: pytest.fail("Oversized supply conversion"))

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "needs_review"
    assert "check_limit_exceeded" in codes(result)


@pytest.mark.parametrize("candidate,category,expected", [
    ("governance_execution", "governance_proposal", "valid"),
    ("event_cause", "governance_proposal", "needs_review"),
    ("governance_execution", "incident_postmortem", "needs_review"),
])
def test_governance_classification_uses_a_bound_governance_assessment(report, candidate, category, expected):
    report["conclusion"]["classification"] = "governance_explained"
    report["conclusion"]["assessments"] = [assessment(explanation_id=candidate)]
    next(item for item in report["agent"]["evidence"] if item["evidence_id"] == "observed:support")["payload"]["category"] = category

    result = citation_validation(report, COLLECTION)

    assert result["status"] == expected, result
    if expected == "needs_review":
        assert "assessment_binding_mismatch" in codes(result)


def test_legacy_report_retains_its_original_check_counts(report):
    report["conclusion"].pop("assessments")

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "valid"
    assert result["checked_counts"] == {
        "claim_references": 1, "transfers": 0, "transfer_observation_links": 0,
        "receipt_transfer_links": 0, "agent_rpc_links": 0, "empty_query_links": 0,
    }


def test_typed_conclusions_use_the_same_directed_diagnostics_without_serialization(report, monkeypatch):
    report["conclusion"] = Conclusion.model_validate(report["conclusion"])
    monkeypatch.setattr(Conclusion, "model_dump", lambda *args, **kwargs: pytest.fail("Full report serialization"))

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "valid", result
    assert result["checked_counts"]["assessment_support_references"] == 1


def test_assessment_review_preserves_supplied_documents_and_report_bytes(report):
    report["conclusion"]["assessments"][0]["support_evidence_ids"] = ["missing:private-reference"]
    before_documents = deepcopy((report, COLLECTION))
    before_bytes = json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    result = citation_validation(report, COLLECTION)

    assert result["status"] == "needs_review"
    assert result["report_modified"] is False
    assert (report, COLLECTION) == before_documents
    assert json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8") == before_bytes
