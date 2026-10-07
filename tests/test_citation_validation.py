"""Regression tests for read-only public citation and raw observation checks."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from cluetide.citation_validation import (
    MAX_AGENT_EVIDENCE, MAX_CLAIMS, MAX_CLAIM_REFERENCES,
    MAX_LOGS_PER_OBSERVATION, MAX_OBSERVATIONS, MAX_TRANSFERS,
    citation_validation,
)
from cluetide.evidence import TRANSFER_TOPIC, address_topic
from cluetide.schemas import EvidenceSet


ROOT = Path(__file__).resolve().parents[1]


def archived(version=1):
    with zipfile.ZipFile(ROOT / "data/demo/bundles" / f"real-acceptance-v{version}.zip") as bundle:
        return tuple(json.loads(bundle.read(name)) for name in ("report.json", "evidence.json", "raw.json"))


@pytest.fixture
def documents():
    return archived()


def codes(result):
    return {issue["code"] for issue in result["issues"]}


@pytest.mark.parametrize("version", [1, 2])
def test_archived_acceptance_citations_and_raw_links_pass_without_mutation(version):
    path = ROOT / "data/demo/bundles" / f"real-acceptance-v{version}.zip"
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    report, evidence, raw = archived(version)
    original_documents = copy.deepcopy((report, evidence, raw))
    result = citation_validation(report, evidence, raw)
    assert result["status"] == "valid", result
    assert result["issues"] == []
    assert result["checked_counts"] == {
        "claim_references": 5, "transfers": 1, "transfer_observation_links": 1,
        "receipt_transfer_links": 2, "agent_rpc_links": 2, "empty_query_links": 0,
    }
    assert result["report_modified"] is False
    assert result["factual_verification"] is False
    assert result["source_authenticity_verified"] is False
    assert "No factual verification" in result["scope"]
    assert (report, evidence, raw) == original_documents
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash


def test_pydantic_evidence_is_read_without_model_dump(documents, monkeypatch):
    report, evidence, raw = documents
    model = EvidenceSet.model_validate({**evidence, "raw": raw}, strict=True)
    monkeypatch.setattr(EvidenceSet, "model_dump", lambda *args, **kwargs: pytest.fail("Unexpected full evidence copy"))
    assert citation_validation(report, model)["status"] == "valid"


@pytest.mark.parametrize("status", ["empty", "error"])
def test_claim_cannot_cite_unsuccessful_agent_evidence(documents, status):
    report, evidence, raw = documents
    report["agent"]["evidence"][0]["status"] = status
    result = citation_validation(report, evidence, raw)
    assert result["status"] == "needs_review"
    assert "unsuccessful_claim_evidence" in codes(result)


def test_claim_dangling_reference_is_flagged_without_echoing_supplied_id(documents):
    report, evidence, raw = documents
    supplied = "sensitive-untrusted-reference-content"
    report["conclusion"]["claims"][0]["evidence_ids"] = [supplied]
    result = citation_validation(report, evidence, raw)
    assert "missing_claim_evidence" in codes(result)
    assert supplied not in json.dumps(result)


@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_agent_ids_are_ambiguous_even_when_identical(documents, conflicting):
    report, evidence, raw = documents
    duplicate = copy.deepcopy(report["agent"]["evidence"][0])
    if conflicting:
        duplicate["status"] = "error"
        duplicate["payload"] = {"untrusted": "content must not be echoed"}
    report["agent"]["evidence"].append(duplicate)
    result = citation_validation(report, evidence, raw)
    assert "duplicate_evidence_id" in codes(result)
    assert result["status"] == "needs_review"
    assert "content must not be echoed" not in json.dumps(result)


@pytest.mark.parametrize("change", ["missing", "failed", "incomplete", "wrong_method", "duplicate"])
def test_transfer_observation_must_resolve_to_successful_unambiguous_log_read(documents, change):
    report, evidence, raw = documents
    observation = next(item for item in raw["rpc_observations"] if item["observation_id"] == "rpc:4")
    if change == "missing":
        raw["rpc_observations"].remove(observation)
    elif change == "failed":
        observation["error"] = "read failed"
    elif change == "incomplete":
        observation.pop("result")
    elif change == "wrong_method":
        observation["method"] = "eth_call"
    else:
        raw["rpc_observations"].append(copy.deepcopy(observation))
    result = citation_validation(report, evidence, raw)
    assert result["status"] == "needs_review"
    assert codes(result) & {
        "missing_transfer_observation", "unsuccessful_transfer_observation", "duplicate_observation_id",
    }


@pytest.mark.parametrize("field,value", [
    ("transactionHash", "0x" + "f" * 64), ("blockHash", "0x" + "e" * 64),
    ("blockNumber", "0x1"), ("logIndex", "0xff"),
    ("data", "0x" + "0" * 63 + "1"), ("removed", True),
])
def test_transfer_payload_must_match_its_actual_raw_log(documents, field, value):
    report, evidence, raw = documents
    next(item for item in raw["rpc_observations"] if item["observation_id"] == "rpc:4")["result"][0][field] = value
    result = citation_validation(report, evidence, raw)
    assert "transfer_observation_mismatch" in codes(result)


@pytest.mark.parametrize("field,value", [
    ("address", "0x" + "1" * 40), ("fromBlock", "0x0"),
    ("toBlock", "0xfffffff"), ("topics", [TRANSFER_TOPIC, None, None]),
])
def test_raw_log_observation_filter_must_match_subject_token_and_window(documents, field, value):
    report, evidence, raw = documents
    next(item for item in raw["rpc_observations"] if item["observation_id"] == "rpc:4")["params"][0][field] = value
    result = citation_validation(report, evidence, raw)
    assert "transfer_observation_scope" in codes(result)


def test_accepted_raw_log_remains_valid_when_other_log_is_rejected(documents):
    report, evidence, raw = documents
    next(item for item in raw["rpc_observations"] if item["observation_id"] == "rpc:4")["result"].append({"malformed": True})
    evidence["coverage"]["status"] = "partial"
    evidence["coverage"]["queries"][0]["rejected_logs"] = 1
    assert citation_validation(report, evidence, raw)["status"] == "valid"


@pytest.mark.parametrize("where", ["projected_receipt", "projected_transaction", "raw_receipt", "raw_transaction"])
def test_confirmed_block_identity_must_agree_across_collector_projection_and_raw(documents, where):
    report, evidence, raw = documents
    if where.startswith("projected"):
        kind = "receipt" if where.endswith("receipt") else "transaction"
        next(item for item in report["agent"]["evidence"] if item["kind"] == kind)["payload"]["blockHash"] = "0x" + "f" * 64
    else:
        method = "eth_getTransactionReceipt" if where.endswith("receipt") else "eth_getTransactionByHash"
        next(item for item in raw["agent_rpc_observations"] if item["method"] == method)["result"]["blockHash"] = "0x" + "f" * 64
    result = citation_validation(report, evidence, raw)
    assert result["status"] == "needs_review"
    assert codes(result) & {"receipt_transfer_mismatch", "agent_rpc_observation_mismatch"}


@pytest.mark.parametrize("where", ["projected", "raw"])
def test_receipt_logs_must_contain_same_transfer_payload(documents, where):
    report, evidence, raw = documents
    receipt = (next(item for item in report["agent"]["evidence"] if item["kind"] == "receipt")["payload"]
               if where == "projected" else
               next(item for item in raw["agent_rpc_observations"] if item["method"] == "eth_getTransactionReceipt")["result"])
    next(log for log in receipt["logs"] if log.get("logIndex") == "0xb")["data"] = "0x" + "0" * 63 + "1"
    assert "receipt_log_mismatch" in codes(citation_validation(report, evidence, raw))


def test_receipt_cannot_point_to_other_transaction_with_no_collected_transfer(documents):
    report, evidence, raw = documents
    item = report["agent"]["evidence"][0]
    old = item["evidence_id"]
    new_tx = "0x" + "f" * 64
    item["evidence_id"] = "receipt:" + new_tx
    item["payload"]["transactionHash"] = new_tx
    for claim in report["conclusion"]["claims"]:
        claim["evidence_ids"] = [item["evidence_id"] if ref == old else ref for ref in claim["evidence_ids"]]
    observed = raw["agent_rpc_observations"][0]
    observed["params"] = [new_tx]
    observed["result"]["transactionHash"] = new_tx
    result = citation_validation(report, evidence, raw)
    assert "receipt_transfer_mismatch" in codes(result)


@pytest.mark.parametrize("raw_change", ["missing", "failed", "duplicate"])
def test_successful_receipt_must_have_one_successful_raw_rpc_observation(documents, raw_change):
    report, evidence, raw = documents
    item = raw["agent_rpc_observations"][0]
    if raw_change == "missing":
        raw["agent_rpc_observations"].remove(item)
    elif raw_change == "failed":
        item["error"] = "failed read"
    else:
        raw["agent_rpc_observations"].append(copy.deepcopy(item))
    assert codes(citation_validation(report, evidence, raw)) & {
        "missing_agent_rpc_observation", "agent_rpc_observation_mismatch",
    }


@pytest.mark.parametrize("field,value", [("blockNumber", None), ("blockNumber", "0x0"), ("blockHash", {}), ("transactionHash", None)])
def test_successful_receipt_identity_is_strict_and_bounded(documents, field, value):
    report, evidence, raw = documents
    report["agent"]["evidence"][0]["payload"][field] = value
    assert "malformed_receipt" in codes(citation_validation(report, evidence, raw))


@pytest.mark.parametrize("field,value", [
    ("chain_id", True), ("block_number", True), ("value_raw", 100), ("observation_ids", "rpc:4"),
    ("directions", ["out", "out", "out"]), ("directions", ["in"]),
    ("evidence_id", "x" * 241), ("value_raw", "9" * 1000),
])
def test_collector_transfer_types_are_strict(documents, field, value):
    report, evidence, raw = documents
    evidence["transfers"][0][field] = value
    assert "malformed_evidence" in codes(citation_validation(report, evidence, raw))


def test_duplicate_transfer_identity_is_flagged(documents):
    report, evidence, raw = documents
    evidence["transfers"].append(copy.deepcopy(evidence["transfers"][0]))
    assert "duplicate_transfer_id" in codes(citation_validation(report, evidence, raw))


def test_receipt_rpc_param_hash_can_use_equivalent_hex_case(documents):
    report, evidence, raw = documents
    raw["agent_rpc_observations"][0]["params"][0] = "0x" + raw["agent_rpc_observations"][0]["params"][0][2:].upper()
    assert citation_validation(report, evidence, raw)["status"] == "valid"


@pytest.mark.parametrize("where", ["receipt", "raw_log", "query"])
def test_oversized_rpc_quantity_is_rejected_before_integer_conversion(documents, where, monkeypatch):
    import cluetide.citation_validation as module
    report, evidence, raw = documents
    oversized = "0x" + "f" * 100_000
    parser = module.parse_quantity
    monkeypatch.setattr(module, "parse_quantity", lambda value: pytest.fail("Oversized integer conversion")
                        if value == oversized else parser(value))
    if where == "receipt":
        report["agent"]["evidence"][0]["payload"]["blockNumber"] = oversized
    else:
        observed = next(item for item in raw["rpc_observations"] if item["observation_id"] == "rpc:4")
        if where == "raw_log":
            observed["result"][0]["blockNumber"] = oversized
        else:
            observed["params"][0]["fromBlock"] = oversized
    assert citation_validation(report, evidence, raw)["status"] == "needs_review"


@pytest.mark.parametrize("report", [
    None, [], {"conclusion": "malformed"}, {"summary": {}, "claims": []},
    {"summary": "Legacy", "claims": "untrusted-claim-content"},
    {"summary": "Legacy", "claims": [None]},
    {"summary": "Legacy", "claims": [{"text": "Claim", "evidence_ids": "wrong"}]},
    {"summary": "Legacy", "claims": [{"text": "Claim", "evidence_ids": [], "interpretation": 1}]},
])
def test_malformed_generic_report_returns_safe_bounded_diagnostics(report):
    result = citation_validation(report, {"transfers": []})
    assert result["status"] == "needs_review"
    assert "malformed_report" in codes(result)
    assert "untrusted-claim-content" not in json.dumps(result)


@pytest.mark.parametrize("report", [
    {"schema_version": "cluetide-report/v1", "conclusion": None,
     "agent": {"status": "partial", "evidence": [], "report": None}},
    {"schema_version": "cluetide-report/v1", "conclusion": None,
     "agent": {"status": "stopped", "evidence": [], "report": None}},
    {"summary": "Empty legacy report", "claims": []}, {},
])
def test_legal_empty_or_partial_without_claims_is_not_applicable(report):
    assert citation_validation(report, {"transfers": []})["status"] == "not_applicable"


def empty_documents():
    _, evidence, raw = archived()
    evidence["transfers"], evidence["alerts"] = [], []
    coverage = evidence["coverage"]
    coverage["status"] = "empty"
    for query in coverage["queries"]:
        query.update(status="complete", accepted_logs=0, returned_logs=0, rejected_logs=0, error=None)
    for observed in raw["rpc_observations"]:
        if observed["method"] == "eth_getLogs":
            observed["result"] = []
    refs = [query["observation_id"] for query in coverage["queries"]]
    request = evidence["request"]
    supporting = {"evidence_id": "collection:empty-transfer-queries", "kind": "empty_transfer_queries", "status": "ok",
                  "payload": {"source_observation_ids": refs, "query_count": len(refs),
                              **{field: request[field] for field in ("address", "token_address", "from_block", "to_block")},
                              "matching_transfer_count": 0}}
    report = {"schema_version": "cluetide-report/v1", "conclusion": {
        "summary": "No matching transfers returned within this finite window.", "classification": "unresolved",
        "claims": [{"text": "Successful finite queries returned no matching logs.", "evidence_ids": [supporting["evidence_id"]], "interpretation": False}],
        "limitations": ["Provider returned data; no provider authenticity claim."]},
        "agent": {"status": "completed", "evidence": [supporting]}}
    return report, evidence, raw


def test_deterministic_empty_window_claim_checks_successful_empty_queries():
    result = citation_validation(*empty_documents())
    assert result["status"] == "valid", result
    assert result["checked_counts"]["empty_query_links"] == 2


@pytest.mark.parametrize("change", ["failed", "nonempty", "missing", "wrong_filter", "wrong_plan", "count", "bool_count", "duplicate_ref"])
def test_empty_window_proof_cannot_promote_failures_or_wrong_query_scope(change):
    report, evidence, raw = empty_documents()
    payload = report["agent"]["evidence"][0]["payload"]
    observed = next(item for item in raw["rpc_observations"] if item["observation_id"] == "rpc:4")
    if change == "failed":
        observed["error"] = "failed read"
    elif change == "nonempty":
        observed["result"] = [{"malformed": True}]
    elif change == "missing":
        raw["rpc_observations"].remove(observed)
    elif change == "wrong_filter":
        observed["params"][0]["topics"] = [TRANSFER_TOPIC, None, address_topic(evidence["request"]["address"])]
    elif change == "wrong_plan":
        evidence["coverage"]["queries"][0]["direction"] = "incoming"
    elif change == "count":
        payload["query_count"] = 1
    elif change == "bool_count":
        payload["matching_transfer_count"] = False
    else:
        payload["source_observation_ids"][1] = payload["source_observation_ids"][0]
    result = citation_validation(report, evidence, raw)
    assert result["status"] == "needs_review"
    assert codes(result) & {"empty_query_observation_mismatch", "empty_query_evidence_mismatch"}


@pytest.mark.parametrize("section", ["claims", "claim_refs", "agent", "observations", "logs", "transfers"])
def test_oversized_sections_are_not_silently_truncated_or_copied(documents, section):
    report, evidence, raw = documents
    if section == "claims":
        report["conclusion"]["claims"] = [report["conclusion"]["claims"][0]] * (MAX_CLAIMS + 1)
    elif section == "claim_refs":
        # Generic form reaches the per-reference bound before standard validation.
        report.pop("schema_version")
        report["conclusion"]["claims"][0]["evidence_ids"] *= MAX_CLAIM_REFERENCES + 1
    elif section == "agent":
        report["agent"]["evidence"] = [report["agent"]["evidence"][0]] * (MAX_AGENT_EVIDENCE + 1)
    elif section == "observations":
        raw["rpc_observations"] = [raw["rpc_observations"][0]] * (MAX_OBSERVATIONS + 1)
    elif section == "logs":
        observation = next(item for item in raw["rpc_observations"] if item["observation_id"] == "rpc:4")
        observation["result"] *= MAX_LOGS_PER_OBSERVATION + 1
    else:
        evidence["transfers"] *= MAX_TRANSFERS + 1
    result = citation_validation(report, evidence, raw)
    assert result["status"] == "needs_review"
    assert "check_limit_exceeded" in codes(result)
    assert len(result["issues"]) <= 64


def test_issue_output_has_fixed_size_and_does_not_include_embedded_payloads():
    report = {"summary": "Report", "claims": [
        {"text": "Claim", "evidence_ids": [f"untrusted-ref-{index}-{ref}" for ref in range(8)]}
        for index in range(12)
    ], "agent": {"evidence": []}}
    result = citation_validation(report, {"transfers": []})
    assert result["issue_count"] == 96
    assert len(result["issues"]) == 64
    assert result["issues_truncated"] is True
    assert "untrusted-ref-" not in json.dumps(result)
