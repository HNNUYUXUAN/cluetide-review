"""Bounded, read-only checks of report-to-evidence and RPC observation links.

This is a structural consistency check of supplied bytes. It does not establish
provider authenticity, factual truth, report completeness, or reviewer
independence. Nothing in the report, evidence, raw data, or manifest is changed.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from .agent import Conclusion, ExplanationAssessment, validate_assessment_bindings
from .evidence import (
    TRANSFER_TOPIC, address_topic, decode_transfer_log, transfer_identity,
    transfer_payload,
)
from .rpc import parse_quantity
from .schemas import InvestigationRequest, TransferRecord, normalize_hash


MAX_CLAIMS = 12
MAX_CLAIM_REFERENCES = 8
MAX_ASSESSMENTS = 12
MAX_ASSESSMENT_REFERENCES = 8
MAX_ASSESSMENT_NOTES = 8
MAX_AGENT_EVIDENCE = 32
MAX_OBSERVATIONS = 128
MAX_TRANSFERS = 10_000
MAX_LOGS_PER_OBSERVATION = 5_000
MAX_TRANSFER_OBSERVATIONS = 40
MAX_ISSUES = 64
SCOPE = (
    "Bounded structural checks of claim and directed candidate-explanation references "
    "to successful Agent evidence, explanation-status bindings, "
    "Transfer links to supplied RPC observations, and receipt/transaction identity "
    "consistency. No factual verification, source authenticity, completeness, or "
    "reviewer independence is established."
)
_MESSAGES = {
    "malformed_report": "Report or conclusion has an unsupported typed structure.",
    "malformed_agent_evidence": "Agent evidence has an unsupported typed structure.",
    "duplicate_evidence_id": "Repeated Agent evidence IDs make references ambiguous.",
    "missing_claim_evidence": "A claim reference has no matching Agent evidence.",
    "unsuccessful_claim_evidence": "A claim reference points to empty or failed Agent evidence.",
    "malformed_assessment": "A candidate explanation has an unsupported typed structure.",
    "assessment_status_requirement": "A candidate explanation needs evidence or a concrete gap for its stated status.",
    "missing_assessment_evidence": "A candidate-explanation reference has no matching Agent evidence.",
    "unsuccessful_assessment_evidence": "A candidate-explanation reference points to empty or failed Agent evidence.",
    "ambiguous_assessment_evidence": "A candidate-explanation reference points to repeated Agent evidence IDs.",
    "duplicate_assessment_reference": "A candidate explanation repeats a reference within one evidence direction.",
    "duplicate_assessment_id": "Repeated candidate-explanation IDs make the assessments ambiguous.",
    "assessment_binding_mismatch": "A candidate-explanation status disagrees with its bounded evidence bindings.",
    "malformed_evidence": "Collector evidence has an unsupported typed structure.",
    "malformed_observations": "RPC observations have an unsupported typed structure.",
    "duplicate_observation_id": "Repeated RPC observation IDs make Transfer links ambiguous.",
    "duplicate_transfer_id": "Repeated Transfer identities make observation links ambiguous.",
    "missing_transfer_observation": "A Transfer has no matching successful log observation.",
    "unsuccessful_transfer_observation": "A Transfer points to a failed or incomplete log observation.",
    "transfer_observation_scope": "A Transfer log observation does not match the requested filter and window.",
    "transfer_observation_mismatch": "A Transfer does not match its supplied raw log.",
    "malformed_receipt": "Successful receipt or transaction evidence lacks a bounded confirmed identity.",
    "receipt_transfer_mismatch": "Receipt or transaction identity disagrees with its collected Transfer.",
    "receipt_log_mismatch": "A collected Transfer is absent or inconsistent in its supplied receipt logs.",
    "missing_agent_rpc_observation": "Successful receipt or transaction evidence lacks a matching raw RPC observation.",
    "agent_rpc_observation_mismatch": "Projected receipt or transaction identity disagrees with the supplied raw RPC result.",
    "empty_query_evidence_mismatch": "Empty-window evidence disagrees with the request or empty query coverage.",
    "empty_query_observation_mismatch": "Empty-window evidence lacks matching successful empty log observations.",
    "check_limit_exceeded": "Supplied content exceeds a structural check limit; this check is incomplete.",
}


def _mapping(value: Any) -> Mapping[str, Any] | None:
    # Pydantic fields can be read directly without copying large raw payloads.
    if isinstance(value, BaseModel):
        return value.__dict__
    return value if isinstance(value, Mapping) else None


def _identifier(value: Any, maximum: int = 240) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum


def _quantity(value: Any) -> int:
    # Reject oversized quantities before regex/int conversion. A JSON-safe RPC
    # quantity has at most 14 hexadecimal digits plus its 0x prefix.
    if not isinstance(value, str) or len(value) > 16:
        raise ValueError
    return parse_quantity(value)


def _decoded_log(log: Any, request: InvestigationRequest, **kwargs: Any) -> TransferRecord:
    if not isinstance(log, dict):
        raise ValueError
    topics = log.get("topics")
    if (not isinstance(topics, list) or len(topics) != 3
            or not isinstance(topics[0], str) or len(topics[0]) != 66):
        raise ValueError
    for name in ("blockNumber", "logIndex"):
        _quantity(log.get(name))
    if log.get("transactionIndex") is not None:
        _quantity(log["transactionIndex"])
    return decode_transfer_log(log, request, **kwargs)


@dataclass
class _Checks:
    issues: list[dict[str, str]] = field(default_factory=list)
    issue_count: int = 0
    counts: dict[str, int] = field(default_factory=lambda: {
        "claim_references": 0, "transfers": 0, "transfer_observation_links": 0,
        "receipt_transfer_links": 0, "agent_rpc_links": 0, "empty_query_links": 0,
    })

    def add(self, code: str, location: str) -> None:
        self.issue_count += 1
        if len(self.issues) < MAX_ISSUES:
            # Neither payloads nor supplied IDs are interpolated into diagnostics.
            self.issues.append({"code": code, "message": _MESSAGES[code], "location": location})

    def array(self, value: Any, limit: int, location: str, malformed: str) -> list | None:
        if not isinstance(value, list):
            self.add(malformed, location)
            return None
        if len(value) > limit:
            self.add("check_limit_exceeded", location)
            return None
        return value

    def result(self) -> dict[str, Any]:
        return {
            "schema_version": "cluetide-citation-validation/v1",
            "status": ("needs_review" if self.issue_count else
                       "valid" if any(self.counts.values()) else "not_applicable"),
            "issues": self.issues, "issue_count": self.issue_count,
            "issues_truncated": self.issue_count > len(self.issues),
            "checked_counts": self.counts, "report_modified": False,
            "factual_verification": False, "source_authenticity_verified": False,
            "scope": SCOPE,
        }


def _agent_index(report: Mapping[str, Any], checks: _Checks) -> dict[str, Mapping[str, Any] | None]:
    agent = _mapping(report.get("agent"))
    if agent is None:
        if report.get("agent") is not None:
            checks.add("malformed_agent_evidence", "agent")
        return {}
    values = checks.array(agent.get("evidence", []), MAX_AGENT_EVIDENCE,
                          "agent.evidence", "malformed_agent_evidence")
    index: dict[str, Mapping[str, Any] | None] = {}
    for number, value in enumerate(values or []):
        item = _mapping(value)
        location = f"agent.evidence[{number}]"
        if (item is None or not _identifier(item.get("evidence_id"))
                or not _identifier(item.get("kind"), 80)
                or item.get("status") not in ("ok", "empty", "error")
                or "payload" not in item):
            checks.add("malformed_agent_evidence", location)
            continue
        identity = item["evidence_id"]
        if identity in index:
            index[identity] = None
            checks.add("duplicate_evidence_id", location)
        else:
            index[identity] = item
    return index


def _claims(report: Mapping[str, Any], index: dict, checks: _Checks) -> None:
    if "conclusion" in report:
        value, location = report["conclusion"], "conclusion"
    elif "report" in report:
        value, location = report["report"], "report"
    elif "summary" in report or "claims" in report or "assessments" in report:
        value, location = report, "report"
    else:
        return
    if value is None:
        return  # A bounded run can finish without a conclusion.
    conclusion = _mapping(value)
    if (conclusion is None or not isinstance(conclusion.get("summary"), str)
            or len(conclusion["summary"]) > 1800):
        checks.add("malformed_report", location)
        return
    claims = checks.array(conclusion.get("claims"), MAX_CLAIMS,
                          location + ".claims", "malformed_report")
    if claims is None:
        return
    assessment_structure_valid = _assessments(conclusion, index, checks, location)
    if report.get("schema_version") == "cluetide-report/v1":
        if not assessment_structure_valid:
            return
        try:
            Conclusion.model_validate(value, strict=True)
        except (ValidationError, ValueError, TypeError):
            checks.add("malformed_report", location)
            return
    for number, value in enumerate(claims):
        claim = _mapping(value)
        claim_location = f"{location}.claims[{number}]"
        if (claim is None or not isinstance(claim.get("text"), str)
                or not 0 < len(claim["text"]) <= 1400
                or ("interpretation" in claim and type(claim["interpretation"]) is not bool)):
            checks.add("malformed_report", claim_location)
            continue
        refs = checks.array(claim.get("evidence_ids"), MAX_CLAIM_REFERENCES,
                            claim_location + ".evidence_ids", "malformed_report")
        if refs is None:
            continue
        if not refs or any(not _identifier(ref) for ref in refs):
            checks.add("malformed_report", claim_location + ".evidence_ids")
            continue
        for ref_number, ref in enumerate(refs):
            checks.counts["claim_references"] += 1
            ref_location = f"{claim_location}.evidence_ids[{ref_number}]"
            if ref not in index:
                checks.add("missing_claim_evidence", ref_location)
            elif index[ref] is not None and index[ref]["status"] != "ok":
                checks.add("unsuccessful_claim_evidence", ref_location)


def _assessments(conclusion: Mapping[str, Any], index: dict, checks: _Checks,
                 location: str) -> bool:
    """Check bounded support/counter links and the runtime's status invariants."""
    values = checks.array(conclusion.get("assessments", []), MAX_ASSESSMENTS,
                          location + ".assessments", "malformed_assessment")
    if values is None:
        return False
    if not values:
        return True
    checks.counts.update(assessments=0, assessment_references=0,
                         assessment_support_references=0, assessment_counter_references=0)
    structure_valid, bindings_valid = True, True
    typed_assessments: list[ExplanationAssessment] = []
    explanation_ids: set[str] = set()
    for number, value in enumerate(values):
        item = _mapping(value)
        item_location = f"{location}.assessments[{number}]"
        checks.counts["assessments"] += 1
        if (item is None or not _identifier(item.get("explanation_id"), 64)
                or not isinstance(item.get("explanation"), str)
                or not 0 < len(item["explanation"]) <= 1400
                or not item["explanation"].strip()
                or item.get("status") not in ("supported", "refuted", "unknown")):
            checks.add("malformed_assessment", item_location)
            structure_valid = False
            continue
        item_valid = True
        directions: dict[str, list] = {}
        for direction, field_name in (("support", "support_evidence_ids"),
                                      ("counter", "counter_evidence_ids")):
            field_location = item_location + "." + field_name
            refs = checks.array(item.get(field_name, []), MAX_ASSESSMENT_REFERENCES,
                                field_location, "malformed_assessment")
            if refs is None:
                item_valid = False
                continue
            directions[direction] = refs
            if any(not _identifier(ref) for ref in refs):
                checks.add("malformed_assessment", field_location)
                item_valid = False
                continue
            if len(set(refs)) != len(refs):
                checks.add("duplicate_assessment_reference", field_location)
                item_valid = False
            for ref_number, ref in enumerate(refs):
                checks.counts["assessment_references"] += 1
                checks.counts[f"assessment_{direction}_references"] += 1
                ref_location = f"{field_location}[{ref_number}]"
                if ref not in index:
                    checks.add("missing_assessment_evidence", ref_location)
                    bindings_valid = False
                elif index[ref] is None:
                    checks.add("ambiguous_assessment_evidence", ref_location)
                    bindings_valid = False
                elif index[ref]["status"] != "ok":
                    checks.add("unsuccessful_assessment_evidence", ref_location)
                    bindings_valid = False
        notes: dict[str, list] = {}
        for field_name in ("unknowns", "checks"):
            field_location = item_location + "." + field_name
            entries = checks.array(item.get(field_name, []), MAX_ASSESSMENT_NOTES,
                                   field_location, "malformed_assessment")
            if entries is None:
                item_valid = False
                continue
            notes[field_name] = entries
            if (any(not isinstance(note, str) or not 0 < len(note) <= 600 for note in entries)
                    or field_name == "checks" and (not entries or any(not note.strip() for note in entries))):
                checks.add("malformed_assessment", field_location)
                item_valid = False
        required_field = {"supported": "support_evidence_ids", "refuted": "counter_evidence_ids",
                          "unknown": "unknowns"}[item["status"]]
        required_values = (notes.get("unknowns") if item["status"] == "unknown"
                           else directions.get("support" if item["status"] == "supported" else "counter"))
        if required_values is not None and (not required_values or item["status"] == "unknown"
                and any(not isinstance(note, str) or not note.strip() for note in required_values)):
            checks.add("assessment_status_requirement", item_location + "." + required_field)
            item_valid = False
        if not item_valid:
            structure_valid = False
            continue
        try:
            assessment = ExplanationAssessment.model_validate(value, strict=True)
        except (ValidationError, ValueError, TypeError):
            checks.add("malformed_assessment", item_location)
            structure_valid = False
            continue
        if assessment.explanation_id in explanation_ids:
            checks.add("duplicate_assessment_id", item_location + ".explanation_id")
            bindings_valid = False
        explanation_ids.add(assessment.explanation_id)
        typed_assessments.append(assessment)
    if structure_valid and bindings_valid:
        comparison_refs = {
            ref for assessment in typed_assessments
            if assessment.explanation_id == "supply_decrease" and assessment.status != "unknown"
            for ref in (assessment.support_evidence_ids if assessment.status == "supported"
                        else assessment.counter_evidence_ids)
        }
        comparison_bounded = True
        for ref in comparison_refs:
            evidence = index[ref]
            payload = _mapping(evidence["payload"])
            raw = payload.get("total_supply_raw", payload.get("totalSupply")) if payload is not None else None
            if evidence["kind"] == "token_state" and isinstance(raw, str) and len(raw) > 78:
                checks.add("check_limit_exceeded", location + ".assessments")
                comparison_bounded = False
        if not comparison_bounded:
            return structure_valid
        bounded_conclusion = Conclusion.model_construct(
            summary=conclusion["summary"], classification=conclusion.get("classification", "unresolved"),
            claims=[], limitations=[], assessments=typed_assessments,
        )
        try:
            validate_assessment_bindings(bounded_conclusion, [item for item in index.values() if item is not None],
                                         require_assessments=False)
        except (ValidationError, ValueError, TypeError, KeyError):
            checks.add("assessment_binding_mismatch", location + ".assessments")
    return structure_valid


def _observation_index(raw: Mapping[str, Any], checks: _Checks) -> dict[str, Mapping[str, Any] | None]:
    values = checks.array(raw.get("rpc_observations", []), MAX_OBSERVATIONS,
                          "raw.rpc_observations", "malformed_observations")
    index: dict[str, Mapping[str, Any] | None] = {}
    for number, value in enumerate(values or []):
        item = _mapping(value)
        location = f"raw.rpc_observations[{number}]"
        if item is None or not _identifier(item.get("observation_id")):
            checks.add("malformed_observations", location)
            continue
        identity = item["observation_id"]
        if identity in index:
            index[identity] = None
            checks.add("duplicate_observation_id", location)
        else:
            index[identity] = item
    return index


def _log_index(observation: Mapping[str, Any], request: InvestigationRequest,
               checks: _Checks, location: str) -> dict[tuple, tuple] | None:
    if (observation.get("error") is not None or observation.get("method") != "eth_getLogs"
            or "result" not in observation):
        checks.add("unsuccessful_transfer_observation", location)
        return None
    params = observation.get("params")
    try:
        if not isinstance(params, list) or len(params) != 1 or not isinstance(params[0], dict):
            raise ValueError
        query = params[0]
        subject = address_topic(request.address)
        topics = query.get("topics")
        if (not isinstance(query.get("address"), str)
                or len(query["address"]) != 42
                or query["address"].lower() != request.token_address
                or not isinstance(topics, list) or len(topics) != 3
                or topics[0] != TRANSFER_TOPIC):
            raise ValueError
        if topics[1:] == [subject, None]:
            direction = "outgoing"
        elif topics[1:] == [None, subject]:
            direction = "incoming"
        else:
            raise ValueError
        start, end = _quantity(query.get("fromBlock")), _quantity(query.get("toBlock"))
        if not request.from_block <= start <= end <= request.to_block:
            raise ValueError
    except (ValueError, TypeError, KeyError):
        checks.add("transfer_observation_scope", location)
        return None
    logs = checks.array(observation["result"], MAX_LOGS_PER_OBSERVATION,
                        location + ".result", "malformed_observations")
    if logs is None:
        return None
    index: dict[tuple, tuple] = {}
    ambiguous: set[tuple] = set()
    for value in logs:
        try:
            record = _decoded_log(value, request, direction=direction,
                                  observation_id=observation["observation_id"],
                                  query_from=start, query_to=end)
        except (ValueError, TypeError, KeyError):
            # Unaccepted logs can occur in honest partial coverage. They cannot
            # support a Transfer link, but do not invalidate other accepted logs.
            continue
        identity, payload = transfer_identity(record), transfer_payload(record)
        if identity in index and index[identity] != payload:
            ambiguous.add(identity)
        index[identity] = payload
    for identity in ambiguous:
        del index[identity]
    return index


def _transfers(evidence: Mapping[str, Any], raw: Mapping[str, Any], checks: _Checks
               ) -> tuple[InvestigationRequest | None, list[TransferRecord]]:
    values = checks.array(evidence.get("transfers", []), MAX_TRANSFERS,
                          "evidence.transfers", "malformed_evidence")
    if not values:
        return None, []
    try:
        request = InvestigationRequest.model_validate(evidence.get("request"), strict=True)
    except (ValidationError, ValueError, TypeError):
        checks.add("malformed_evidence", "evidence.request")
        return None, []
    observations = _observation_index(raw, checks)
    decoded_indexes: dict[str, dict[tuple, tuple] | None] = {}
    records: list[TransferRecord] = []
    identities: set[tuple] = set()
    evidence_ids: set[str] = set()
    for number, value in enumerate(values):
        location = f"evidence.transfers[{number}]"
        item = _mapping(value)
        ids = checks.array(item.get("observation_ids") if item is not None else None,
                           MAX_TRANSFER_OBSERVATIONS, location + ".observation_ids", "malformed_evidence")
        if ids is None:
            continue
        try:
            if (item is None or type(item.get("chain_id", 1)) is not int
                    or not _identifier(item.get("evidence_id"))
                    or not isinstance(item.get("directions"), list) or len(item["directions"]) > 2
                    or not isinstance(item.get("value_raw"), str) or len(item["value_raw"]) > 78):
                raise ValueError
            record = TransferRecord.model_validate(value, strict=True)
            if (record.token_address != request.token_address
                    or not request.from_block <= record.block_number <= request.to_block
                    or record.from_address != request.address and record.to_address != request.address
                    or record.directions != (["in"] if record.to_address == request.address else [])
                    + (["out"] if record.from_address == request.address else [])):
                raise ValueError
        except (ValidationError, ValueError, TypeError):
            checks.add("malformed_evidence", location)
            continue
        checks.counts["transfers"] += 1
        identity = transfer_identity(record)
        if identity in identities or record.evidence_id in evidence_ids:
            checks.add("duplicate_transfer_id", location)
        identities.add(identity)
        evidence_ids.add(record.evidence_id)
        records.append(record)
        if not ids:
            checks.add("missing_transfer_observation", location + ".observation_ids")
        for ref_number, ref in enumerate(ids):
            checks.counts["transfer_observation_links"] += 1
            ref_location = f"{location}.observation_ids[{ref_number}]"
            if not _identifier(ref) or ref not in observations:
                checks.add("missing_transfer_observation", ref_location)
                continue
            if observations[ref] is None:
                continue  # Already flagged as ambiguous.
            if ref not in decoded_indexes:
                decoded_indexes[ref] = _log_index(observations[ref], request, checks, ref_location)
            log_index = decoded_indexes[ref]
            if log_index is not None and log_index.get(identity) != transfer_payload(record):
                checks.add("transfer_observation_mismatch", ref_location)
    return request, records


def _confirmed_identity(payload: Any, *, receipt: bool) -> tuple[str, str, int]:
    item = _mapping(payload)
    if item is None:
        raise ValueError
    return (normalize_hash(item.get("transactionHash" if receipt else "hash")),
            normalize_hash(item.get("blockHash")), _quantity(item.get("blockNumber")))


def _receipt_logs(payload: Mapping[str, Any], request: InvestigationRequest,
                  records: list[TransferRecord], checks: _Checks, location: str) -> None:
    logs = checks.array(payload.get("logs"), MAX_LOGS_PER_OBSERVATION,
                        location + ".logs", "malformed_receipt")
    if logs is None:
        return
    expected = {transfer_identity(record): transfer_payload(record) for record in records}
    observed: dict[tuple, tuple] = {}
    conflicts: set[tuple] = set()
    for log in logs:
        # A receipt can contain other contracts/topics and requested-token logs
        # unrelated to this subject. Only matching Transfer links are compared.
        try:
            record = _decoded_log(log, request, direction="outgoing", observation_id="receipt")
        except (ValueError, TypeError, KeyError):
            try:
                record = _decoded_log(log, request, direction="incoming", observation_id="receipt")
            except (ValueError, TypeError, KeyError):
                continue
        identity, data = transfer_identity(record), transfer_payload(record)
        if identity in observed and observed[identity] != data:
            conflicts.add(identity)
        observed[identity] = data
    for identity, data in expected.items():
        if identity in conflicts or observed.get(identity) != data:
            checks.add("receipt_log_mismatch", location + ".logs")


def _receipts(index: dict[str, Mapping[str, Any] | None], raw: Mapping[str, Any],
              request: InvestigationRequest | None, records: list[TransferRecord],
              evidence: Mapping[str, Any], checks: _Checks) -> None:
    selected = [(number, item) for number, item in enumerate(index.values())
                if item is not None and item["status"] == "ok"
                and item["kind"] in ("receipt", "transaction")]
    if not selected:
        return
    if request is None:
        try:
            request = InvestigationRequest.model_validate(evidence.get("request"), strict=True)
        except (ValidationError, ValueError, TypeError):
            checks.add("malformed_evidence", "evidence.request")
            return
    observations = checks.array(raw.get("agent_rpc_observations", []), MAX_OBSERVATIONS,
                                "raw.agent_rpc_observations", "malformed_observations")
    for number, item in selected:
        receipt = item["kind"] == "receipt"
        location = f"agent.evidence[{number}].payload"
        try:
            identity = _confirmed_identity(item["payload"], receipt=receipt)
            if not request.from_block <= identity[2] <= request.to_block:
                raise ValueError
            expected_id = ("receipt:" if receipt else "transaction:") + identity[0]
            if item["evidence_id"].startswith(("receipt:", "transaction:")) and item["evidence_id"].lower() != expected_id:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            checks.add("malformed_receipt", location)
            continue
        matching = [record for record in records if record.transaction_hash == identity[0]]
        if records and not matching:
            checks.add("receipt_transfer_mismatch", location)
        for record in matching:
            checks.counts["receipt_transfer_links"] += 1
            if (record.block_hash, record.block_number) != identity[1:]:
                checks.add("receipt_transfer_mismatch", location)
        if receipt and matching:
            _receipt_logs(_mapping(item["payload"]), request, matching, checks, location)
        method = "eth_getTransactionReceipt" if receipt else "eth_getTransactionByHash"
        raw_matches = []
        for observation in observations or []:
            observed = _mapping(observation)
            params = observed.get("params") if observed is not None else None
            if (observed is not None and observed.get("method") == method
                    and observed.get("error") is None
                    and isinstance(params, list) and len(params) == 1
                    and isinstance(params[0], str) and len(params[0]) == 66
                    and params[0].lower() == identity[0] and "result" in observed):
                raw_matches.append(observed)
        checks.counts["agent_rpc_links"] += 1
        if not raw_matches:
            checks.add("missing_agent_rpc_observation", location)
            continue
        if len(raw_matches) != 1:
            checks.add("agent_rpc_observation_mismatch", location)
            continue
        try:
            if _confirmed_identity(raw_matches[0]["result"], receipt=receipt) != identity:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            checks.add("agent_rpc_observation_mismatch", location)
            continue
        if receipt and matching:
            _receipt_logs(_mapping(raw_matches[0]["result"]), request, matching, checks,
                          "raw.agent_rpc_observations.result")


def _empty_queries(index: dict[str, Mapping[str, Any] | None], evidence: Mapping[str, Any],
                   raw: Mapping[str, Any], checks: _Checks) -> None:
    selected = [item for item in index.values() if item is not None
                and item["status"] == "ok" and item["kind"] == "empty_transfer_queries"]
    if not selected:
        return
    try:
        request = InvestigationRequest.model_validate(evidence.get("request"), strict=True)
    except (ValidationError, ValueError, TypeError):
        checks.add("malformed_evidence", "evidence.request")
        return
    observations = _observation_index(raw, checks)
    coverage = _mapping(evidence.get("coverage"))
    for item in selected:
        location = "agent.evidence.empty_transfer_queries.payload"
        payload = _mapping(item["payload"])
        refs = checks.array(payload.get("source_observation_ids") if payload else None,
                            MAX_TRANSFER_OBSERVATIONS, location + ".source_observation_ids",
                            "empty_query_evidence_mismatch")
        if refs is None:
            continue
        queries = coverage.get("queries") if coverage is not None else None
        typed_refs = refs and all(_identifier(ref) for ref in refs) and len(set(refs)) == len(refs)
        try:
            if (payload is None or not typed_refs or coverage is None
                    or coverage.get("status") != "empty" or evidence.get("transfers", []) != []
                    or type(payload.get("matching_transfer_count")) is not int
                    or payload["matching_transfer_count"] != 0
                    or type(payload.get("query_count")) is not int
                    or payload["query_count"] != len(refs)
                    or payload.get("address") != request.address
                    or payload.get("token_address") != request.token_address
                    or type(payload.get("from_block")) is not int
                    or type(payload.get("to_block")) is not int
                    or (payload["from_block"], payload["to_block"]) != (request.from_block, request.to_block)
                    or type(coverage.get("planned_queries")) is not int
                    or type(coverage.get("completed_queries")) is not int
                    or coverage["planned_queries"] != len(refs) or coverage["completed_queries"] != len(refs)
                    or not isinstance(queries, list) or len(queries) != len(refs)):
                raise ValueError
            coverage_refs = []
            coverage_plan = set()
            reference_plan = {}
            for query in queries:
                query = _mapping(query)
                if (query is None or not _identifier(query.get("observation_id"))
                        or query.get("status") != "complete"
                        or query.get("error") is not None
                        or query.get("direction") not in ("incoming", "outgoing")
                        or type(query.get("from_block")) is not int
                        or type(query.get("to_block")) is not int
                        or any(type(query.get(name)) is not int or query[name] != 0
                               for name in ("returned_logs", "accepted_logs", "rejected_logs"))):
                    raise ValueError
                coverage_refs.append(query["observation_id"])
                plan = (query["direction"], query["from_block"], query["to_block"])
                coverage_plan.add(plan)
                reference_plan[query["observation_id"]] = plan
            expected_plan = {(direction, start, min(start + request.log_chunk_size - 1, request.to_block))
                             for start in range(request.from_block, request.to_block + 1, request.log_chunk_size)
                             for direction in ("incoming", "outgoing")}
            if (set(coverage_refs) != set(refs) or coverage_plan != expected_plan
                    or len(coverage_plan) != len(refs)):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            checks.add("empty_query_evidence_mismatch", location)
            continue
        for number, ref in enumerate(refs):
            checks.counts["empty_query_links"] += 1
            ref_location = f"{location}.source_observation_ids[{number}]"
            observation = observations.get(ref)
            if (observation is None or observation.get("result") != []
                    or observation.get("error") is not None
                    or _log_index(observation, request, checks, ref_location) != {}):
                checks.add("empty_query_observation_mismatch", ref_location)
                continue
            query = observation["params"][0]
            direction, start, end = reference_plan[ref]
            expected_topics = ([TRANSFER_TOPIC, address_topic(request.address), None] if direction == "outgoing"
                               else [TRANSFER_TOPIC, None, address_topic(request.address)])
            if query["topics"] != expected_topics or query["fromBlock"] != hex(start) or query["toBlock"] != hex(end):
                checks.add("empty_query_observation_mismatch", ref_location)


def citation_validation(report: Any, evidence: Any, raw: Any = None) -> dict[str, Any]:
    """Compute safe structural diagnostics from supplied public documents.

    ``raw`` can be supplied separately after a bundle import. Otherwise the
    evidence's ``raw`` field is read. Malformed/oversized content produces
    ``needs_review`` with bounded diagnostics instead of leaking supplied values
    or silently treating omitted checks as successful. Documents with no
    applicable checks are ``not_applicable``.
    """
    checks = _Checks()
    report_document, evidence_document = _mapping(report), _mapping(evidence)
    if report_document is None:
        checks.add("malformed_report", "report")
        report_document = {}
    if evidence_document is None:
        checks.add("malformed_evidence", "evidence")
        evidence_document = {}
    raw_document = _mapping(raw if raw is not None else evidence_document.get("raw", {}))
    if raw_document is None:
        checks.add("malformed_observations", "raw")
        raw_document = {}
    index = _agent_index(report_document, checks)
    _claims(report_document, index, checks)
    request, records = _transfers(evidence_document, raw_document, checks)
    _receipts(index, raw_document, request, records, evidence_document, checks)
    _empty_queries(index, evidence_document, raw_document, checks)
    return checks.result()


assess_citation_validation = citation_validation
