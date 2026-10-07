"""Cross-case public cache identities, state anchors and source roles."""
import asyncio
import copy
import hashlib
import json

import pytest

from cluetide import case_catalog
from cluetide.adapters import CachedRpc, InvestigationTools
from cluetide.collector import collect_transfers
from cluetide.evidence import TRANSFER_TOPIC, address_topic
from cluetide.rpc import RpcError
from cluetide.schemas import InvestigationRequest


def request(preset):
    return InvestigationRequest(**{key: preset[key] for key in (
        "address", "token_address", "from_block", "to_block", "alert_threshold_raw")})


@pytest.mark.parametrize("case_id", ["uniswap93", "euler-20230313"])
def test_catalog_identity_and_exact_collection(case_id):
    preset, snapshot, sources = case_catalog.load_catalog_case(case_id)
    scope = request(preset)
    assert case_catalog.match_catalog_case(scope) == preset
    evidence = asyncio.run(collect_transfers(scope, CachedRpc(snapshot)))
    assert evidence.coverage.status == "complete"
    assert evidence.metadata.status == "complete"
    assert evidence.metadata.symbol == preset["token_symbol"]
    assert {transfer.transaction_hash for transfer in evidence.transfers} == {preset["tx_hash"]}
    assert len(evidence.transfers) == (1 if case_id == "uniswap93" else 3)
    assert all(source["category"] == preset["source_category"] for source in sources)


def test_catalog_copies_and_loader_preserve_source_bytes():
    before = (case_catalog.CASE_ROOT / "uniswap93" / "sources.json").read_bytes()
    first = case_catalog.list_cases()
    first[0]["address"] = "modified"
    assert case_catalog.list_cases()[0]["address"] != "modified"
    _, snapshot, sources = case_catalog.load_catalog_case("uniswap93")
    snapshot["request"]["subject_address"] = "modified"
    sources[0]["category"] = "modified"
    assert case_catalog.load_catalog_case("uniswap93")[2][0]["category"] == "governance"
    assert (case_catalog.CASE_ROOT / "uniswap93" / "sources.json").read_bytes() == before


@pytest.mark.parametrize("change", [
    {"chain_id": 677}, {"chain_id": True}, {"from_block": True},
    {"to_block": 16817998}, {"address": "0x" + "f" * 40},
    {"token_address": "0x" + "f" * 40},
])
def test_catalog_matching_requires_exact_public_scope(change):
    preset = case_catalog.list_cases()[1]
    assert case_catalog.match_catalog_case({**preset, **change}) is None


def test_matching_allows_configured_alert_threshold():
    preset = case_catalog.list_cases()[1]
    assert case_catalog.match_catalog_case({**preset, "alert_threshold_raw": "1"}) == preset


@pytest.mark.parametrize("case_id", ["../uniswap93", "unknown", "uniswap93/rpc.json"])
def test_unknown_case_cannot_select_arbitrary_path(case_id):
    with pytest.raises(ValueError, match="Unknown public case"):
        case_catalog.load_catalog_case(case_id)


def test_loader_refuses_conflicting_snapshot(tmp_path, monkeypatch):
    _, snapshot, sources = case_catalog.load_catalog_case("euler-20230313")
    target = tmp_path / "euler-20230313"
    target.mkdir()
    snapshot["request"]["transaction_hash"] = "0x" + "f" * 64
    (target / "rpc.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (target / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
    monkeypatch.setattr(case_catalog, "CASE_ROOT", tmp_path)
    with pytest.raises(ValueError, match="catalog identity"):
        case_catalog.load_catalog_case("euler-20230313")


@pytest.mark.parametrize("case_id", ["uniswap93", "euler-20230313"])
def test_cache_refuses_window_and_transaction_relabelling(case_id):
    preset, snapshot, _ = case_catalog.load_catalog_case(case_id)
    rpc = CachedRpc(snapshot)
    query = {"address": preset["token_address"], "fromBlock": hex(preset["from_block"]),
             "toBlock": hex(preset["to_block"] - 1),
             "topics": [TRANSFER_TOPIC, address_topic(preset["address"]), None]}
    with pytest.raises(RpcError, match="log window"):
        asyncio.run(rpc.call("eth_getLogs", [query]))
    with pytest.raises(RpcError, match="transaction"):
        asyncio.run(rpc.call("eth_getTransactionReceipt", ["0x" + "f" * 64]))
    with pytest.raises(RpcError, match="block"):
        asyncio.run(rpc.call("eth_getBlockByNumber", [hex(preset["to_block"] + 1), False]))


def test_euler_state_getters_use_exact_hashes_and_raw_observation_indices():
    preset, snapshot, sources = case_catalog.load_catalog_case("euler-20230313")
    backend = InvestigationTools(CachedRpc(snapshot), request(preset), sources)
    for index, suffix in enumerate(("before", "after")):
        key = "totalSupply_" + suffix
        block = snapshot["token_state"][key + "_block_number"]
        result = asyncio.run(backend.get_token_state(preset["token_address"], block))
        payload = result["payload"]
        assert payload["anchor_status"] == "hash_pinned"
        assert payload["block_hash"] == snapshot["token_state"][key + "_block_hash"]
        assert payload["rpc_observation_index"] == index
        assert payload["total_supply_raw"] == str(int(snapshot["token_state"][key], 16))
        assert backend.observations[index]["params"][1] == {"blockHash": payload["block_hash"], "requireCanonical": True}
    with pytest.raises(RpcError, match="historical token state"):
        asyncio.run(backend.rpc.call("eth_call", [{"to": preset["token_address"], "data": "0x18160ddd"},
            {"blockHash": "0x" + "f" * 64, "requireCanonical": True}]))


def test_missing_uniswap_before_header_is_explicit():
    preset, snapshot, sources = case_catalog.load_catalog_case("uniswap93")
    backend = InvestigationTools(CachedRpc(snapshot), request(preset), sources)
    result = asyncio.run(backend.get_token_state(preset["token_address"], 24106377))
    assert result["payload"]["block_hash"] is None
    assert result["payload"]["anchor_status"] == "hash_unavailable"
    assert result["payload"]["block_selection"] == "number"
    assert backend.observations[0]["params"][1] == hex(24106377)


def test_public_incident_sources_keep_distinct_role():
    for case_id, expected in (("uniswap93", "governance_source"), ("euler-20230313", "public_context_source")):
        preset, snapshot, sources = case_catalog.load_catalog_case(case_id)
        backend = InvestigationTools(CachedRpc(snapshot), request(preset), sources)
        result = asyncio.run(backend.get_governance_source(sources[0]["source_id"]))
        assert result["kind"] == expected
        assert result["payload"]["source_id"] == sources[0]["source_id"]


def test_conflicting_captured_block_hash_is_refused():
    preset, snapshot, sources = case_catalog.load_catalog_case("euler-20230313")
    conflicting = copy.deepcopy(snapshot["case_block"])
    conflicting["hash"] = "0x" + "f" * 64
    snapshot["block_headers"] = {"case": conflicting}
    with pytest.raises(RpcError, match="headers conflict"):
        InvestigationTools(CachedRpc(snapshot), request(preset), sources)


def test_euler_fixture_manifest_and_source_excerpts_match():
    directory = case_catalog.case_directory("euler-20230313")
    manifest = json.loads((directory / "fixture-manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        payload = (directory / entry["path"]).read_bytes()
        assert len(payload) == entry["bytes"]
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
    for source in case_catalog.load_catalog_case("euler-20230313")[2]:
        assert hashlib.sha256((directory / source["excerpt_file"]).read_bytes()).hexdigest() == source["excerpt_sha256"]
