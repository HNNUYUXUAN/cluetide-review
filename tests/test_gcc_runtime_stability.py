"""Offline regressions for bounded report turns and validation recovery."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from pydantic_ai import models
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter, ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart,
    ToolReturnPart, UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import ToolDefinition

from cluetide.agent import (
    AgentRequest, RuntimeLimits, ToolEvidence, _BoundedModel, _Halt, _State,
    compare_supply_observations, create_gateway_model, run_investigation,
)
from cluetide.budget import BudgetLedger, PriceQuote

TX = "0x" + "12" * 32
TOKEN = "0x" + "34" * 20


@pytest.fixture(autouse=True)
def offline_models_only():
    with models.override_allow_model_requests(False):
        yield


def scope():
    return AgentRequest(tx_hash=TX, token_address=TOKEN, from_block=100, to_block=110,
        finalized_block=120, initial_observations=[{
            "evidence_id": "transfer:observed", "kind": "transfer", "status": "ok",
            "payload": {"raw_amount": "100000000000000000000000000"}}])


def report(citation="transfer:observed"):
    return {"summary": "已采集转账线索，完整原因待核对。", "classification": "unresolved",
        "claims": [{"text": "存在已采集的代币转账观察。", "evidence_ids": [citation]}],
        "limitations": ["报告范围限于所选交易和已返回观察。"],
        "assessments": [{"explanation_id": "event_cause", "explanation": "转账原因待核对。",
            "status": "unknown", "support_evidence_ids": [], "counter_evidence_ids": [],
            "unknowns": ["缺少可绑定原因的业务背景。"], "checks": ["检查已采集转账。"]}]}


class UnusedBackend:
    pass


def _bounded(model, *, context_bytes, requests=1):
    state = _State(UnusedBackend(), scope(), RuntimeLimits(
        max_model_requests=requests, max_context_bytes=context_bytes))
    return _BoundedModel(model, state, ledger=None, price_quotes={}, fallback_model=None, paid_enabled=False)


def test_report_turn_uses_its_available_schema_for_the_context_gate():
    seen = []

    def respond(messages, info):
        seen.append((messages, info))
        return ModelResponse(parts=[ToolCallPart("final_report", {})])

    messages = [ModelRequest(parts=[UserPromptPart("Investigate the selected event.")])]
    parameters = ModelRequestParameters(function_tools=[ToolDefinition(name="get_receipt",
        parameters_json_schema={"type": "object", "description": "x" * 5000})],
        output_tools=[ToolDefinition(name="final_report", parameters_json_schema={"type": "object"})])
    bounded = _bounded(FunctionModel(respond), context_bytes=1500)
    asyncio.run(bounded.request(messages, None, parameters))
    assert bounded.state.model_requests == 1
    assert len(seen) == 1 and seen[0][1].function_tools == []
    assert "final allowed model request" in seen[0][0][-1].parts[0].content


def test_report_boundary_instruction_is_checked_before_sending_a_request():
    sent = []
    model = FunctionModel(lambda *_: sent.append(True) or ModelResponse(parts=[]))
    messages = [ModelRequest(parts=[UserPromptPart("x" * 1200)])]
    # The original messages fit; the required report-boundary instruction does not.
    context_bytes = len(ModelMessagesTypeAdapter.dump_json(messages)) + 2
    bounded = _bounded(model, context_bytes=context_bytes)
    with pytest.raises(_Halt, match="context_limit"):
        asyncio.run(bounded.request(messages, None, ModelRequestParameters()))
    assert sent == [] and bounded.state.model_requests == 0


@pytest.mark.parametrize("first_error", ["schema", "citation"])
def test_report_repairs_can_use_remaining_requests_after_two_distinct_errors(first_error):
    seen = []

    def respond(messages, info):
        seen.append(messages)
        if len(seen) == 1:
            output = report("invented:reference") if first_error == "citation" else report()
            if first_error == "schema":
                output["assessments"] = []
        elif len(seen) == 2:
            output = report("invented:reference") if first_error == "schema" else report()
            if first_error == "citation":
                output["assessments"] = []
        else:
            output = report()
            assert info.function_tools == []
        return ModelResponse(parts=[ToolCallPart("final_report", output)])

    outcome = asyncio.run(run_investigation(FunctionModel(respond), UnusedBackend(), scope(),
        limits=RuntimeLimits(max_model_requests=3)))
    assert outcome.status == "completed", outcome.stop_reason
    assert outcome.model_requests == len(seen) == 3 and outcome.tool_attempts == 0
    retries = [part for message in seen[-1] if isinstance(message, ModelRequest)
               for part in message.parts if isinstance(part, RetryPromptPart)]
    assert len(retries) == 2
    assert any("transfer:observed" in str(part.content) for part in retries)


@pytest.mark.parametrize("strategy,limit", [("adaptive", 2), ("adaptive", 4), ("one_shot", 1)])
def test_repeated_invalid_reports_stop_at_the_actual_request_budget(strategy, limit):
    sent = []

    def respond(*_):
        sent.append(True)
        return ModelResponse(parts=[ToolCallPart("final_report", report("invented:reference"))])

    outcome = asyncio.run(run_investigation(FunctionModel(respond), UnusedBackend(), scope(),
        limits=RuntimeLimits(max_model_requests=limit), strategy=strategy))
    assert outcome.status == "partial" and outcome.report is None
    assert outcome.stop_reason == "model_request_limit"
    assert outcome.model_requests == len(sent) == limit and outcome.tool_attempts == 0


def test_fallback_rechecks_its_final_instruction_before_reserving_or_sending(tmp_path):
    sent = []

    async def respond(http_request):
        sent.append(json.loads(http_request.content)["model"])
        return httpx.Response(500, json={"error": {"message": "synthetic transport failure"}})

    async def execute():
        messages = [ModelRequest(parts=[UserPromptPart("x" * 1200)])]
        ledger = BudgetLedger(tmp_path / "budget.sqlite3")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            primary = create_gateway_model("synthetic-key", http_client=client)
            fallback = create_gateway_model("synthetic-key", model_name="minimax-m2.7", http_client=client)
            state = _State(UnusedBackend(), scope(), RuntimeLimits(max_model_requests=2,
                max_context_bytes=len(ModelMessagesTypeAdapter.dump_json(messages)) + 2))
            prices = {name: PriceQuote(name, Decimal("1.6"), Decimal("2.4"),
                f"https://tokendance.space/portal/api/models/{name}/endpoints/stats",
                datetime.now(timezone.utc), verified=True, source_sha256="a" * 64)
                for name in ("deepseek-v3.2", "minimax-m2.7")}
            bounded = _BoundedModel(primary, state, ledger=ledger, price_quotes=prices,
                fallback_model=fallback, paid_enabled=True)
            with models.override_allow_model_requests(True), pytest.raises(_Halt, match="context_limit"):
                await bounded.request(messages, None, ModelRequestParameters())
            return state, ledger.snapshot()

    state, budget = asyncio.run(execute())
    assert sent == ["deepseek-v3.2"]
    assert state.model_requests == budget["requests_reserved"] == 1


def test_failed_tool_reference_is_repaired_using_only_successful_observations():
    seen = []

    class Backend:
        async def get_receipt(self, tx_hash):
            raise RuntimeError("synthetic credential-bearing upstream diagnostic")

    def respond(messages, info):
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart("get_receipt", {"tx_hash": TX})])
        if len(seen) == 2:
            return ModelResponse(parts=[ToolCallPart("final_report", report("tool-error:1"))])
        feedback = next(part.content for message in reversed(messages) if isinstance(message, ModelRequest)
                        for part in message.parts if isinstance(part, RetryPromptPart))
        assert "transfer:observed" in feedback and "tool-error:1" not in feedback
        output = report()
        output["assessments"][0]["unknowns"].append("回执读取失败，执行状态待核查。")
        return ModelResponse(parts=[ToolCallPart("final_report", output)])

    outcome = asyncio.run(run_investigation(FunctionModel(respond), Backend(), scope(),
        limits=RuntimeLimits(max_model_requests=3)))
    assert outcome.status == "completed" and outcome.model_requests == 3 and outcome.tool_attempts == 1
    assert outcome.evidence[-1].status == "error"
    assert "credential-bearing" not in outcome.model_dump_json()
    assert any(item["event"] == "report_validation_retry" for item in outcome.trace)


@pytest.mark.parametrize("problem", ["unknown_id", "failed_state", "duplicate_id"])
def test_supply_comparison_requires_successful_distinct_known_observations(problem):
    readings = [ToolEvidence(evidence_id=f"state:{TOKEN}:{block}", kind="token_state", payload={
        "token_address": TOKEN, "block_number": block, "total_supply_raw": amount})
        for block, amount in [(100, "1000000000000000000000000001"), (110, "1000000000000000000000000000")]]
    known = {item.evidence_id: item for item in readings}
    references = list(known)
    assert compare_supply_observations(references, known, scope()) == (
        1000000000000000000000000001, 1000000000000000000000000000)
    if problem == "unknown_id":
        references.append("state:unobserved")
    elif problem == "failed_state":
        readings[0].status = "error"
    else:
        references.append(references[0])
    assert compare_supply_observations(references, known, scope()) is None


def test_model_receives_exact_amount_roles_and_source_conflict_with_interpretation_bounds():
    origin, target = "0x" + "56" * 20, "0x" + "78" * 20
    raw = "100000000000000000000000001"
    facts = {"facts": [{"raw_amount": raw, "decimals": 18, "formatted_amount": "100000000.000000000000000001"}]}
    request = scope()
    request.initial_observations.append({"evidence_id": "derived:transfer-amounts",
        "kind": "deterministic_transfer_amounts", "status": "ok", "payload": facts})
    request.governance_sources = {"context": "https://example.org/incident"}
    source = {"category": "incident_postmortem", "source_quality_note": "来源A与来源B的事件日期相差一天。"}

    class Backend:
        async def get_transaction(self, tx_hash):
            return {"evidence_id": "transaction:observed", "kind": "transaction", "status": "ok",
                "payload": {"from": origin, "to": target, "value": "0x0"}}

        async def get_governance_source(self, source_id):
            return {"evidence_id": "source:context", "kind": "public_context_source", "status": "ok", "payload": source}

    def respond(messages, info):
        initial = json.loads(next(part.content for message in messages if isinstance(message, ModelRequest)
            for part in message.parts if isinstance(part, UserPromptPart)))
        assert initial["scope"]["initial_observations"][-1]["payload"] == facts
        for instruction in ("Transaction from is the originating address", "source_quality_note",
                            "Receipt log data is hexadecimal", "not business authorization"):
            assert instruction in info.instructions
        observed = [part.content for message in messages if isinstance(message, ModelRequest)
                    for part in message.parts if isinstance(part, ToolReturnPart)]
        if not observed:
            return ModelResponse(parts=[ToolCallPart("get_transaction", {"tx_hash": TX}),
                ToolCallPart("get_governance_source", {"source_id": "context"})])
        by_id = {item["evidence_id"]: item for item in observed}
        assert by_id["transaction:observed"]["payload"] == {"from": origin, "to": target, "value": "0x0"}
        assert by_id["source:context"]["payload"] == source
        return ModelResponse(parts=[ToolCallPart("final_report", report())])

    outcome = asyncio.run(run_investigation(FunctionModel(respond), Backend(), request,
        limits=RuntimeLimits(max_model_requests=2)))
    assert outcome.status == "completed" and outcome.tool_attempts == 2
