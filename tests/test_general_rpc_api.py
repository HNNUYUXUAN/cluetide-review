import asyncio
import copy
import hashlib
import json

import httpx
import pytest

from cluetide import app as module
from cluetide.adapters import CachedRpc, load_case
from cluetide.collection_report import empty_window_outcome
from cluetide.bundles import export_bundle, import_bundle
from cluetide.collector import collect_transfers
from cluetide.registry import LocalRegistry
from cluetide.schemas import InvestigationRequest
from cluetide.store import CaseStore


@pytest.fixture
def isolated_app(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "LOCAL_DATA", tmp_path)
    monkeypatch.setattr(module, "store", CaseStore(tmp_path / "cases.sqlite3"))
    monkeypatch.setattr(module, "registry", LocalRegistry(tmp_path / "registry.json"))
    monkeypatch.setattr(module, "tasks", {})
    monkeypatch.setattr(module, "stops", {})
    monkeypatch.setattr(module, "mutation_lock", asyncio.Lock())
    monkeypatch.setattr(module, "paid_session_available", lambda: False)
    return module.app


@pytest.mark.parametrize("changes", [
    {"endpoint": "http://127.0.0.1/private"}, {"source_url": "https://example.test"},
    {"from_block": True}, {"to_block": "24106388"}, {"chain_id": True},
    {"token_address": "https://example.test"}, {"address": "0x01"},
    {"from_block": 24106388, "to_block": 24106368},
    {"from_block": 0, "to_block": 2000}, {"log_chunk_size": 0},
    {"alert_threshold_raw": str(1 << 256)}, {"alert_threshold_raw": "01"},
])
@pytest.mark.asyncio
async def test_invalid_general_input_never_constructs_rpc(isolated_app, monkeypatch, changes):
    calls = []
    def forbid(*args, **kwargs):
        calls.append(1)
        raise AssertionError("Rejected inputs cannot construct a network client")
    monkeypatch.setattr(module, "ReadOnlyRpcClient", forbid)
    fields = {key: module.PRESET[key] for key in ("address", "token_address", "from_block", "to_block")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post("/api/investigations", json={**fields, "mode": "rpc", **changes})
        assert response.status_code == 422
    assert calls == [] and module.store.list() == []


@pytest.mark.asyncio
async def test_generic_empty_rpc_collection_exports_deterministic_report(isolated_app, monkeypatch):
    snapshot, _ = load_case(module.CASE_DIR)
    request = InvestigationRequest(address="0x0000000000000000000000000000000000001111", token_address=module.PRESET["token_address"], from_block=24106377, to_block=24106379)
    calls = []
    class EmptyRpc(CachedRpc):
        async def call(self, method, params):
            calls.append((method, params))
            if method == "eth_getLogs":
                return []
            if method == "eth_getBlockByNumber" and params[0] != "finalized":
                return {"number": params[0], "hash": "0x" + "12" * 32}
            if method == "eth_call":
                return "0x" + "0" * 63 + "0"
            return await super().call(method, params)
    monkeypatch.setattr(module, "ReadOnlyRpcClient", lambda endpoint: EmptyRpc(snapshot))
    def no_cache(*args):
        raise AssertionError("RPC mode must not construct the fixture adapter")
    monkeypatch.setattr(module, "CachedRpc", no_cache)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post("/api/investigations", json={**request.model_dump(), "mode": "rpc", "agent_mode": "offline"})
        case_id = response.json()["id"]
        for _ in range(200):
            document = (await client.get("/api/investigations/" + case_id)).json()
            if document["status"] != "running":
                break
            await asyncio.sleep(.01)
        assert document["status"] == "completed", document
        assert document["evidence"]["coverage"]["status"] == "empty"
        assert document["evidence"]["sources"] == []
        assert document["evidence"]["raw"]["capture_mode"] == "live_read_only"
        assert document["agent"]["model_requests"] == document["agent"]["tool_attempts"] == 0
        assert document["report"]["execution_mode"] == "Deterministic empty-window report; no model request sent"
        bundle = await client.get(f"/api/investigations/{case_id}/bundle?revision=1")
        assert bundle.status_code == 200
        imported = await client.post("/api/bundles/import", files={"file": ("empty.zip", bundle.content, "application/zip")})
        assert imported.status_code == 200
    assert sum(method == "eth_getLogs" for method, _ in calls) == 2


@pytest.mark.asyncio
async def test_future_window_stops_before_log_query():
    class FutureRpc:
        def __init__(self):
            self.calls = []
        async def call(self, method, params):
            self.calls.append(method)
            if method == "eth_chainId":
                return "0x1"
            return {"number": "0x64", "hash": "0x" + "ab" * 32}
    rpc = FutureRpc()
    request = InvestigationRequest(address=module.PRESET["address"], token_address=module.PRESET["token_address"], from_block=100, to_block=101)
    evidence = await collect_transfers(request, rpc)
    assert evidence.coverage.status == "error" and not evidence.transfers
    assert rpc.calls == ["eth_chainId", "eth_getBlockByNumber"]
    with pytest.raises(ValueError):
        empty_window_outcome(evidence)


@pytest.mark.asyncio
async def test_valid_hash_dangling_claim_is_structurally_flagged(isolated_app, tmp_path):
    original = module.ROOT / "data/demo/bundles/real-acceptance-v1.zip"
    original_bytes = original.read_bytes()
    verified = import_bundle(original)
    report = copy.deepcopy(verified.report)
    report["conclusion"]["claims"][0]["evidence_ids"] = ["invented:unobserved"]
    altered = tmp_path / "dangling.zip"
    export_bundle({**verified.evidence, "raw": verified.raw}, report, verified.report_markdown, altered)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post("/api/bundles/import", files={"file": ("dangling.zip", altered.read_bytes(), "application/zip")})
        assert response.status_code == 200
        result = response.json()
        assert result["validation"]["verified"] is True
        assert result["citation_validation"]["status"] == "needs_review"
        assert any(issue["code"] == "missing_claim_evidence" for issue in result["citation_validation"]["issues"])
        assert result["report"] == report
        assert result["manifest_hash"] != "16dd6aa3ce6bd46705864fc71ba52b28339746fb7c039724389f349e09d76a69"
    assert original.read_bytes() == original_bytes
    assert hashlib.sha256(original_bytes).hexdigest() == "34a493340d54637a969ed5e8413f7180f2091c2a26a7cca357a884902a2b13df"


@pytest.mark.asyncio
async def test_conflicting_blocks_for_same_transaction_are_partial():
    snapshot, _ = load_case(module.CASE_DIR)
    class ConflictingRpc(CachedRpc):
        async def call(self, method, params):
            result = await super().call(method, params)
            if method == "eth_getLogs" and result:
                other = copy.deepcopy(result[0])
                other["blockHash"] = "0x" + "ef" * 32
                return [*result, other]
            return result
    request = InvestigationRequest(**{key: module.PRESET[key] for key in ("address", "token_address", "from_block", "to_block")})
    evidence = await collect_transfers(request, ConflictingRpc(snapshot))
    assert evidence.coverage.status == "partial"
    assert len(evidence.transfers) == 1
    assert evidence.coverage.queries[0].rejected_logs == 1
    assert len(evidence.raw["rpc_observations"][3]["result"]) == 2
