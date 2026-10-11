"""Unsigned transaction preparation and read-only RPC verification boundaries."""

import copy
import hashlib
import json

import httpx
import pytest
from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

from cluetide.release_chain import (
    ACTIONS, GETTERS, ReleaseChainError, ReleaseChainService, _calldata,
    receipt_typed_data, recover_receipt_signer,
)

TASK = "0x" + hashlib.sha256(b"release task").hexdigest()
TARGET = "0x" + hashlib.sha256(b"demo target").hexdigest()
MANIFEST = "0x" + hashlib.sha256(b"manifest").hexdigest()
ARTIFACT = "0x" + hashlib.sha256(b"artifact").hexdigest()
RECEIPT = "0x" + hashlib.sha256(b"receipt").hexdigest()
ACCOUNT = "0x" + "11" * 20
PROPOSER = "0x" + "22" * 20
REVIEWER = "0x" + "33" * 20
EXECUTOR = "0x" + "44" * 20
SIGNER = "0x" + "55" * 20
CONTRACT = "0x" + "66" * 20
TX_HASH = "0x" + "77" * 32
BLOCK_HASH = "0x" + "88" * 32


def request(action="consume"):
    common = {"chain_id": 677, "action": action, "account": EXECUTOR, "contract_address": CONTRACT, "task_id": TASK}
    values = {
        "create_task": dict(proposer=PROPOSER, reviewer=REVIEWER, executor=EXECUTOR, receipt_signer=SIGNER, target_hash=TARGET),
        "propose_version": dict(parent_version_id=0, manifest_hash=MANIFEST, artifact_hash=ARTIFACT, deadline=1000),
        "approve": dict(version_id=1, manifest_hash=MANIFEST, artifact_hash=ARTIFACT, deadline=1000),
        "consume": dict(version_id=1, manifest_hash=MANIFEST, artifact_hash=ARTIFACT),
        "revoke": dict(version_id=1), "request_correction": dict(version_id=1),
        "record_receipt": dict(attempt_id=1, receipt_hash=RECEIPT, result=1, signature="0x" + "01" * 64 + "1b"),
    }
    if action == "deploy":
        return {"chain_id": 677, "action": action, "account": ACCOUNT}
    common.update(values[action])
    if action == "create_task":
        common["account"] = ACCOUNT
    return common


class FixtureRPC:
    def __init__(self, payload=None):
        self.payload = payload or request()
        self.calls = []
        self.service = ReleaseChainService(client_factory=lambda: httpx.Client(transport=httpx.MockTransport(self.handle)))
        self.expected, self.commitments = self.service._intent(self.payload, self.service.artifact())
        self.chain_id = 677
        self.head = 11
        self.pending = False
        self.failure = False
        self.estimate_error = False
        self.runtime = self.service.artifact().runtime
        self.balance = 10**18
        self.change_anchor = False
        self.block_reads = 0
        self.tx_changes = {}
        self.receipt_changes = {}
        self.task = [ACCOUNT, PROPOSER, REVIEWER, EXECUTOR, SIGNER, bytes.fromhex(TARGET[2:]), 1, 1, 1]
        self.version = [1, 0, bytes.fromhex(MANIFEST[2:]), bytes.fromhex(ARTIFACT[2:]), 1000, 4, 2, 1]
        self.attempt = [1, 1, 3, bytes(32), 0, 0]
        decision = {"propose_version": 0, "approve": 1, "revoke": 2, "request_correction": 3}.get(self.payload["action"])
        if decision is not None:
            self.version[5] = decision
            self.version[7] = 0
        if self.payload["action"] == "record_receipt":
            self.attempt[3:6] = [bytes.fromhex(RECEIPT[2:]), 1, 4]

    def handle(self, http_request):
        assert str(http_request.url) == "https://rpc.botchain.ai"
        body = json.loads(http_request.content)
        self.calls.append(body)
        method, params = body["method"], body["params"]
        assert method not in ("eth_sendTransaction", "eth_sendRawTransaction", "eth_sign", "personal_sign")
        if method == "eth_chainId":
            result = hex(self.chain_id)
        elif method == "eth_blockNumber":
            result = hex(self.head)
        elif method == "eth_getBlockByNumber":
            self.block_reads += 1
            result = {"number": params[0], "hash": BLOCK_HASH if not self.change_anchor or self.block_reads == 1 else TX_HASH, "timestamp": "0x64"}
        elif method == "eth_getCode":
            result = self.runtime
        elif method == "eth_gasPrice":
            result = "0x2"
        elif method == "eth_getBalance":
            result = hex(self.balance)
        elif method == "eth_estimateGas":
            if self.estimate_error:
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32000, "message": "private upstream detail"}})
            result = "0x186a0"
        elif method == "eth_call":
            data = params[0]["data"]
            result = "0x"
            for kind, (name, types, output, _) in GETTERS.items():
                arguments = [bytes.fromhex(TASK[2:])] + ([] if kind == "task" else [1])
                if data == _calldata(name, types, arguments):
                    result = "0x" + encode([output], [tuple(getattr(self, kind))]).hex()
        elif method == "eth_getTransactionByHash":
            result = {"hash": TX_HASH, "from": self.expected["from"], "to": self.expected.get("to"),
                "input": self.expected["data"], "value": "0x0", "chainId": "0x2a5", "blockNumber": "0xa", "blockHash": BLOCK_HASH}
            result.update(self.tx_changes)
        elif method == "eth_getTransactionReceipt":
            result = None if self.pending else {"transactionHash": TX_HASH, "from": self.expected["from"], "to": self.expected.get("to"),
                "status": "0x0" if self.failure else "0x1", "blockNumber": "0xa", "blockHash": BLOCK_HASH,
                "gasUsed": "0x186a0", "effectiveGasPrice": "0x2", "contractAddress": CONTRACT if self.payload["action"] == "deploy" else None}
            if result is not None:
                result.update(self.receipt_changes)
        else:
            raise AssertionError(method)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    def verify(self, minimum=2):
        return self.service.verify({"prepared": self.payload, "transaction_hash": TX_HASH, "min_confirmations": minimum})


@pytest.mark.parametrize("action", ["deploy", "create_task", "propose_version", "approve", "revoke", "request_correction", "consume", "record_receipt"])
def test_unsigned_preparation_calldata_and_real_fee_preflight(action):
    fixture = FixtureRPC(request(action))
    prepared = fixture.service.prepare(fixture.payload)
    assert prepared["ready_to_send"] is True
    assert prepared["transaction"]["value"] == "0x0"
    assert prepared["transaction"]["chainId"] == "0x2a5"
    assert prepared["fees"] == {"gas_limit": "120000", "gas_price_wei": "2", "estimated_max_fee_wei": "240000",
        "balance_wei": str(10**18), "sufficient_balance": True}
    assert prepared["read_block_hash"] == BLOCK_HASH
    artifact = json.loads(fixture.service.artifact_path.read_text(encoding="utf-8"))
    contract = Web3().eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    if action == "deploy":
        assert "to" not in prepared["transaction"]
        assert prepared["transaction"]["data"] == artifact["bytecode"]
    else:
        function, _ = contract.decode_function_input(prepared["transaction"]["data"])
        assert function.fn_name == ACTIONS[action][0]
    assert len(fixture.calls) <= 20


def test_preflight_failure_and_insufficient_balance_are_explicit():
    fixture = FixtureRPC()
    fixture.balance = 1
    prepared = fixture.service.prepare(fixture.payload)
    assert prepared["ready_to_send"] is False
    assert prepared["fees"]["sufficient_balance"] is False
    fixture.estimate_error = True
    with pytest.raises(ReleaseChainError, match="^rpc_error$"):
        fixture.service.prepare(fixture.payload)


@pytest.mark.parametrize("action", ["deploy", "create_task", "propose_version", "approve", "revoke", "request_correction", "consume"])
def test_exact_transaction_receipt_runtime_and_historical_getters(action):
    fixture = FixtureRPC(request(action))
    result = fixture.verify()
    assert result["status"] == "verified"
    assert result["block_number"] == "10"
    assert result["confirmations"] == "2"
    assert result["actual_fee_wei"] == "200000"
    assert result["contract_address"] == CONTRACT
    if action == "consume":
        assert result["onchain_ids"] == {"version_id": "1", "attempt_id": "1"}
        assert result["getter_state"]["task"]["target_hash"] == TARGET
    assert len(fixture.calls) <= 20


def test_pending_insufficient_confirmations_and_failed_transaction():
    fixture = FixtureRPC()
    fixture.pending = True
    assert fixture.verify()["status"] == "pending"
    fixture.pending = False
    assert fixture.verify(3)["status"] == "confirming"
    fixture.failure = True
    assert fixture.verify()["status"] == "failed"


@pytest.mark.parametrize("changes,error", [
    ({"from": ACCOUNT}, "transaction_intent_mismatch"), ({"to": ACCOUNT}, "transaction_intent_mismatch"),
    ({"input": "0x1234"}, "transaction_intent_mismatch"), ({"value": "0x1"}, "transaction_intent_mismatch"),
    ({"chainId": "0x1"}, "transaction_chain_mismatch"), ({"blockHash": TX_HASH}, "transaction_block_mismatch"),
    ({"hash": BLOCK_HASH}, "invalid_transaction"),
])
def test_unrelated_or_modified_transaction_is_rejected(changes, error):
    fixture = FixtureRPC()
    fixture.tx_changes = changes
    with pytest.raises(ReleaseChainError, match=error):
        fixture.verify()


def test_chain_runtime_anchor_and_commitment_mismatch_fail_closed():
    fixture = FixtureRPC()
    fixture.chain_id = 1
    with pytest.raises(ReleaseChainError, match="chain_id_mismatch"):
        fixture.verify()
    assert len(fixture.calls) == 1
    fixture = FixtureRPC()
    fixture.runtime = "0x1234"
    with pytest.raises(ReleaseChainError, match="contract_runtime_mismatch"):
        fixture.verify()
    fixture = FixtureRPC()
    fixture.change_anchor = True
    with pytest.raises(ReleaseChainError, match="canonical_block_changed"):
        fixture.verify()
    fixture = FixtureRPC()
    fixture.version[2] = bytes.fromhex(ARTIFACT[2:])
    with pytest.raises(ReleaseChainError, match="getter_commitment_mismatch"):
        fixture.verify()


def test_authorize_execution_binds_consumed_attempt_and_target():
    fixture = FixtureRPC()
    payload = dict(fixture.payload)
    payload.pop("action")
    payload.update(attempt_id=1, start_transaction_hash=TX_HASH, minimum_confirmations=2, target_hash=TARGET)
    result = fixture.service.authorize_execution(payload)
    assert result["status"] == "verified"
    assert result["attempt_id"] == "1"
    assert result["start_transaction_hash"] == TX_HASH
    payload["attempt_id"] = 2
    with pytest.raises(ReleaseChainError, match="execution_grant_mismatch"):
        fixture.service.authorize_execution(payload)
    payload["attempt_id"] = 1
    payload["target_hash"] = MANIFEST
    with pytest.raises(ReleaseChainError, match="execution_target_mismatch"):
        fixture.service.authorize_execution(payload)


def test_authorize_execution_checks_latest_receipt_and_preserves_used_grant():
    fixture = FixtureRPC()
    payload = dict(fixture.payload)
    payload.pop("action")
    payload.update(attempt_id=1, start_transaction_hash=TX_HASH)
    original = fixture.handle
    def handle(http_request):
        body = json.loads(http_request.content)
        if body["method"] == "eth_call" and body["params"][1] == "0xb":
            fixture.task[6] = 2
        return original(http_request)
    fixture.service.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(handle))
    result = fixture.service.authorize_execution(payload)
    assert result["latest_getter_state"]["task"]["version_count"] == "2"
    fixture = FixtureRPC()
    original = fixture.handle
    def reported(http_request):
        body = json.loads(http_request.content)
        if body["method"] == "eth_call" and body["params"][1] == "0xb":
            fixture.attempt[3:6] = [bytes.fromhex(RECEIPT[2:]), 1, 4]
        return original(http_request)
    fixture.service.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(reported))
    with pytest.raises(ReleaseChainError, match="execution_already_reported"):
        fixture.service.authorize_execution(payload)


def test_read_uses_bounded_getters_and_no_logs_or_broadcast():
    fixture = FixtureRPC()
    state = fixture.service.read(dict(chain_id=677, contract_address=CONTRACT, task_id=TASK, version_id=1, attempt_id=1))
    assert state["getter_state"]["attempt"]["version_id"] == "1"
    assert all(body["method"] not in ("eth_getLogs", "eth_sendRawTransaction") for body in fixture.calls)
    assert len(fixture.calls) == 8


def test_receipt_recovery_and_getter_verification_use_preset_signer():
    signer = Account.create()
    payload = request("record_receipt")
    typed = receipt_typed_data(677, CONTRACT, TASK, 1, 1, RECEIPT, 1)
    payload["signature"] = "0x" + signer.sign_message(encode_typed_data(full_message=typed)).signature.hex()
    fixture = FixtureRPC(payload)
    fixture.task[4] = signer.address.lower()
    result = fixture.verify()
    assert result["status"] == "verified"
    assert result["receipt_signer"] == signer.address.lower()
    fixture.task[4] = SIGNER
    with pytest.raises(ReleaseChainError, match="receipt_signer_mismatch"):
        fixture.verify()
    assert recover_receipt_signer(968, CONTRACT, TASK, 1, 1, RECEIPT, 1, payload["signature"]) != signer.address.lower()


@pytest.mark.parametrize("changes", [{"chain_id": True}, {"chain_id": 1}, {"account": "0x0"}, {"task_id": "0x" + "00" * 32},
    {"version_id": True}, {"version_id": "01"}, {"rpc_url": "https://attacker.invalid"}, {"data": "0xdeadbeef"}])
def test_untrusted_prepare_inputs_are_rejected_before_rpc(changes):
    fixture = FixtureRPC()
    payload = dict(fixture.payload)
    payload.update(changes)
    with pytest.raises(ReleaseChainError):
        fixture.service.prepare(payload)
    assert fixture.calls == []


def test_artifact_source_integrity_is_checked(tmp_path):
    fixture = FixtureRPC()
    source = tmp_path / "source.sol"
    source.write_text("changed source", encoding="utf-8")
    service = ReleaseChainService(source_path=source)
    with pytest.raises(ReleaseChainError, match="artifact_integrity_error"):
        service.networks()


def test_rpc_budget_and_response_size_remain_bounded():
    from cluetide.bot import BotRPC

    fixture = FixtureRPC()
    with httpx.Client(transport=httpx.MockTransport(fixture.handle)) as client:
        rpc = BotRPC(677, client, max_calls=1)
        rpc.verify_chain()
        with pytest.raises(ReleaseChainError, match="rpc_limit_reached"):
            rpc.verify_chain()
        with pytest.raises(ReleaseChainError, match="forbidden_rpc_method"):
            rpc.request("eth_sendRawTransaction", ["0x1234"])
    def oversized(request):
        return httpx.Response(200, content=b" " * 262145)
    with httpx.Client(transport=httpx.MockTransport(oversized)) as client:
        with pytest.raises(ReleaseChainError, match="rpc_response_too_large"):
            BotRPC(677, client).verify_chain()
