"""Every attempted Agent RPC read leaves a safe, reviewable observation."""

import asyncio
import copy
import json
from pathlib import Path

import pytest

from cluetide.adapters import InvestigationTools, load_case
from cluetide.rpc import RpcError
from cluetide.schemas import InvestigationRequest


ADDRESS = "0x" + "a" * 40
TOKEN = "0x" + "b" * 40
TX_HASH = "0x" + "c" * 64
SCOPE = InvestigationRequest(address=ADDRESS, token_address=TOKEN,
                             from_block=100, to_block=110)


@pytest.mark.parametrize("tool,arguments,method,params", [
    ("get_receipt", {"tx_hash": TX_HASH}, "eth_getTransactionReceipt", [TX_HASH]),
    ("get_transaction", {"tx_hash": TX_HASH}, "eth_getTransactionByHash", [TX_HASH]),
    ("get_token_state", {"token_address": TOKEN, "block_number": 105}, "eth_call",
     [{"to": TOKEN, "data": "0x18160ddd"}, "0x69"]),
])
@pytest.mark.parametrize("error_type", [RpcError, RuntimeError])
def test_failed_agent_rpc_is_retained_without_upstream_diagnostics(
        tool, arguments, method, params, error_type):
    private_diagnostics = "https://rpc.invalid/?key=synthetic-private-value Authorization: Bearer synthetic-private-value"

    class FailingRpc:
        async def call(self, requested_method, requested_params):
            assert (requested_method, requested_params) == (method, params)
            raise error_type(private_diagnostics)

    backend = InvestigationTools(FailingRpc(), SCOPE, [])
    with pytest.raises(error_type):
        asyncio.run(getattr(backend, tool)(**arguments))
    assert backend.observations == [{"method": method, "params": params,
                                    "error": "RPC read failed"}]
    assert "synthetic-private-value" not in json.dumps(backend.observations)
    assert "result" not in backend.observations[0]


def test_cancelled_agent_rpc_retains_interrupted_attempt():
    async def run():
        started = asyncio.Event()

        class BlockingRpc:
            async def call(self, method, params):
                started.set()
                await asyncio.Event().wait()

        backend = InvestigationTools(BlockingRpc(), SCOPE, [])
        task = asyncio.create_task(backend.get_receipt(TX_HASH))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert backend.observations == [{"method": "eth_getTransactionReceipt",
            "params": [TX_HASH], "error": "RPC read interrupted"}]

    asyncio.run(run())


def test_invalid_arguments_never_enter_raw_observations_or_transport():
    class NoRpc:
        async def call(self, method, params):
            raise AssertionError("Invalid hash must not reach transport")

    backend = InvestigationTools(NoRpc(), SCOPE, [])
    with pytest.raises(ValueError):
        asyncio.run(backend.get_receipt("https://private.invalid/?key=synthetic-private-value"))
    assert backend.observations == []


def test_successful_null_read_remains_distinct_from_failure():
    class EmptyRpc:
        async def call(self, method, params):
            return None

    backend = InvestigationTools(EmptyRpc(), SCOPE, [])
    evidence = asyncio.run(backend.get_receipt(TX_HASH))
    assert evidence["status"] == "empty"
    assert backend.observations == [{"method": "eth_getTransactionReceipt",
                                    "params": [TX_HASH], "result": None}]


@pytest.fixture
def captured_case():
    snapshot, _ = load_case(Path(__file__).resolve().parents[1] / "data" / "cases" / "uniswap93")
    scope = snapshot["request"]
    request = InvestigationRequest(address=scope["subject_address"], token_address=scope["token_address"],
                                   from_block=scope["start_block"], to_block=scope["end_block"])
    return snapshot, request, scope["transaction_hash"]


@pytest.mark.parametrize("receipt", [True, False])
@pytest.mark.parametrize("overrides", [
    {"blockNumber": "+0x16fd58a"}, {"blockNumber": "0x016fd58a"},
    {"blockNumber": 24106378}, {"blockHash": "malformed"}, {"blockHash": None},
])
def test_transaction_block_fields_must_be_canonical(captured_case, receipt, overrides):
    snapshot, scope, tx_hash = captured_case
    returned = copy.deepcopy(snapshot["receipt" if receipt else "transaction"])
    returned.update(overrides)

    class ReturningRpc:
        async def call(self, method, params):
            return returned

    backend = InvestigationTools(ReturningRpc(), scope, [])
    with pytest.raises(RpcError, match="confirmed-block schema"):
        asyncio.run(backend.get_receipt(tx_hash) if receipt else backend.get_transaction(tx_hash))
    assert backend.observations[0]["result"] == returned


@pytest.mark.parametrize("receipt", [True, False])
@pytest.mark.parametrize("field,value", [
    ("block_hash", "0x" + "f" * 64), ("block_number", 24106379),
])
def test_transaction_must_match_collected_transfer_block(captured_case, receipt, field, value):
    snapshot, scope, tx_hash = captured_case
    returned = copy.deepcopy(snapshot["receipt" if receipt else "transaction"])
    anchor = {"block_number": 24106378, "block_hash": snapshot["case_block"]["hash"]}
    anchor[field] = value

    class ReturningRpc:
        async def call(self, method, params):
            return returned

    backend = InvestigationTools(ReturningRpc(), scope, [], expected_transaction_blocks={tx_hash: anchor})
    with pytest.raises(RpcError, match="collected Transfer block"):
        asyncio.run(backend.get_receipt(tx_hash) if receipt else backend.get_transaction(tx_hash))
    assert backend.observations[0]["result"] == returned


@pytest.mark.parametrize("status", [None, "0x2", "0x01", 1, True])
def test_receipt_execution_status_requires_canonical_zero_or_one(captured_case, status):
    snapshot, scope, tx_hash = captured_case
    returned = copy.deepcopy(snapshot["receipt"])
    returned["status"] = status

    class ReturningRpc:
        async def call(self, method, params):
            return returned

    with pytest.raises(RpcError, match="status or log schema"):
        asyncio.run(InvestigationTools(ReturningRpc(), scope, []).get_receipt(tx_hash))


@pytest.mark.parametrize("invalid_logs", [None, {}, [None], [0], [{}], [None] * 5_001])
def test_receipt_logs_require_bounded_standard_objects(captured_case, invalid_logs):
    snapshot, scope, tx_hash = captured_case
    returned = copy.deepcopy(snapshot["receipt"])
    returned["logs"] = invalid_logs

    class ReturningRpc:
        async def call(self, method, params):
            return returned

    with pytest.raises(RpcError, match="status or log schema"):
        asyncio.run(InvestigationTools(ReturningRpc(), scope, []).get_receipt(tx_hash))


@pytest.mark.parametrize("field,value", [
    ("address", "bad"), ("topics", ["bad"]), ("data", "0x1"),
    ("blockNumber", "0x1"), ("blockHash", "0x" + "f" * 64),
    ("transactionHash", "0x" + "f" * 64), ("logIndex", "0x00"),
    ("transactionIndex", True), ("removed", True), ("removed", 0),
])
def test_receipt_log_fields_must_match_confirmed_transaction(captured_case, field, value):
    snapshot, scope, tx_hash = captured_case
    returned = copy.deepcopy(snapshot["receipt"])
    returned["logs"][0][field] = value

    class ReturningRpc:
        async def call(self, method, params):
            return returned

    with pytest.raises(RpcError, match="status or log schema"):
        asyncio.run(InvestigationTools(ReturningRpc(), scope, []).get_receipt(tx_hash))


def test_valid_receipt_and_transaction_keep_exact_public_payload(captured_case):
    snapshot, scope, tx_hash = captured_case
    anchor = {"block_number": 24106378, "block_hash": snapshot["case_block"]["hash"]}

    class ReturningRpc:
        async def call(self, method, params):
            return snapshot["receipt" if method == "eth_getTransactionReceipt" else "transaction"]

    backend = InvestigationTools(ReturningRpc(), scope, [], expected_transaction_blocks={tx_hash: anchor})
    receipt = asyncio.run(backend.get_receipt(tx_hash))
    transaction = asyncio.run(backend.get_transaction(tx_hash))
    assert receipt["status"] == transaction["status"] == "ok"
    assert receipt["payload"]["blockHash"] == anchor["block_hash"]
    assert receipt["payload"]["logs"] == [
        log for log in snapshot["receipt"]["logs"] if log["address"].lower() == scope.token_address]
    assert backend.observations == [
        {"method": "eth_getTransactionReceipt", "params": [tx_hash], "result": snapshot["receipt"]},
        {"method": "eth_getTransactionByHash", "params": [tx_hash], "result": snapshot["transaction"]}]
