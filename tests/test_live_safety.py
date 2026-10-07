"""Paid wiring tested without real keys, account reads, or model requests."""

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from pydantic_ai import models

from cluetide import live
from cluetide.agent import AgentOutcome
from cluetide.budget import PriceQuote
from cluetide.budget import HARD_CAP_RMB, BudgetLedger


@pytest.fixture(autouse=True)
def isolated_paid_helpers(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "LOCAL_DATA", tmp_path)
    monkeypatch.setattr(live, "gateway_key", lambda: "synthetic-key-for-tests")
    monkeypatch.setattr(live, "paid_session_available", lambda: True)
    actual_client = httpx.AsyncClient
    def reject_external(request):
        raise AssertionError("Live safety tests must not contact external services")
    def client_factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(reject_external))
        return actual_client(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    async def balance(*args, **kwargs):
        return {"balance_micro_rmb": 5_000_000}
    async def price(model, **kwargs):
        return PriceQuote(model, Decimal("1.6"), Decimal("2.4"),
                          f"https://tokendance.space/portal/api/models/{model}/endpoints/stats",
                          datetime.now(timezone.utc), verified=True, source_sha256="a" * 64)
    monkeypatch.setattr(live, "read_gateway_balance", balance)
    monkeypatch.setattr(live, "fetch_official_price_quote", price)
    monkeypatch.setattr(live, "create_gateway_model", lambda *args, **kwargs: object())
    with models.override_allow_model_requests(False):
        yield


def test_preflight_failure_sends_no_model_request(monkeypatch):
    async def failed_balance(*args, **kwargs):
        raise RuntimeError("Synthetic private diagnostic must not enter public output")
    invoked = []
    async def forbidden_runtime(*args, **kwargs):
        invoked.append(True)
        raise AssertionError
    monkeypatch.setattr(live, "read_gateway_balance", failed_balance)
    monkeypatch.setattr(live, "run_investigation", forbidden_runtime)
    outcome = asyncio.run(live.run_paid(object(), object(), stop_event=None, deadline_seconds=1))
    assert outcome.status == "partial" and outcome.model_requests == 0
    assert outcome.stop_reason == "provider_preflight_or_transport_unavailable"
    assert "private diagnostic" not in outcome.model_dump_json()
    assert invoked == []


def test_balance_preflight_is_inside_total_deadline(monkeypatch):
    async def slow_balance(*args, **kwargs):
        await asyncio.sleep(1)
    monkeypatch.setattr(live, "read_gateway_balance", slow_balance)
    started = time.monotonic()
    outcome = asyncio.run(live.run_paid(object(), object(), stop_event=None, deadline_seconds=0.03))
    assert time.monotonic() - started < 0.3
    assert outcome.stop_reason == "deadline_exceeded" and outcome.model_requests == 0


def test_quote_time_is_deducted_from_agent_deadline(monkeypatch, tmp_path):
    async def balance(*args, **kwargs):
        await asyncio.sleep(0.01)
        return {"balance_micro_rmb": 5_000_000}
    original_price = live.fetch_official_price_quote
    async def price(*args, **kwargs):
        await asyncio.sleep(0.01)
        return await original_price(*args, **kwargs)
    observed = []
    async def runtime(*args, **kwargs):
        observed.append(kwargs)
        return AgentOutcome(status="partial", model_requests=2, stop_reason="synthetic")
    monkeypatch.setattr(live, "read_gateway_balance", balance)
    monkeypatch.setattr(live, "fetch_official_price_quote", price)
    monkeypatch.setattr(live, "run_investigation", runtime)
    outcome = asyncio.run(live.run_paid(object(), object(), stop_event=None, deadline_seconds=0.2))
    assert outcome.model_requests == 2
    assert 0 < observed[0]["limits"].deadline_seconds < 0.175
    assert observed[0]["ledger"].path == tmp_path / "model-budget.sqlite3"


def test_balance_limits_new_spending_and_preserves_shared_authorization(monkeypatch, tmp_path):
    ledger_path = tmp_path / "model-budget.sqlite3"
    original = BudgetLedger(ledger_path)
    current_quote = PriceQuote("deepseek-v3.2", Decimal("1.6"), Decimal("2.4"),
        "https://tokendance.space/portal/api/models/deepseek-v3.2/endpoints/stats",
        datetime.now(timezone.utc), verified=True, source_sha256="a" * 64)
    completed = original.reserve(current_quote, input_tokens=100000, output_tokens=1024)
    original.finish(completed, quote=current_quote, input_tokens=100000, output_tokens=1024)

    async def balance(*args, **kwargs):
        return {"balance_micro_rmb": 50000}

    async def runtime(*args, **kwargs):
        kwargs["ledger"].reserve(current_quote, input_tokens=1000, output_tokens=1024)
        return AgentOutcome(status="partial", model_requests=1, stop_reason="synthetic")

    monkeypatch.setattr(live, "read_gateway_balance", balance)
    monkeypatch.setattr(live, "run_investigation", runtime)
    outcome = asyncio.run(live.run_paid(object(), object(), stop_event=None, deadline_seconds=1))
    state = BudgetLedger(ledger_path).snapshot()
    assert outcome.model_requests == 1 and state["requests_reserved"] == 2
    assert state["cap_rmb"] == str(HARD_CAP_RMB)
    assert Decimal(state["reserved_rmb"]) > Decimal("0.05")


def test_stop_before_preflight_sends_no_account_or_model_requests(monkeypatch):
    async def forbidden_balance(*args, **kwargs):
        raise AssertionError("Pre-stopped tasks must not read the account")
    monkeypatch.setattr(live, "read_gateway_balance", forbidden_balance)
    async def run():
        stop = asyncio.Event()
        stop.set()
        return await live.run_paid(object(), object(), stop_event=stop, deadline_seconds=1)
    outcome = asyncio.run(run())
    assert outcome.status == "stopped" and outcome.stop_reason == "user_stop"
    assert outcome.model_requests == 0


def test_total_deadline_preserves_already_attempted_model_counts(monkeypatch):
    async def runtime(*args, **kwargs):
        try:
            await asyncio.sleep(1)
        finally:
            kwargs["outcome_callback"](AgentOutcome(status="partial", stop_reason="externally_cancelled",
                model_requests=1, tool_attempts=1, model_names=["deepseek-v3.2"],
                trace=[{"event": "model_request", "number": 1, "model": "deepseek-v3.2"}]))
    monkeypatch.setattr(live, "run_investigation", runtime)
    outcome = asyncio.run(live.run_paid(object(), object(), stop_event=None, deadline_seconds=0.03))
    assert outcome.stop_reason == "deadline_exceeded"
    assert outcome.model_requests == outcome.tool_attempts == 1
    assert outcome.model_names == ["deepseek-v3.2"] and outcome.trace[0]["event"] == "model_request"


@pytest.mark.asyncio
async def test_external_cancel_notifies_owner_and_retains_reservation(monkeypatch, tmp_path):
    entered = asyncio.Event()
    observed = []
    async def runtime(*args, **kwargs):
        quote = kwargs["price_quotes"]["deepseek-v3.2"]
        kwargs["ledger"].reserve(quote, input_tokens=100, output_tokens=100)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            kwargs["outcome_callback"](AgentOutcome(status="partial", stop_reason="externally_cancelled",
                model_requests=1, tool_attempts=1, model_names=["deepseek-v3.2"],
                trace=[{"event": "model_request", "number": 1, "model": "deepseek-v3.2"}]))
    monkeypatch.setattr(live, "run_investigation", runtime)
    task = asyncio.create_task(live.run_paid(object(), object(), stop_event=None,
                                            deadline_seconds=1, outcome_callback=observed.append))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(observed) == 1 and observed[0].model_requests == 1
    assert observed[0].stop_reason == "externally_cancelled"
    ledger = BudgetLedger(tmp_path / "model-budget.sqlite3")
    assert ledger.snapshot()["requests_reserved"] == 1
    assert Decimal(ledger.snapshot()["reserved_rmb"]) > 0


@pytest.fixture
def isolated_workbench(monkeypatch, tmp_path):
    from cluetide import app as module
    from cluetide.registry import LocalRegistry
    from cluetide.store import CaseStore
    monkeypatch.setattr(module, "LOCAL_DATA", tmp_path)
    monkeypatch.setattr(module, "store", CaseStore(tmp_path / "cases.sqlite3"))
    monkeypatch.setattr(module, "registry", LocalRegistry(tmp_path / "registry.json"))
    monkeypatch.setattr(module, "tasks", {})
    monkeypatch.setattr(module, "stops", {})
    monkeypatch.setattr(module, "mutation_lock", asyncio.Lock())
    monkeypatch.setattr(module, "paid_session_available", lambda: True)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["stop", "deadline"])
async def test_app_interruption_retains_counts_raw_and_cleans_state(isolated_workbench, monkeypatch, interruption):
    module = isolated_workbench
    entered = asyncio.Event()
    async def interrupted_paid(backend, request, *, stop_event, deadline_seconds, outcome_callback):
        backend.observations.append({"method": "eth_getTransactionReceipt", "params": [request.tx_hash],
                                     "result": {"transactionHash": request.tx_hash, "status": "0x1"}})
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            outcome_callback(AgentOutcome(status="partial", stop_reason="externally_cancelled",
                model_requests=2, tool_attempts=1, model_names=["deepseek-v3.2"],
                trace=[{"event": "model_request", "number": 2, "model": "deepseek-v3.2"}]))
    monkeypatch.setattr(module, "run_paid", interrupted_paid)
    async def failed_close(*args):
        raise RuntimeError("Synthetic close failure")
    monkeypatch.setattr(module.CachedRpc, "close", failed_close)
    if interruption == "deadline":
        actual_timeout = asyncio.timeout
        monkeypatch.setattr(module.asyncio, "timeout", lambda delay: actual_timeout(0.1 if delay == 180 else delay))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1:8765") as client:
        payload = {key: module.PRESET[key] for key in ("address", "token_address", "from_block", "to_block")}
        started = await client.post("/api/investigations", json={**payload, "agent_mode": "live"})
        case_id = started.json()["id"]
        await asyncio.wait_for(entered.wait(), 1)
        if interruption == "stop":
            response = await client.post(f"/api/investigations/{case_id}/stop")
            assert response.status_code == 200
        for _ in range(100):
            document = (await client.get(f"/api/investigations/{case_id}")).json()
            if document["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert document["status"] == ("stopped" if interruption == "stop" else "partial")
        assert document["agent"]["model_requests"] == 2
        assert document["agent"]["tool_attempts"] == 1
        assert document["agent"]["stop_reason"] == ("user_stop" if interruption == "stop" else "deadline_exceeded")
        assert document["agent"]["trace"][0]["number"] == 2
        assert document["evidence"]["raw"]["agent_rpc_observations"][0]["method"] == "eth_getTransactionReceipt"
        assert case_id not in module.tasks and case_id not in module.stops


@pytest.mark.asyncio
async def test_paid_api_preflight_failure_is_labeled_zero_requests(monkeypatch, tmp_path):
    from cluetide import app as module
    from cluetide.registry import LocalRegistry
    from cluetide.store import CaseStore
    monkeypatch.setattr(module, "LOCAL_DATA", tmp_path)
    monkeypatch.setattr(module, "store", CaseStore(tmp_path / "cases.sqlite3"))
    monkeypatch.setattr(module, "registry", LocalRegistry(tmp_path / "registry.json"))
    monkeypatch.setattr(module, "tasks", {})
    monkeypatch.setattr(module, "stops", {})
    monkeypatch.setattr(module, "mutation_lock", asyncio.Lock())
    monkeypatch.setattr(module, "paid_session_available", lambda: True)
    async def blocked(*args, **kwargs):
        return AgentOutcome(status="budget_exhausted", stop_reason="provider_balance_exhausted")
    monkeypatch.setattr(module, "run_paid", blocked)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1:8765") as client:
        payload = {key: module.PRESET[key] for key in ("address", "token_address", "from_block", "to_block")}
        started = await client.post("/api/investigations", json={**payload, "agent_mode": "live"})
        assert started.status_code == 202
        case_id = started.json()["id"]
        for _ in range(100):
            document = (await client.get(f"/api/investigations/{case_id}")).json()
            if document["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert document["status"] == "budget_exhausted"
        assert document["agent"]["model_requests"] == 0
        assert document["report"]["requested_agent_mode"] == "live"
        assert document["report"]["execution_mode"] == "Live model requested; no model request sent"
