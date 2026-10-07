"""Public-cache replay must not relabel historical observations as new reads."""

import asyncio
import copy
from pathlib import Path

import pytest

from cluetide.adapters import CachedRpc, InvestigationTools, load_case
from cluetide.evidence import TRANSFER_TOPIC, address_topic
from cluetide.rpc import RpcError
from cluetide.schemas import InvestigationRequest, MAX_UINT256


CASE_DIR = Path(__file__).resolve().parents[1] / "data" / "cases" / "uniswap93"
FOREIGN_TX = "0x" + "f" * 64
FOREIGN_ADDRESS = "0x" + "e" * 40


@pytest.fixture
def snapshot():
    result, _ = load_case(CASE_DIR)
    assert result, "The public case fixture must be available"
    return copy.deepcopy(result)


def get_request(snapshot):
    scope = snapshot["request"]
    return InvestigationRequest(address=scope["subject_address"], token_address=scope["token_address"],
                                from_block=scope["start_block"], to_block=scope["end_block"])


@pytest.mark.parametrize("method", ["eth_getTransactionReceipt", "eth_getTransactionByHash"])
def test_cache_rejects_foreign_transaction(snapshot, method):
    with pytest.raises(RpcError, match="does not cover this transaction"):
        asyncio.run(CachedRpc(snapshot).call(method, [FOREIGN_TX]))


def test_cache_rejects_uncaptured_block(snapshot):
    with pytest.raises(RpcError, match="does not cover this block"):
        asyncio.run(CachedRpc(snapshot).call("eth_getBlockByNumber", [hex(24106379), False]))


def test_cache_supply_requires_exact_historical_block(snapshot):
    rpc = CachedRpc(snapshot)
    contract_call = {"to": snapshot["request"]["token_address"], "data": "0x18160ddd"}
    before = asyncio.run(rpc.call("eth_call", [contract_call, hex(24106377)]))
    after = asyncio.run(rpc.call("eth_call", [contract_call, hex(24106378)]))
    assert before == after == snapshot["token_state"]["totalSupply_after"]
    with pytest.raises(RpcError, match="historical token state"):
        asyncio.run(rpc.call("eth_call", [contract_call, hex(24106388)]))


def test_cache_metadata_requires_captured_window_end_hash(snapshot):
    for key in list(snapshot["token_state"]):
        if key.endswith("_window_end") or key.startswith("metadata_"):
            del snapshot["token_state"][key]
    snapshot.pop("token_state_queries", None)
    params = [{"to": snapshot["request"]["token_address"], "data": "0x313ce567"},
              {"blockHash": snapshot["end_block"]["hash"], "requireCanonical": True}]
    with pytest.raises(RpcError, match="historical token state"):
        asyncio.run(CachedRpc(snapshot).call("eth_call", params))
    snapshot["token_state"]["metadata_block_number"] = 24106388
    snapshot["token_state"]["metadata_block_hash"] = snapshot["end_block"]["hash"]
    assert asyncio.run(CachedRpc(snapshot).call("eth_call", params)) == snapshot["token_state"]["decimals"]
    params[1]["blockHash"] = snapshot["case_block"]["hash"]
    with pytest.raises(RpcError):
        asyncio.run(CachedRpc(snapshot).call("eth_call", params))


@pytest.mark.parametrize("kind", ["foreign_token", "foreign_subject", "foreign_event", "partial_window"])
def test_cache_log_filter_must_match_captured_scope(snapshot, kind):
    scope = snapshot["request"]
    query = {"address": scope["token_address"], "fromBlock": hex(scope["start_block"]),
             "toBlock": hex(scope["end_block"]),
             "topics": [TRANSFER_TOPIC, address_topic(scope["subject_address"]), None]}
    if kind == "foreign_token":
        query["address"] = FOREIGN_ADDRESS
    elif kind == "foreign_subject":
        query["topics"][1] = address_topic(FOREIGN_ADDRESS)
    elif kind == "foreign_event":
        query["topics"][0] = "0x" + "e" * 64
    else:
        query["toBlock"] = hex(scope["end_block"] - 1)
    with pytest.raises(RpcError):
        asyncio.run(CachedRpc(snapshot).call("eth_getLogs", [query]))


def test_cache_exact_transfer_filter_preserves_incoming_outgoing(snapshot):
    scope = snapshot["request"]
    query = {"address": scope["token_address"], "fromBlock": hex(scope["start_block"]),
             "toBlock": hex(scope["end_block"]),
             "topics": [TRANSFER_TOPIC, address_topic(scope["subject_address"]), None]}
    assert len(asyncio.run(CachedRpc(snapshot).call("eth_getLogs", [query]))) == 1
    query["topics"] = [TRANSFER_TOPIC, None, address_topic(scope["subject_address"])]
    assert asyncio.run(CachedRpc(snapshot).call("eth_getLogs", [query])) == []


@pytest.mark.parametrize("receipt", [True, False])
@pytest.mark.parametrize("kind", ["foreign_hash", "out_of_window", "pending"])
def test_live_adapter_rejects_mismatched_returned_transaction(snapshot, receipt, kind):
    request = get_request(snapshot)
    tx_hash = snapshot["request"]["transaction_hash"]
    result = copy.deepcopy(snapshot["receipt" if receipt else "transaction"])
    if kind == "foreign_hash":
        result["transactionHash" if receipt else "hash"] = FOREIGN_TX
    elif kind == "out_of_window":
        result["blockNumber"] = hex(request.to_block + 1)
    else:
        result["blockNumber"] = None
    class ReturningRpc:
        async def call(self, method, params):
            return result
    backend = InvestigationTools(ReturningRpc(), request, [])
    with pytest.raises(RpcError):
        asyncio.run(backend.get_receipt(tx_hash) if receipt else backend.get_transaction(tx_hash))
    assert len(backend.observations) == 1  # Raw rejected response remains reviewable.


@pytest.mark.parametrize("result", [None, "0x01", "0x" + "f" * 65, "0x" + "g" * 64])
def test_token_getter_rejects_incomplete_or_invalid_uint256_word(snapshot, result):
    class ReturningRpc:
        async def call(self, method, params):
            return result
    request = get_request(snapshot)
    backend = InvestigationTools(ReturningRpc(), request, [])
    with pytest.raises(RpcError, match="complete uint256"):
        asyncio.run(backend.get_token_state(request.token_address, 24106378))
    assert len(backend.observations) == 1


def test_token_getter_preserves_max_uint256_as_decimal_string(snapshot):
    class ReturningRpc:
        async def call(self, method, params):
            return "0x" + format(MAX_UINT256, "064x")
    request = get_request(snapshot)
    result = asyncio.run(InvestigationTools(ReturningRpc(), request, []).get_token_state(request.token_address, 24106378))
    assert result["payload"]["total_supply_raw"] == str(MAX_UINT256)
