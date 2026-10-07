from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from pydantic_ai import models
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from cluetide.agent import (
    AgentRequest, Conclusion, ExplanationAssessment, InvestigationConclusion, RuntimeLimits,
    ToolEvidence, build_offline_model, create_gateway_model, run_investigation,
    validate_assessment_bindings,
)
from cluetide.budget import BudgetLedger, PriceQuote
from cluetide.schemas import MAX_UINT256

TOKEN = "0x" + "ab" * 20
OTHER_TOKEN = "0x" + "cd" * 20
TX = "0x" + "12" * 32


@pytest.fixture(autouse=True)
def only_offline_or_mocked_models():
    with models.override_allow_model_requests(False):
        yield


def observed(identifier, kind="receipt", status="ok", payload=None):
    return {"evidence_id": identifier, "kind": kind, "status": status,
            "payload": payload if payload is not None else {"selected_transaction": TX}}


def state(block, raw="1000000000000000000000000000", token=TOKEN):
    return observed(f"state:{token}:{block}", "token_state", payload={
        "token_address": token, "block_number": block, "total_supply_raw": raw})


def scope(initial=None, sources=None):
    return AgentRequest(tx_hash=TX, token_address=TOKEN, from_block=100, to_block=110,
                        finalized_block=120, initial_observations=initial or [],
                        governance_sources={"proposal": "https://vote.uniswapfoundation.org/proposals/93"}
                        if sources is None else sources)


def assessment(identifier="event_cause", status="unknown", support=None, counter=None, unknowns=None):
    return {"explanation_id": identifier, "explanation": "在所选证据范围内核对候选事件解释。",
            "status": status, "support_evidence_ids": support or [], "counter_evidence_ids": counter or [],
            "unknowns": ["完整因果与来源真实性仍需复核。"] if unknowns is None else unknowns,
            "checks": ["核对当前已返回的观察及其明确缺口。"]}


def report(assessments=None, classification="unresolved"):
    return {"summary": "本次结论仅针对选定交易和有限观察。", "classification": classification,
            "claims": [{"text": "回执材料提供选定交易的公开观察。", "evidence_ids": ["receipt:1"]}],
            "limitations": ["来源真实性和因果语义仍需人工复核。"],
            "assessments": [assessment()] if assessments is None else assessments}


class Backend:
    def __init__(self, context_kind="governance_source"):
        self.calls = []
        self.context_kind = context_kind

    async def get_receipt(self, tx_hash):
        self.calls.append("get_receipt")
        return observed("receipt:1", payload={"governance_hint": True, "recipient": "0x000000000000000000000000000000000000dead"})

    async def get_transaction(self, tx_hash):
        self.calls.append("get_transaction")
        return observed("transaction:1", "transaction")

    async def get_governance_source(self, source_id):
        self.calls.append("get_governance_source")
        return observed("source:1", self.context_kind, payload={
            "category": "incident_postmortem" if self.context_kind == "public_context_source" else "governance_proposal"})

    async def get_token_state(self, token_address, block_number):
        self.calls.append("get_token_state")
        return state(block_number, token=token_address)


def test_stored_legacy_conclusion_remains_readable_and_bytes_are_preserved():
    path = Path(__file__).resolve().parents[1] / "tests/fixtures/legacy-conclusion.json"
    original = path.read_bytes()
    legacy = json.loads(original)
    parsed = Conclusion.model_validate(legacy, strict=True)
    assert parsed.assessments == []
    validate_assessment_bindings(parsed, [], require_assessments=False)
    with pytest.raises(ValidationError):
        InvestigationConclusion.model_validate(legacy, strict=True)
    assert path.read_bytes() == original
    source = json.loads((path.parents[2] / "source-manifest.json").read_text(encoding="utf-8"))
    expected = next(item["sha256"] for item in source["files"] if item["path"] == "tests/fixtures/legacy-conclusion.json")
    assert hashlib.sha256(original).hexdigest() == expected


@pytest.mark.parametrize("value", [
    assessment(status="supported", unknowns=[]),
    assessment(status="refuted", unknowns=[]),
    assessment(status="unknown", unknowns=[]),
    {**assessment(), "checks": []},
    assessment(status="supported", support=["receipt:1", "receipt:1"]),
    {**assessment(), "explanation": " \t\n"},
    {**assessment(), "checks": [" \t\n"]},
    assessment(unknowns=[" \t\n"]),
    assessment(status="supported", support=["receipt:1"], unknowns=[" \t\n"]),
    assessment(status="refuted", counter=["receipt:1"], unknowns=[" \t\n"]),
])
def test_assessment_states_require_directed_evidence_or_specific_gaps(value):
    with pytest.raises(ValidationError):
        ExplanationAssessment.model_validate(value)


def test_assessment_text_validation_preserves_nonblank_original_strings():
    original = {**assessment(unknowns=["  Evidence gap retained.\n"]),
                "explanation": "  Candidate explanation retained.\n",
                "checks": ["  Check retained.\n"]}
    parsed = ExplanationAssessment.model_validate(original, strict=True)
    assert parsed.model_dump() == original


def test_read_governance_material_must_be_bound_to_the_governance_explanation():
    conclusion = Conclusion.model_validate(report(
        [assessment("governance_execution", "supported", support=["receipt:1"])], "governance_explained"))
    evidence = [observed("receipt:1"), observed("source:1", "governance_source")]
    with pytest.raises(ValueError, match="Bind supported governance_execution"):
        validate_assessment_bindings(conclusion, evidence)
    conclusion.assessments[0].support_evidence_ids = ["source:1"]
    validate_assessment_bindings(conclusion, evidence)


@pytest.mark.parametrize("kind,category", [
    ("public_context_source", "incident_postmortem"),
    ("public_context_source", "governance_proposal"),
    ("governance_source", "incident_postmortem"),
    ("governance_source", "public_context_source"),
])
@pytest.mark.parametrize("classification", ["unresolved", "needs_review", "governance_explained"])
def test_supported_governance_candidate_requires_governance_material(kind, category, classification):
    conclusion = Conclusion.model_validate(report(
        [assessment("governance_execution", "supported", support=["source:1"])], classification))
    with pytest.raises(ValueError, match="Bind supported governance_execution"):
        validate_assessment_bindings(conclusion, [observed("source:1", kind, payload={"category": category})])


@pytest.mark.parametrize("classification", ["unresolved", "needs_review"])
def test_governance_material_supports_a_candidate_with_an_unresolved_report(classification):
    conclusion = Conclusion.model_validate(report(
        [assessment("governance_execution", "supported", support=["source:1"])], classification))
    validate_assessment_bindings(conclusion, [observed(
        "source:1", "governance_source", payload={"category": "governance_proposal"})])


def test_incident_material_supports_the_incident_candidate():
    conclusion = Conclusion.model_validate(report([
        assessment("reported_protocol_exploit", "supported", support=["source:1"])]))
    validate_assessment_bindings(conclusion, [observed(
        "source:1", "public_context_source", payload={"category": "incident_postmortem"})])


def test_governance_classification_requires_a_supported_governance_candidate():
    conclusion = Conclusion.model_validate(report(
        [assessment("event_cause", "supported", support=["source:1"])], "governance_explained"))
    with pytest.raises(ValueError, match="Bind governance_explained"):
        validate_assessment_bindings(conclusion, [observed(
            "source:1", "governance_source", payload={"category": "governance_proposal"})])


@pytest.mark.parametrize("status", ["error", "empty"])
def test_failed_or_empty_reads_are_not_support_or_counter_evidence(status):
    for judgment, side in (("supported", "support"), ("refuted", "counter")):
        candidate = assessment(status=judgment, **{side: ["read:1"]})
        with pytest.raises(ValueError, match="successful observed"):
            validate_assessment_bindings(Conclusion.model_validate(report([candidate])),
                                         [observed("read:1", status=status)])


def test_unknown_can_record_failed_followup_without_turning_it_into_support():
    initial = [observed("receipt:1"), observed("read:failed", "token_state", "error", {"error_code": "tool_read_failed"})]
    result = report([assessment("supply_decrease", unknowns=["历史供应读取失败，无法形成前后状态比较。"])])
    outcome = asyncio.run(run_investigation(FunctionModel(lambda *_: ModelResponse(parts=[
        ToolCallPart("final_report", result)])), Backend(), scope(initial), strategy="one_shot"))
    assert outcome.status == "completed" and outcome.report.assessments[0].status == "unknown"
    assert outcome.report.assessments[0].support_evidence_ids == []
    assert any(item.status == "error" for item in outcome.evidence)


@pytest.mark.parametrize("before,after,status", [
    ("1000000000000000000000000000", "1000000000000000000000000000", "refuted"),
    ("100", "101", "refuted"), (str(MAX_UINT256), str(MAX_UINT256 - 1), "supported"),
])
def test_historical_supply_net_change_uses_all_raw_integer_digits(before, after, status):
    readings = [state(100, before), state(110, after)]
    refs = [item["evidence_id"] for item in readings]
    judgment = assessment("supply_decrease", status,
                          support=refs if status == "supported" else [],
                          counter=refs if status == "refuted" else [])
    validate_assessment_bindings(Conclusion.model_validate(report([judgment])), readings, request=scope())
    judgment["status"] = "refuted" if status == "supported" else "supported"
    judgment["support_evidence_ids"], judgment["counter_evidence_ids"] = (
        judgment["counter_evidence_ids"], judgment["support_evidence_ids"])
    with pytest.raises(ValueError, match="net supply comparison"):
        validate_assessment_bindings(Conclusion.model_validate(report([judgment])), readings, request=scope())


@pytest.mark.parametrize("mutate", [
    lambda items: items.__setitem__(1, state(110, token=OTHER_TOKEN)),
    lambda items: items[1]["payload"].update(token_address=OTHER_TOKEN),
    lambda items: items[1]["payload"].update(block_number=100),
    lambda items: items[1]["payload"].update(total_supply_raw=1e27),
    lambda items: items.append(state(111)),
    lambda items: items.append(state(105, raw="malformed")),
    lambda items: items.append(observed("state:malformed", "token_state", payload=[])),
    lambda items: items.append({"evidence_id": "state:null", "kind": "token_state", "status": "ok", "payload": None}),
    lambda items: items.append(observed("state:duplicate", "token_state", payload={
        "token_address": TOKEN, "block_number": 110, "total_supply_raw": "1000000000000000000000000000"})),
])
def test_supply_pair_rejects_other_tokens_or_inconsistent_referenced_states(mutate):
    readings = [state(100), state(110)]
    mutate(readings)
    judgment = assessment("supply_decrease", "refuted", counter=[item["evidence_id"] for item in readings])
    with pytest.raises(ValueError, match="historical raw supply"):
        validate_assessment_bindings(Conclusion.model_validate(report([judgment])), readings, request=scope())


def test_one_supply_block_and_source_code_do_not_stand_in_for_two_state_reads():
    readings = [state(110), observed("source:code", "public_context_source")]
    judgment = assessment("supply_decrease", "refuted", counter=[item["evidence_id"] for item in readings])
    with pytest.raises(ValueError, match="two scoped historical"):
        validate_assessment_bindings(Conclusion.model_validate(report([judgment])), readings, request=scope())


def test_offline_one_shot_receives_all_preloaded_observations_and_only_output_tool():
    initial = [observed("receipt:1"), observed("source:1", "governance_source"), state(100), state(110),
               observed("read:failed", "token_state", "error")]
    before = copy.deepcopy(initial)
    def finish(messages, info):
        assert info.function_tools == []
        schema = info.output_tools[0].parameters_json_schema
        assert "assessments" in schema["required"]
        prompt = next(part for message in messages if isinstance(message, ModelRequest)
                      for part in message.parts if isinstance(part, UserPromptPart))
        assert json.loads(prompt.content)["scope"]["initial_observations"] == initial
        assert json.loads(prompt.content)["runtime_limits"] == {
            "max_model_requests": 1, "max_tool_attempts": 8,
            "deadline_seconds": 180, "max_output_tokens": 1024}
        result = report([assessment("governance_execution", "supported", support=["source:1"])], "governance_explained")
        return ModelResponse(parts=[ToolCallPart("final_report", result)])
    backend = Backend()
    outcome = asyncio.run(run_investigation(FunctionModel(finish), backend, scope(initial),
        limits=RuntimeLimits(max_model_requests=6), strategy="one_shot"))
    assert outcome.status == "completed" and outcome.model_requests == 1 and outcome.tool_attempts == 0
    assert backend.calls == [] and initial == before


def test_one_shot_rejected_tool_selection_never_executes_a_backend_read():
    backend = Backend()
    outcome = asyncio.run(run_investigation(FunctionModel(lambda *_: ModelResponse(parts=[
        ToolCallPart("get_receipt", {"tx_hash": TX})])), backend, scope(), strategy="one_shot"))
    assert outcome.status == "partial" and outcome.stop_reason == "one_shot_output_only"
    assert outcome.model_requests == 1 and outcome.tool_attempts == 1 and backend.calls == []


def test_one_shot_validation_retry_cannot_send_a_second_model_request():
    invalid = report([])
    backend = Backend()
    outcome = asyncio.run(run_investigation(FunctionModel(lambda *_: ModelResponse(parts=[
        ToolCallPart("final_report", invalid)])), backend, scope([observed("receipt:1")]), strategy="one_shot"))
    assert outcome.status == "partial" and outcome.report is None
    assert outcome.model_requests == 1 and backend.calls == []


def test_offline_adaptive_and_one_shot_publish_evidence_assessments():
    initial = [observed("receipt:1", payload={"governance_hint": True,
                   "recipient": "0x000000000000000000000000000000000000dead"}),
               observed("source:1", "governance_source", payload={"category": "governance_proposal"}),
               state(100), state(110)]
    for strategy in ("adaptive", "one_shot"):
        backend = Backend()
        outcome = asyncio.run(run_investigation(build_offline_model(), backend, scope(initial), strategy=strategy))
        assert outcome.status == "completed", outcome.stop_reason
        found = {item.explanation_id: item for item in outcome.report.assessments}
        assert found["governance_execution"].status == "supported"
        assert found["supply_decrease"].status == "refuted"
        assert len(found["supply_decrease"].counter_evidence_ids) == 2
        if strategy == "one_shot":
            assert outcome.model_requests == 1 and backend.calls == []
        else:
            assert backend.calls[:2] == ["get_receipt", "get_governance_source"]


def test_offline_incident_context_stays_a_public_context_explanation():
    backend = Backend("public_context_source")
    outcome = asyncio.run(run_investigation(build_offline_model(), backend,
        scope(sources={"incident-postmortem": "https://example.org/public-incident-source"})))
    assert outcome.status == "completed", outcome.stop_reason
    assert backend.calls == ["get_receipt", "get_governance_source"]
    assert outcome.report.classification == "unresolved"
    assert outcome.report.assessments[0].explanation_id == "public_context_explanation"


@pytest.mark.parametrize("thinking", [False, True])
def test_one_shot_sdk_wire_request_has_only_final_report_and_current_required_schema(tmp_path, thinking):
    bodies = []
    initial = [observed("receipt:1"), observed("source:1", "governance_source")]
    async def respond(http_request):
        body = json.loads(http_request.content)
        bodies.append(body)
        assert body["stream"] is False and [item["function"]["name"] for item in body["tools"]] == ["final_report"]
        assert body["thinking"] == {"type": "enabled" if thinking else "disabled"}
        assert "assessments" in body["tools"][0]["function"]["parameters"]["required"]
        result = report([assessment("governance_execution", "supported", support=["source:1"])], "governance_explained")
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 1,
            "model": "deepseek-v3.2", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{"id": "call_final", "type": "function",
                "function": {"name": "final_report", "arguments": json.dumps(result)}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = create_gateway_model("synthetic-test-key", http_client=client, thinking=thinking)
            quote = PriceQuote("deepseek-v3.2", Decimal("1.6"), Decimal("2.4"),
                "https://tokendance.space/portal/api/models/deepseek-v3.2/endpoints/stats",
                datetime.now(timezone.utc), verified=True, source_sha256="a" * 64)
            ledger = BudgetLedger(tmp_path / "synthetic-budget.sqlite3")
            with models.override_allow_model_requests(True):
                outcome = await run_investigation(model, Backend(), scope(initial), strategy="one_shot",
                    paid_enabled=True, ledger=ledger, price_quotes={"deepseek-v3.2": quote})
            return outcome, ledger.snapshot()
    outcome, ledger = asyncio.run(execute())
    assert outcome.status == "completed", outcome.stop_reason
    assert outcome.model_requests == len(bodies) == ledger["requests_reserved"] == 1
    assert outcome.tool_attempts == 0


def test_one_shot_transport_failure_reserves_one_request_and_does_not_send_fallback(tmp_path):
    sent_models = []
    async def respond(http_request):
        sent_models.append(json.loads(http_request.content)["model"])
        return httpx.Response(500, json={"error": {"message": "synthetic transport failure"}})
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model_names = ("deepseek-v3.2", "minimax-m2.7")
            primary, fallback = [create_gateway_model("synthetic-test-key", model_name=name, http_client=client)
                                 for name in model_names]
            quotes = {name: PriceQuote(name, Decimal("1.6"), Decimal("2.4"),
                      f"https://tokendance.space/portal/api/models/{name}/endpoints/stats",
                      datetime.now(timezone.utc), verified=True, source_sha256="a" * 64) for name in model_names}
            ledger = BudgetLedger(tmp_path / "synthetic-budget.sqlite3")
            with models.override_allow_model_requests(True):
                outcome = await run_investigation(primary, Backend(), scope(), strategy="one_shot",
                    fallback_model=fallback, paid_enabled=True, ledger=ledger, price_quotes=quotes)
            return outcome, ledger.snapshot()
    outcome, ledger = asyncio.run(execute())
    assert outcome.status == "partial" and outcome.report is None
    assert sent_models == ["deepseek-v3.2"]
    assert outcome.model_requests == ledger["requests_reserved"] == 1
    assert Decimal(ledger["reserved_rmb"]) > 0
