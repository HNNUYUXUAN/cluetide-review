"""Application history reads retain captured anchors and use the tool budget."""
import asyncio

import httpx
import pytest
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from cluetide import app as module
from cluetide.adapters import CachedRpc, InvestigationTools
from cluetide.agent import run_investigation
from cluetide.case_catalog import load_catalog_case
from cluetide.registry import LocalRegistry
from cluetide.schemas import InvestigationRequest
from cluetide.store import CaseStore


@pytest.fixture
def isolated_state_reads(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "LOCAL_DATA", tmp_path)
    monkeypatch.setattr(module, "store", CaseStore(tmp_path / "cases.sqlite3"))
    monkeypatch.setattr(module, "registry", LocalRegistry(tmp_path / "registry.json"))
    monkeypatch.setattr(module, "tasks", {})
    monkeypatch.setattr(module, "stops", {})
    monkeypatch.setattr(module, "mutation_lock", asyncio.Lock())
    monkeypatch.setattr(module, "paid_session_available", lambda: True)

    def forbidden_network(*args, **kwargs):
        raise AssertionError("Application state-read regressions use captured public data")

    monkeypatch.setattr(module, "ReadOnlyRpcClient", forbidden_network)
    with models.override_allow_model_requests(False):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", ["uniswap93", "euler-20230313"])
async def test_dynamic_historical_reads_use_one_evidence_path(isolated_state_reads, monkeypatch, case_id):
    preset, snapshot, _ = load_catalog_case(case_id)
    event_block = int(snapshot["receipt"]["blockNumber"], 16)
    before, after = event_block - 1, event_block
    token = preset["token_address"]
    state_ids = [f"state:{token}:{block}" for block in (before, after)]
    calls = 0

    async def respond(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_receipt", {"tx_hash": preset["tx_hash"]})])
        if calls == 2:
            return ModelResponse(parts=[ToolCallPart("get_token_state", {
                "token_address": token, "block_number": block}) for block in (before, after)])
        return ModelResponse(parts=[ToolCallPart("final_report", {
            "summary": "The captured historical getter values are equal.", "classification": "unresolved",
            "claims": [{"text": "The two observed raw totalSupply values match.",
                        "evidence_ids": state_ids, "interpretation": False}],
            "assessments": [{"explanation_id": "supply_decrease", "explanation": "Net total supply decrease",
                "status": "refuted", "counter_evidence_ids": state_ids,
                "checks": ["Compare the two captured raw getter integers."]}], "limitations": [],
        })])

    async def simulated_model(backend, request, **kwargs):
        assert not any(item.get("kind") == "token_state" for item in request.initial_observations)
        return await run_investigation(FunctionModel(respond), backend, request,
            stop_event=kwargs["stop_event"], outcome_callback=kwargs["outcome_callback"])

    monkeypatch.setattr(module, "run_paid", simulated_model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                               base_url="http://127.0.0.1:8765") as client:
        started = await client.post("/api/investigations", json={
            **{key: preset[key] for key in ("address", "token_address", "from_block", "to_block")},
            "mode": "offline", "agent_mode": "live",
        })
        assert started.status_code == 202, started.text
        case_id = started.json()["id"]
        for _ in range(200):
            document = (await client.get(f"/api/investigations/{case_id}")).json()
            if document.get("versions"):
                break
            await asyncio.sleep(.01)
        else:
            raise AssertionError("Historical-state investigation was not published")
        assert document["status"] == "completed", document["agent"].get("stop_reason")
        assert document["agent"]["model_requests"] == 3
        assert document["agent"]["tool_attempts"] == 3
        assert document["report"]["conclusion"]["assessments"][0]["status"] == "refuted"
        state_evidence = [item for item in document["agent"]["evidence"] if item["kind"] == "token_state"]
        assert {item["evidence_id"] for item in state_evidence} == set(state_ids)
        observations = module.store.get(case_id)["evidence"]["raw"]["agent_rpc_observations"]
        for item in state_evidence:
            payload = item["payload"]
            recorded = observations[payload["rpc_observation_index"]]
            suffix = "before" if payload["block_number"] == before else "after"
            expected_hash = snapshot["token_state"].get("totalSupply_" + suffix + "_block_hash")
            if expected_hash is None and suffix == "after":
                expected_hash = snapshot["case_block"]["hash"]
            assert payload["block_hash"] == expected_hash
            assert recorded["params"][1] == ({"blockHash": expected_hash, "requireCanonical": True}
                if expected_hash else hex(payload["block_number"]))
        assert (await client.get(f"/api/investigations/{case_id}/bundle")).status_code == 200


def test_caller_anchor_cannot_replace_a_wrapped_captured_block():
    preset, snapshot, sources = load_catalog_case("euler-20230313")
    scope = InvestigationRequest(**{key: preset[key] for key in
                                   ("address", "token_address", "from_block", "to_block")})
    with pytest.raises(ValueError, match="captured block header"):
        InvestigationTools(module.RecordedRpc(CachedRpc(snapshot)), scope, sources,
            block_anchors={16817995: "0x" + "f" * 64})
