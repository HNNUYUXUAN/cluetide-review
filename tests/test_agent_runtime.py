from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from pydantic_ai import models
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from cluetide.agent import AgentRequest, RuntimeLimits, build_offline_model, create_gateway_model, run_investigation
from cluetide.budget import BudgetLedger, PriceQuote

TX = "0x" + "a1" * 32
TOKEN = "0x" + "b2" * 20


@pytest.fixture(autouse=True)
def no_real_model_requests():
    with models.override_allow_model_requests(False):
        yield


def request(**updates):
    values = dict(tx_hash=TX, token_address=TOKEN, from_block=100, to_block=110,
                  finalized_block=120, governance_sources={"proposal": "https://vote.uniswapfoundation.org/proposals/93"})
    values.update(updates)
    return AgentRequest(**values)


class Backend:
    def __init__(self, governance_hint=True, failure=False):
        self.governance_hint, self.failure = governance_hint, failure
        self.calls = []

    async def get_receipt(self, tx_hash):
        self.calls.append(("get_receipt", tx_hash))
        if self.failure:
            raise RuntimeError("Do not expose upstream credential-bearing error text")
        return {"evidence_id": "receipt:1", "kind": "receipt", "status": "ok",
                "payload": {"status": "0x1", "governance_hint": self.governance_hint,
                            "recipient": "0x000000000000000000000000000000000000dead"}}

    async def get_transaction(self, tx_hash):
        self.calls.append(("get_transaction", tx_hash))
        return {"evidence_id": "transaction:1", "kind": "transaction", "status": "ok", "payload": {"target": "unknown"}}

    async def get_governance_source(self, source_id):
        self.calls.append(("get_governance_source", source_id))
        return {"evidence_id": "governance:1", "kind": "governance_source", "status": "ok",
                "payload": {"text": "Proposal source explains governance execution and a transfer to dead."}}

    async def get_token_state(self, token_address, block_number):
        self.calls.append(("get_token_state", block_number))
        return {"evidence_id": f"token:{block_number}", "kind": "token_state", "status": "ok",
                "payload": {"totalSupply": "1000000000000000000000000000"}}


def report(citation="governance:1", classification="governance_explained"):
    return {"summary": "公开治理材料提供事件解释，仍需核对交易语义。", "classification": classification,
            "claims": [{"text": "观察支持治理解释。", "evidence_ids": [citation], "interpretation": True}],
            "limitations": ["转至dead本身不能证明totalSupply下降。", "文件哈希验证完整性，不能证明事实。"],
            "assessments": [{"explanation_id": "governance_execution" if classification == "governance_explained" else "event_cause",
                "explanation": "公开观察提供候选事件解释。", "status": "supported" if classification == "governance_explained" else "unknown",
                "support_evidence_ids": [citation] if classification == "governance_explained" else [],
                "counter_evidence_ids": [], "unknowns": ["完整交易语义仍需核对。"], "checks": ["检查已返回的公开观察。"]}]}


def dynamic_model(messages, info):
    observed = [part for message in messages if isinstance(message, ModelRequest)
                for part in message.parts if isinstance(part, ToolReturnPart)]
    if not observed:
        return ModelResponse(parts=[ToolCallPart("get_receipt", {"tx_hash": TX})])
    if len(observed) == 1:
        if observed[0].content["payload"].get("governance_hint"):
            return ModelResponse(parts=[ToolCallPart("get_governance_source", {"source_id": "proposal"})])
        return ModelResponse(parts=[ToolCallPart("get_transaction", {"tx_hash": TX})])
    if observed[-1].tool_name == "get_governance_source":
        conclusion = report()
    else:
        conclusion = report("transaction:1", "unresolved")
    return ModelResponse(parts=[ToolCallPart("final_report", conclusion)])


@pytest.mark.parametrize("hint,expected", [(True, "get_governance_source"), (False, "get_transaction")])
def test_function_model_selects_second_tool_from_receipt_observation(hint, expected):
    backend = Backend(governance_hint=hint)
    outcome = asyncio.run(run_investigation(FunctionModel(dynamic_model), backend, request()))
    assert outcome.status == "completed"
    assert [name for name, _ in backend.calls] == ["get_receipt", expected]
    assert outcome.model_requests == 3 and outcome.tool_attempts == 2
    assert len(outcome.evidence) == 2
    assert outcome.report.claims[0].evidence_ids[-1] in {item.evidence_id for item in outcome.evidence}


def test_progress_is_observed_before_completion_and_detached():
    snapshots = []
    def observe(value):
        snapshots.append(json.loads(json.dumps(value)))
        value["trace"].clear()
    outcome = asyncio.run(run_investigation(FunctionModel(dynamic_model), Backend(), request(), progress_callback=observe))
    assert outcome.status == "completed"
    assert snapshots[0]["model_requests"] == 1 and snapshots[0]["tool_attempts"] == 0
    assert any(row["stage"] == "reading" for row in snapshots)
    assert any(row["trace"][-1]["event"] == "tool_observed" for row in snapshots)
    assert outcome.trace and outcome.model_requests == 3


def test_progress_observer_failure_preserves_execution():
    def observe(_):
        raise RuntimeError("Synthetic observer failure")
    outcome = asyncio.run(run_investigation(FunctionModel(dynamic_model), Backend(), request(), progress_callback=observe))
    assert outcome.status == "completed" and outcome.model_requests == 3


def test_failed_read_is_an_observation_and_counts_as_attempt():
    def response(messages, info):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("get_receipt", {"tx_hash": TX})])
        result = [part.content for message in messages if isinstance(message, ModelRequest)
                  for part in message.parts if isinstance(part, ToolReturnPart)][-1]
        assert result["status"] == "error"
        assert "credential" not in json.dumps(result)
        return ModelResponse(parts=[ToolCallPart("get_transaction", {"tx_hash": TX})])
    backend = Backend(failure=True)
    outcome = asyncio.run(run_investigation(FunctionModel(response), backend, request(),
                                            limits=RuntimeLimits(max_model_requests=2)))
    assert outcome.status == "partial" and outcome.stop_reason == "model_request_limit"
    assert outcome.tool_attempts == 2
    assert outcome.evidence[0].status == "error"


def test_equivalent_case_insensitive_query_stops_before_duplicate_read():
    def repeat(messages, info):
        tx = TX if len(messages) == 1 else "0x" + TX[2:].upper()
        return ModelResponse(parts=[ToolCallPart("get_receipt", {"tx_hash": tx})])
    backend = Backend()
    outcome = asyncio.run(run_investigation(FunctionModel(repeat), backend, request()))
    assert outcome.stop_reason == "repeated_equivalent_query"
    assert outcome.tool_attempts == 2 and len(backend.calls) == 1


@pytest.mark.parametrize("tool,args,reason", [
    ("send_transaction", {}, "tool_outside_allowlist"),
    ("get_receipt", {"tx_hash": "0x" + "cc" * 32}, "transaction_outside_scope"),
    ("get_token_state", {"token_address": TOKEN, "block_number": 111}, "block_outside_finalized_window"),
    ("get_token_state", {"token_address": TOKEN, "block_number": True}, "block_outside_finalized_window"),
    ("get_governance_source", {"source_id": "https://untrusted.example"}, "source_outside_allowlist"),
    ("get_receipt", {"tx_hash": TX, "extra": "bad"}, "invalid_tool_arguments"),
])
def test_out_of_scope_attempt_is_counted_and_never_executed(tool, args, reason):
    backend = Backend()
    outcome = asyncio.run(run_investigation(FunctionModel(lambda *_: ModelResponse(parts=[ToolCallPart(tool, args)])),
                                            backend, request()))
    assert outcome.stop_reason == reason and outcome.tool_attempts == 1
    assert not backend.calls


def test_tools_cannot_exceed_eight_attempts_even_in_one_batch():
    response = ModelResponse(parts=[ToolCallPart("get_token_state", {"token_address": TOKEN, "block_number": block})
                                    for block in range(100, 109)])
    backend = Backend()
    outcome = asyncio.run(run_investigation(FunctionModel(lambda *_: response), backend, request()))
    assert outcome.stop_reason == "tool_attempt_limit" and outcome.tool_attempts == 8
    assert not backend.calls  # A rejected batch has no partial side effects.


def test_fabricated_citation_is_rejected():
    def fake(messages, info):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("get_receipt", {"tx_hash": TX})])
        return ModelResponse(parts=[ToolCallPart("final_report", report("fabricated:1", "unresolved"))])
    outcome = asyncio.run(run_investigation(FunctionModel(fake), Backend(), request()))
    assert outcome.status == "partial" and outcome.report is None


def test_deadline_cancels_an_inflight_read():
    class Slow(Backend):
        async def get_receipt(self, tx_hash):
            await asyncio.sleep(5)
    model = FunctionModel(lambda *_: ModelResponse(parts=[ToolCallPart("get_receipt", {"tx_hash": TX})]))
    outcome = asyncio.run(run_investigation(model, Slow(), request(), limits=RuntimeLimits(deadline_seconds=0.03)))
    assert outcome.status == "partial" and outcome.stop_reason == "deadline_exceeded"


def test_stop_event_cancels_an_inflight_model():
    async def task():
        stop = asyncio.Event()
        async def slow(*_):
            stop.set()
            await asyncio.sleep(5)
        return await run_investigation(FunctionModel(slow), Backend(), request(), stop_event=stop)
    outcome = asyncio.run(task())
    assert outcome.status == "stopped" and outcome.model_requests == 1


def test_paid_model_requires_explicit_gate_without_network():
    model = create_gateway_model("test-key-not-a-real-credential")
    outcome = asyncio.run(run_investigation(model, Backend(), request()))
    assert outcome.status == "budget_exhausted" and outcome.model_requests == 0


def quote(model):
    return PriceQuote(model, Decimal("1.6"), Decimal("2.4"),
                      f"https://tokendance.space/portal/api/models/{model}/endpoints/stats",
                      datetime.now(timezone.utc), verified=True, source_sha256="a" * 64)


@pytest.mark.parametrize("model_name", ["deepseek-v3.2", "minimax-m2.7"])
def test_gateway_reasoning_content_roundtrip_nonstreaming(tmp_path, model_name):
    bodies = []
    async def respond(http_request):
        body = json.loads(http_request.content)
        bodies.append(body)
        assert body["stream"] is False and body["model"] == model_name
        if model_name == "deepseek-v3.2":
            assert body["thinking"] == {"type": "disabled"}
        else:
            assert "thinking" not in body
        if len(bodies) == 1:
            message = {"role": "assistant", "content": None, "reasoning_content": "synthetic-provider-reasoning",
                       "tool_calls": [{"id": "call_1", "type": "function", "function": {
                           "name": "get_receipt", "arguments": json.dumps({"tx_hash": TX})}}]}
        elif len(bodies) == 2:
            assistant = [msg for msg in body["messages"] if msg["role"] == "assistant"][-1]
            assert assistant["reasoning_content"] == "synthetic-provider-reasoning"
            message = {"role": "assistant", "content": None, "reasoning_content": "synthetic-second-step",
                       "tool_calls": [{"id": "call_2", "type": "function", "function": {
                           "name": "get_governance_source", "arguments": '{"source_id":"proposal"}'}}]}
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_3", "type": "function",
                       "function": {"name": "final_report", "arguments": json.dumps(report())}}]}
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 1,
                              "model": model_name, "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
                              "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}})
    async def task():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = create_gateway_model("synthetic-test-key", model_name=model_name, http_client=client)
            with models.override_allow_model_requests(True):
                return await run_investigation(model, Backend(), request(), paid_enabled=True,
                    ledger=BudgetLedger(tmp_path / "budget.sqlite3"), price_quotes={model_name: quote(model_name)})
    outcome = asyncio.run(task())
    assert outcome.status == "completed", outcome.stop_reason
    assert len(bodies) == 3 and outcome.model_requests == 3
    assert "synthetic-provider-reasoning" not in outcome.model_dump_json()


def test_failed_request_does_not_trigger_hidden_sdk_retry(tmp_path):
    count = 0
    async def respond(_):
        nonlocal count
        count += 1
        return httpx.Response(500, json={"error": {"message": "synthetic server error"}})
    async def task():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = create_gateway_model("synthetic-test-key", http_client=client)
            ledger = BudgetLedger(tmp_path / "budget.sqlite3")
            with models.override_allow_model_requests(True):
                outcome = await run_investigation(model, Backend(), request(), paid_enabled=True,
                    ledger=ledger, price_quotes={"deepseek-v3.2": quote("deepseek-v3.2")})
            return outcome, ledger.snapshot()
    outcome, snapshot = asyncio.run(task())
    assert count == 1 and outcome.model_requests == 1
    assert snapshot["requests_reserved"] == 1 and Decimal(snapshot["reserved_rmb"]) > 0


def test_authorized_limits_cannot_be_enlarged():
    with pytest.raises(ValueError):
        RuntimeLimits(max_model_requests=7)
    with pytest.raises(ValueError):
        RuntimeLimits(max_tool_attempts=9)
    with pytest.raises(ValueError):
        RuntimeLimits(deadline_seconds=181)


def test_explicit_fallback_failures_share_six_request_budget(tmp_path):
    calls = []
    fallback_turn = 0
    async def respond(http_request):
        nonlocal fallback_turn
        body = json.loads(http_request.content)
        calls.append(body["model"])
        assert body["provider"] == {"only": ["agentuniverse"], "allow_fallbacks": False}
        if body["model"] == "deepseek-v3.2":
            return httpx.Response(500, json={"error": {"message": "synthetic transient error"}})
        fallback_turn += 1
        if fallback_turn == 1:
            name, args = "get_receipt", {"tx_hash": TX}
        elif fallback_turn == 2:
            name, args = "get_governance_source", {"source_id": "proposal"}
        else:
            name, args = "final_report", report()
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 1,
            "model": body["model"], "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{"id": f"call_{fallback_turn}",
                "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}})
    async def task():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            primary = create_gateway_model("synthetic-test-key", http_client=client)
            fallback = create_gateway_model("synthetic-test-key", model_name="minimax-m2.7", http_client=client)
            ledger = BudgetLedger(tmp_path / "budget.sqlite3")
            with models.override_allow_model_requests(True):
                result = await run_investigation(primary, Backend(), request(), paid_enabled=True,
                    fallback_model=fallback, ledger=ledger, price_quotes={name: quote(name)
                        for name in ("deepseek-v3.2", "minimax-m2.7")})
            return result, ledger.snapshot()
    result, budget = asyncio.run(task())
    assert result.status == "completed", result.stop_reason
    assert result.model_requests == len(calls) == budget["requests_reserved"] == 6
    assert result.tool_attempts == 2


def test_offline_simulation_respects_last_request_report_boundary():
    backend = Backend()
    outcome = asyncio.run(run_investigation(build_offline_model(), backend, request(),
                                            limits=RuntimeLimits(max_model_requests=2)))
    assert outcome.status == "completed", outcome.stop_reason
    assert outcome.model_requests == 2 and outcome.tool_attempts == 1
    assert len(backend.calls) == 1
    assert outcome.report.classification == "unresolved"
    assert any(item["event"] == "final_report_required" for item in outcome.trace)


def test_scope_constraints_are_visible_in_model_tool_schema():
    def choose(messages, info):
        if len(messages) == 1:
            tools = {tool.name: tool.parameters_json_schema for tool in info.function_tools}
            block = tools["get_token_state"]["properties"]["block_number"]
            assert block["minimum"] == 100 and block["maximum"] == 110
            assert tools["get_receipt"]["properties"]["tx_hash"]["const"] == TX
            assert tools["get_governance_source"]["properties"]["source_id"]["const"] == "proposal"
            return ModelResponse(parts=[ToolCallPart("get_receipt", {"tx_hash": TX})])
        return ModelResponse(parts=[ToolCallPart("final_report", report("receipt:1", "unresolved"))])
    outcome = asyncio.run(run_investigation(FunctionModel(choose), Backend(), request()))
    assert outcome.status == "completed", outcome.stop_reason


@pytest.mark.parametrize("model_name", ["deepseek-v3.2", "minimax-m2.7"])
def test_sixth_chat_completion_exposes_only_selectable_final_report(tmp_path, model_name):
    bodies = []
    choices = [
        ("get_receipt", {"tx_hash": TX}),
        ("get_transaction", {"tx_hash": TX}),
        ("get_governance_source", {"source_id": "proposal"}),
        ("get_token_state", {"token_address": TOKEN, "block_number": 100}),
        ("get_token_state", {"token_address": TOKEN, "block_number": 110}),
        ("final_report", report()),
    ]
    async def respond(http_request):
        body = json.loads(http_request.content)
        bodies.append(body)
        names = [item["function"]["name"] for item in body["tools"]]
        if len(bodies) < 6:
            assert set(names) == {"get_receipt", "get_transaction", "get_governance_source",
                                  "get_token_state", "final_report"}
        else:
            assert len(bodies) == 6
            assert names == ["final_report"]
            # Assert the actual SDK wire payload, rather than simulating a model
            # that ignores serialized tool-choice restrictions.
            assert body["tool_choice"] == "auto"
            assert body["messages"][-1]["role"] == "user"
            assert "final allowed model request" in body["messages"][-1]["content"]
        name, args = choices[len(bodies) - 1]
        assert body["stream"] is False
        return httpx.Response(200, json={"id": f"synthetic_{len(bodies)}", "object": "chat.completion",
            "created": 1, "model": model_name, "choices": [{"index": 0, "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": None, "tool_calls": [{"id": f"call_{len(bodies)}",
                "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}})
    async def task():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = create_gateway_model("synthetic-test-key", model_name=model_name, http_client=client)
            with models.override_allow_model_requests(True):
                return await run_investigation(model, Backend(), request(), paid_enabled=True,
                    ledger=BudgetLedger(tmp_path / "budget.sqlite3"), price_quotes={model_name: quote(model_name)})
    outcome = asyncio.run(task())
    assert outcome.status == "completed", outcome.stop_reason
    assert outcome.model_requests == len(bodies) == 6
    assert outcome.tool_attempts == 5
    assert outcome.report.classification == "governance_explained"


def test_outcome_callback_receives_detached_normal_result_once():
    snapshots = []
    outcome = asyncio.run(run_investigation(FunctionModel(dynamic_model), Backend(), request(),
                                            outcome_callback=snapshots.append))
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.status == "completed" and snapshot.model_requests == 3 and snapshot.tool_attempts == 2
    assert snapshot is not outcome
    snapshot.evidence[0].payload["status"] = "changed-by-observer"
    assert outcome.evidence[0].payload["status"] == "0x1"


def test_external_cancellation_preserves_public_callback_and_budget_counts(tmp_path):
    snapshots = []
    async def task():
        second_request_started = asyncio.Event()
        bodies = []
        async def respond(http_request):
            body = json.loads(http_request.content)
            bodies.append(body)
            if len(bodies) == 2:
                second_request_started.set()
                await asyncio.Future()
            return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 1,
                "model": "deepseek-v3.2", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
                    "function": {"name": "get_receipt", "arguments": json.dumps({"tx_hash": TX})}}]}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}})
        ledger = BudgetLedger(tmp_path / "budget.sqlite3")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = create_gateway_model("synthetic-test-key", http_client=client)
            with models.override_allow_model_requests(True):
                pending = asyncio.create_task(run_investigation(model, Backend(), request(), paid_enabled=True,
                    ledger=ledger, price_quotes={"deepseek-v3.2": quote("deepseek-v3.2")},
                    outcome_callback=snapshots.append))
                try:
                    await asyncio.wait_for(second_request_started.wait(), timeout=2)
                    pending.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await pending
                finally:
                    if not pending.done():
                        pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
        return ledger.snapshot(), len(bodies)
    budget, network_requests = asyncio.run(task())
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.status == "partial" and snapshot.stop_reason == "externally_cancelled"
    assert snapshot.model_requests == network_requests == budget["requests_reserved"] == 2
    assert snapshot.tool_attempts == 1 and len(snapshot.evidence) == 1
    assert snapshot.model_names == ["deepseek-v3.2", "deepseek-v3.2"]
    assert any(item["event"] == "tool_observed" for item in snapshot.trace)
    assert "synthetic-test-key" not in snapshot.model_dump_json()
