import asyncio

import pytest
from pydantic import ValidationError

from cluetide.collector import collect_transfers
from cluetide.evidence import TRANSFER_TOPIC, address_topic, decode_transfer_log, summarize_transfers
from cluetide.rpc import RpcError, validate_rpc_request
from cluetide.schemas import InvestigationRequest, MAX_UINT256


ADDRESS = "0x" + "a" * 40
TOKEN = "0x" + "b" * 40
OTHER = "0x" + "c" * 40
BLOCK_HASH = "0x" + "1" * 64
END_HASH = "0x" + "2" * 64
TX_HASH = "0x" + "3" * 64


def request(**overrides):
    values = dict(address=ADDRESS, token_address=TOKEN, from_block=100, to_block=110)
    values.update(overrides)
    return InvestigationRequest(**values)


def transfer(sender=ADDRESS, receiver=OTHER, amount=100_000_000 * 10**18, **overrides):
    values = {"address": TOKEN, "topics": [TRANSFER_TOPIC, address_topic(sender), address_topic(receiver)],
              "data": "0x" + format(amount, "064x"), "blockNumber": "0x69", "blockHash": BLOCK_HASH,
              "transactionHash": TX_HASH, "transactionIndex": "0x0", "logIndex": "0x0", "removed": False}
    values.update(overrides)
    return values


def abi_string(value):
    data = value.encode()
    return "0x" + (32).to_bytes(32, "big").hex() + len(data).to_bytes(32, "big").hex() + data.hex() + "00" * (-len(data) % 32)


class FixtureRpc:
    def __init__(self, outgoing=(), incoming=(), *, fail_incoming=False, finalized=200,
                 chain=1, metadata=True):
        self.outgoing, self.incoming = list(outgoing), list(incoming)
        self.fail_incoming, self.finalized, self.chain = fail_incoming, finalized, chain
        self.metadata = metadata
        self.calls = []

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId":
            return hex(self.chain)
        if method == "eth_getBlockByNumber":
            number = self.finalized if params[0] == "finalized" else int(params[0], 16)
            return {"number": hex(number), "hash": END_HASH}
        if method == "eth_getLogs":
            incoming = params[0]["topics"][1] is None
            if incoming and self.fail_incoming:
                raise RpcError("RPC returned an error (code -32000)")
            return self.incoming if incoming else self.outgoing
        if method == "eth_call":
            if not self.metadata:
                raise RpcError("RPC returned an error (code -32000)")
            assert params[1] == {"blockHash": END_HASH, "requireCanonical": True}
            selector = params[0]["data"]
            return "0x" + format(18, "064x") if selector == "0x313ce567" else abi_string("UNI" if selector == "0x95d89b41" else "Uniswap")
        raise AssertionError(method)


def test_request_rejects_bad_addresses_and_unbounded_window():
    with pytest.raises(ValidationError):
        request(address="0x123")
    with pytest.raises(ValidationError):
        request(to_block=3000)
    with pytest.raises(ValidationError):
        request(from_block=111)
    with pytest.raises(ValidationError):
        request(from_block=True)
    with pytest.raises(ValidationError):
        request(alert_threshold_raw=str(MAX_UINT256 + 1))
    with pytest.raises(ValidationError):
        request(from_block=2**53, to_block=2**53)
    with pytest.raises(ValidationError):
        request(chain_id=True)


def test_uint256_roundtrip_and_self_transfer_dual_direction():
    record = decode_transfer_log(transfer(receiver=ADDRESS, amount=MAX_UINT256), request(),
                                 direction="outgoing", observation_id="rpc:1")
    assert record.value_raw == str(MAX_UINT256)
    assert record.directions == ["in", "out"]
    assert summarize_transfers([record]) == {"transfer_count": 1, "incoming_raw": str(MAX_UINT256),
        "outgoing_raw": str(MAX_UINT256), "net_transfer_flow_raw": "0"}


@pytest.mark.parametrize("overrides", [
    {"removed": True}, {"removed": 0}, {"data": "0x01"}, {"blockNumber": "0x70"},
    {"address": OTHER}, {"blockHash": "0x01"}, {"logIndex": "0x00"},
])
def test_decoder_rejects_removed_malformed_and_out_of_window_logs(overrides):
    with pytest.raises(ValueError):
        decode_transfer_log(transfer(**overrides), request(), direction="outgoing", observation_id="rpc:1")


def test_deduplicates_self_transfer_and_records_both_rpc_observations():
    log = transfer(receiver=ADDRESS)
    evidence = asyncio.run(collect_transfers(request(), FixtureRpc([log, log], [log])))
    assert evidence.coverage.status == "complete"
    assert len(evidence.transfers) == len(evidence.alerts) == 1
    assert evidence.transfers[0].directions == ["in", "out"]
    assert len(evidence.transfers[0].observation_ids) == 2
    assert evidence.coverage.queries[0].returned_logs == 2
    assert evidence.metadata.decimals == 18 and evidence.metadata.symbol == "UNI"
    assert "not a finding of attack" in evidence.alerts[0].interpretation
    assert len(evidence.raw["rpc_observations"]) == 8


def test_empty_is_distinct_from_rpc_failure_and_missing_metadata():
    empty = asyncio.run(collect_transfers(request(), FixtureRpc(metadata=False)))
    partial = asyncio.run(collect_transfers(request(), FixtureRpc(fail_incoming=True)))
    assert empty.coverage.status == "empty"
    assert empty.metadata.status == "unavailable"
    assert empty.coverage.completed_queries == 2
    assert partial.coverage.status == "partial"
    assert partial.coverage.completed_queries == 1
    assert partial.coverage.queries[1].error


def test_nonfinalized_range_stops_before_getlogs():
    rpc = FixtureRpc(finalized=109)
    evidence = asyncio.run(collect_transfers(request(), rpc))
    assert evidence.coverage.status == "error"
    assert "exceeds the finalized" in evidence.coverage.issues[0]
    assert not any(method == "eth_getLogs" for method, _ in rpc.calls)


def test_chain_mismatch_is_error_and_does_not_scan_logs():
    rpc = FixtureRpc(chain=677)
    evidence = asyncio.run(collect_transfers(request(), rpc))
    assert evidence.coverage.status == "error"
    assert len(rpc.calls) == 1


def test_conflicting_duplicate_cannot_claim_complete_coverage():
    evidence = asyncio.run(collect_transfers(request(), FixtureRpc([transfer(), transfer(amount=1)])))
    assert evidence.coverage.status == "partial"
    assert evidence.coverage.queries[0].rejected_logs == 1


def test_one_failed_chunk_preserves_good_chunk_and_exact_requested_ranges():
    rpc = FixtureRpc([transfer()])
    original = rpc.call
    async def call(method, params):
        if method == "eth_getLogs" and params[0]["fromBlock"] == "0x6e":
            raise RpcError("RPC request timed out")
        return await original(method, params)
    rpc.call = call
    evidence = asyncio.run(collect_transfers(request(log_chunk_size=10), rpc))
    assert evidence.coverage.status == "partial"
    assert evidence.coverage.planned_queries == 4
    assert [(query.from_block, query.to_block) for query in evidence.coverage.queries] == [(100,109),(100,109),(110,110),(110,110)]
    assert len(evidence.transfers) == 1


@pytest.mark.parametrize("method,params,chain", [
    ("eth_sendRawTransaction", ["0x00"], 1), ("personal_sign", ["0x00"], 1),
    ("eth_getLogs", [{"address": TOKEN, "fromBlock": "0x64", "toBlock": "0x6e", "topics": [TRANSFER_TOPIC]}], 677),
    ("eth_getLogs", [{"address": TOKEN, "fromBlock": "latest", "toBlock": "latest", "topics": [TRANSFER_TOPIC]}], 1),
    ("eth_call", [{"to": TOKEN, "data": "0x18160ddd", "value": "0x1"}, "0x6e"], 1),
    ("eth_getBlockByNumber", ["latest", False], 1),
    ("eth_call", [{"to": TOKEN, "data": "0x18160ddd"}, {"blockHash": BLOCK_HASH, "requireCanonical": False}], 1),
])
def test_readonly_allowlist_rejects_unsafe_or_unbounded_reads(method, params, chain):
    with pytest.raises(ValueError):
        validate_rpc_request(method, params, chain_id=chain)


def test_ethereum_pinned_eth_call_remains_allowed():
    validate_rpc_request("eth_call", [{"to": TOKEN, "data": "0x18160ddd"}, "0x6e"], chain_id=1)


def test_metadata_deadline_does_not_erase_complete_empty_log_coverage():
    rpc = FixtureRpc()
    original = rpc.call
    async def call(method, params):
        if method == "eth_call":
            await asyncio.sleep(1)
        return await original(method, params)
    rpc.call = call
    evidence = asyncio.run(collect_transfers(request(), rpc, deadline_seconds=0.02))
    assert evidence.coverage.status == "empty"
    assert evidence.coverage.completed_queries == 2
    assert evidence.metadata.status == "unavailable"
    assert evidence.metadata.errors
    assert evidence.raw["rpc_observations"][-1]["error"] == "RPC read interrupted"


def test_collection_deadline_preserves_attempted_query_error():
    rpc = FixtureRpc()
    original = rpc.call
    async def call(method, params):
        if method == "eth_getLogs":
            await asyncio.sleep(1)
        return await original(method, params)
    rpc.call = call
    evidence = asyncio.run(collect_transfers(request(), rpc, deadline_seconds=0.02))
    assert evidence.coverage.status == "error"
    assert evidence.coverage.planned_queries == 2
    assert len(evidence.coverage.queries) == 1
    assert evidence.coverage.queries[0].error == "RPC read interrupted"


def test_excessive_chunk_plan_stops_before_getlogs():
    rpc = FixtureRpc()
    evidence = asyncio.run(collect_transfers(request(to_block=150, log_chunk_size=1), rpc))
    assert evidence.coverage.status == "error"
    assert "40-query" in evidence.coverage.issues[0]
    assert not any(method == "eth_getLogs" for method, _ in rpc.calls)
