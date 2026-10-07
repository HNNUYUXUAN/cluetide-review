"""Unsigned BOT plans execute actual artifact bytes only on local Py-EVM."""

import ast
from collections.abc import Mapping
import copy
import hashlib
import json

import httpx
import pytest
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from eth_utils import keccak
from fastapi import FastAPI
from web3 import EthereumTesterProvider, Web3

from cluetide.bot import BotError, BotService
from cluetide.bot_api import create_bot_router
from cluetide.registry import case_id_bytes
from test_bot import CASE_ID, saved


def rpc_json(value):
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return hex(value)
    if isinstance(value, Mapping):
        return {key: rpc_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [rpc_json(child) for child in value]
    raise TypeError(type(value).__name__)


def evm_tx(transaction):
    result = {key: value for key, value in transaction.items() if key != "chainId"}
    for key in ("from", "to"):
        if key in result:
            result[key] = Web3.to_checksum_address(result[key])
    for key in ("gas", "gasPrice", "value"):
        if key in result:
            result[key] = int(result[key], 16)
    return result


class LocalEVMNode:
    """Translate fixed read-only HTTP requests into an in-process tester."""

    def __init__(self, web3):
        self.web3 = web3
        self.calls = []

    def handle(self, req):
        payload = json.loads(req.content)
        self.calls.append(payload)
        method, params = payload["method"], payload["params"]
        assert method not in ("eth_sendTransaction", "eth_sendRawTransaction", "eth_accounts", "eth_getLogs")
        w3 = self.web3
        body = {"jsonrpc": "2.0", "id": payload["id"]}
        try:
            if method == "eth_chainId":
                value = 968
            elif method == "eth_blockNumber":
                value = w3.eth.block_number
            elif method == "eth_gasPrice":
                value = w3.eth.gas_price
            elif method == "eth_getBalance":
                value = w3.eth.get_balance(Web3.to_checksum_address(params[0]), int(params[1], 16))
            elif method == "eth_estimateGas":
                assert params[1] == hex(w3.eth.block_number)
                value = w3.eth.estimate_gas(evm_tx(params[0]))
            elif method == "eth_getCode":
                value = w3.eth.get_code(Web3.to_checksum_address(params[0]), int(params[1], 16))
            elif method == "eth_call":
                value = w3.eth.call(evm_tx(params[0]), int(params[1], 16))
            elif method == "eth_getTransactionReceipt":
                value = w3.eth.get_transaction_receipt(params[0])
            elif method == "eth_getTransactionByHash":
                value = w3.eth.get_transaction(params[0])
            elif method == "eth_getBlockByNumber":
                assert params[1] is False
                value = w3.eth.get_block(int(params[0], 16))
            else:
                raise AssertionError(method)
            body["result"] = rpc_json(value)
        except TransactionFailed as exc:
            message = str(exc)
            revert = None
            if "execution reverted: " in message:
                try:
                    revert = ast.literal_eval(message.split("execution reverted: ", 1)[1])
                except (ValueError, SyntaxError):
                    pass
            body["error"] = {"code": -32000, "message": "execution reverted",
                             "data": "0x" + revert.hex() if isinstance(revert, bytes) else "0x"}
        return httpx.Response(200, json=body)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle), trust_env=False)


@pytest.fixture
def evm_service(saved):
    tester = EthereumTester(backend=PyEVMBackend())
    provider = EthereumTesterProvider(tester)
    assert type(provider) is EthereumTesterProvider
    web3 = Web3(provider)
    node = LocalEVMNode(web3)
    svc = BotService(lambda case: saved if case == CASE_ID else None, client_factory=node.client)
    author, reviewer = web3.eth.accounts[:2]
    return svc, tester, web3, node, author, reviewer


def submit_plan(fixture, prepared):
    svc, tester, web3, _, _, _ = fixture
    plan = svc.prepare(prepared)
    assert plan["transaction"]["gas"]
    transaction_hash = web3.eth.send_transaction(evm_tx(plan["transaction"]))
    receipt = web3.eth.wait_for_transaction_receipt(transaction_hash)
    assert receipt.status == 1
    tester.mine_blocks(2)
    verified = svc.verify({"chain_id": 968, "transaction_hash": "0x" + transaction_hash.hex(),
                           "prepared": prepared, "minimum_confirmations": 3})
    assert verified["status"] == "verified", verified
    return verified


def deploy(fixture):
    return submit_plan(fixture, {"chain_id": 968, "action": "deploy", "account": fixture[4]})["contract_address"]


def test_public_plans_deploy_create_review_append_and_getter_mapping(evm_service, saved):
    svc, _, web3, node, author, reviewer = evm_service
    address = deploy(evm_service)
    artifact = json.loads(svc.artifact_path.read_text(encoding="utf-8"))
    contract = web3.eth.contract(address=Web3.to_checksum_address(address), abi=artifact["abi"])
    # A different case consumes global version 1. Local version 41 therefore
    # maps to actual global version 2 rather than borrowing the local ID.
    tx = contract.functions.createCase(hashlib.sha256(b"other-case").digest(), hashlib.sha256(b"other-evidence").digest(),
                                       "cluetide.evidence.v1", "").transact({"from": author})
    assert web3.eth.wait_for_transaction_receipt(tx).status == 1
    create = {"chain_id": 968, "action": "create_case", "account": author, "contract_address": address,
              "case_id": CASE_ID, "local_version_id": 41}
    first = submit_plan(evm_service, create)
    assert first["onchain_ids"]["version_id"] == "2"
    assert first["getter_state"]["version"]["content_hash"] == saved["versions"][0]["content_hash"]
    review = {**create, "action": "add_review", "account": reviewer, "onchain_version_id": "2", "local_review_id": 21}
    reviewed = submit_plan(evm_service, review)
    assert reviewed["onchain_ids"] == {"version_id": "2", "review_id": "1"}
    assert reviewed["getter_state"]["review"]["review_hash"] == saved["reviews"][0]["review_hash"]
    append = {**create, "action": "append_version", "local_version_id": 88, "onchain_version_id": "2"}
    second = submit_plan(evm_service, append)
    assert second["onchain_ids"]["version_id"] == "3"
    assert second["getter_state"]["version"]["parent_version_id"] == "2"
    assert second["getter_state"]["case"]["head_version_id"] == "3"
    assert second["getter_state"]["version"]["content_hash"] == saved["versions"][1]["content_hash"]
    state = svc.read({"chain_id": 968, "contract_address": address, "case_id": CASE_ID,
                      "version_id": "2", "review_id": "1"})
    assert state["getter_state"]["review"]["content_hash"] == saved["versions"][0]["content_hash"]
    assert state["getter_state"]["case"]["head_version_id"] == "3"
    assert max(call["id"] for call in node.calls) <= 20


def test_actual_chain_author_stale_parent_and_exact_review_binding(evm_service, saved):
    svc, _, _, _, author, reviewer = evm_service
    address = deploy(evm_service)
    create = {"chain_id": 968, "action": "create_case", "account": author, "contract_address": address,
              "case_id": CASE_ID, "local_version_id": 41}
    first = submit_plan(evm_service, create)
    parent = first["onchain_ids"]["version_id"]
    append = {**create, "action": "append_version", "local_version_id": 88, "onchain_version_id": parent}
    with pytest.raises(BotError, match="unauthorized_author"):
        svc.prepare({**append, "account": reviewer})
    with pytest.raises(BotError, match="case_exists"):
        svc.prepare(create)
    second = submit_plan(evm_service, append)
    with pytest.raises(BotError, match="stale_parent"):
        svc.prepare(append)
    with pytest.raises(BotError, match="onchain_version_mismatch"):
        svc.prepare({**create, "action": "add_review", "account": reviewer,
                     "onchain_version_id": second["onchain_ids"]["version_id"], "local_review_id": 21})


def test_actual_receipt_failed_and_missing_commitment_event(evm_service):
    svc, tester, web3, node, author, _ = evm_service
    address = deploy(evm_service)
    create = {"chain_id": 968, "action": "create_case", "account": author, "contract_address": address,
              "case_id": CASE_ID, "local_version_id": 41}
    plan = svc.prepare(create)
    tx = web3.eth.send_transaction(evm_tx(plan["transaction"]))
    tester.mine_blocks(2)
    original_handler = node.handle
    def remove_event(req):
        response = original_handler(req)
        payload = json.loads(req.content)
        if payload["method"] == "eth_getTransactionReceipt":
            body = response.json()
            body["result"]["logs"] = []
            return httpx.Response(200, json=body)
        return response
    node.handle = remove_event
    result = svc.verify({"chain_id": 968, "transaction_hash": "0x" + tx.hex(), "prepared": create})
    assert result["status"] == "mismatch"
    assert result["reason"] == "commitment_event_missing"
    node.handle = original_handler
    failed_hash = web3.eth.send_transaction(evm_tx(plan["transaction"]))
    assert web3.eth.wait_for_transaction_receipt(failed_hash).status == 0
    tester.mine_blocks(2)
    failed = svc.verify({"chain_id": 968, "transaction_hash": "0x" + failed_hash.hex(), "prepared": create})
    assert failed["status"] == "failed"
    assert failed["reason"] == "execution_reverted"


def mined_version_intents(fixture):
    """Save public request/hash pairs across a completed local chain cycle."""
    address = deploy(fixture)
    create = {"chain_id": 968, "action": "create_case", "account": fixture[4],
              "contract_address": address, "case_id": CASE_ID, "local_version_id": 41}
    first = submit_plan(fixture, create)
    append = {**create, "action": "append_version", "local_version_id": 88,
              "onchain_version_id": first["onchain_ids"]["version_id"]}
    second = submit_plan(fixture, append)
    intents = [{"chain_id": 968, "transaction_hash": verified["transaction_hash"],
                "prepared": prepared, "minimum_confirmations": 3}
               for prepared, verified in [(create, first), (append, second)]]
    # The recovery material contains the original public request and full hash;
    # JSON round-trip models browser storage rather than a prepared live plan.
    return json.loads(json.dumps(intents))


def fresh_service(fixture, saved):
    return BotService(lambda case: saved if case == CASE_ID else None,
                      client_factory=fixture[3].client)


def fresh_api(service):
    app = FastAPI()
    app.include_router(create_bot_router(service.required, service=service))
    return app


def test_fresh_service_verifies_saved_intents_after_case_and_head_advance(evm_service, saved):
    _, _, web3, node, author, _ = evm_service
    intents = mined_version_intents(evm_service)
    recovered = fresh_service(evm_service, saved)
    with pytest.raises(BotError, match="case_exists"):
        recovered.prepare(intents[0]["prepared"])
    with pytest.raises(BotError, match="stale_parent"):
        recovered.prepare(intents[1]["prepared"])
    nonce, block_number = web3.eth.get_transaction_count(author), web3.eth.block_number
    node.calls.clear()
    for intent, expected in zip(intents, ["1", "2"]):
        result = recovered.verify(intent)
        assert result["status"] == "verified", result
        assert result["onchain_ids"]["version_id"] == expected
        assert result["transaction_hash"] == intent["transaction_hash"]
        assert result["getter_state"]["version"]["content_hash"] == saved["versions"][int(expected) - 1]["content_hash"]
    assert web3.eth.get_transaction_count(author) == nonce
    assert web3.eth.block_number == block_number
    assert {call["method"] for call in node.calls} <= {
        "eth_chainId", "eth_getTransactionReceipt", "eth_getTransactionByHash",
        "eth_getBlockByNumber", "eth_blockNumber", "eth_getCode", "eth_call"}


@pytest.mark.asyncio
async def test_fresh_api_verifies_original_mined_intents_without_prepare(evm_service, saved, monkeypatch):
    _, _, web3, node, author, _ = evm_service
    intents = mined_version_intents(evm_service)
    recovered = fresh_service(evm_service, saved)
    def unexpected_prepare(_):
        raise AssertionError("Receipt recovery invoked transaction preparation")
    monkeypatch.setattr(recovered, "prepare", unexpected_prepare)
    node.calls.clear()
    nonce, block_number = web3.eth.get_transaction_count(author), web3.eth.block_number
    # Each new API/client verifies solely from the saved public intent, including
    # the original author even if another wallet role is currently being used.
    for index, intent in enumerate(intents):
        app = fresh_api(recovered)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/bot/verify", json=intent)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "verified"
        assert result["onchain_ids"]["version_id"] == str(index + 1)
        assert result["commitments"]["local_version_id"] == [41, 88][index]
    assert web3.eth.get_transaction_count(author) == nonce
    assert web3.eth.block_number == block_number
    assert not {"eth_estimateGas", "eth_gasPrice", "eth_getBalance"} & {call["method"] for call in node.calls}


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,http_status,reason", [
    ("chain", 409, "chain_id_mismatch"),
    ("account", 200, "transaction_sender_mismatch"),
    ("contract", 200, "transaction_target_mismatch"),
    ("action", 409, "initial_version_required"),
    ("local_version", 409, "saved_parent_mismatch"),
    ("local_manifest", 409, "saved_bundle_commitment_mismatch"),
    ("parent", 200, "transaction_input_mismatch"),
    ("transaction_hash", 200, "transaction_input_mismatch"),
    ("truncated_hash", 422, "invalid_bot_request"),
])
async def test_recovered_api_intent_preserves_exact_transaction_bindings(evm_service, saved, fault, http_status, reason):
    _, _, web3, node, author, reviewer = evm_service
    intents = mined_version_intents(evm_service)
    intent = copy.deepcopy(intents[1])
    if fault == "chain":
        intent["chain_id"] = intent["prepared"]["chain_id"] = 677
    elif fault == "account":
        intent["prepared"]["account"] = reviewer
    elif fault == "contract":
        intent["prepared"]["contract_address"] = reviewer
    elif fault == "action":
        intent["prepared"]["action"] = "create_case"
        intent["prepared"].pop("onchain_version_id")
    elif fault == "local_version":
        intent["prepared"]["local_version_id"] = 41
    elif fault == "local_manifest":
        saved["versions"][1]["content_hash"] = "a" * 64
    elif fault == "parent":
        intent["prepared"]["onchain_version_id"] = "2"
    elif fault == "transaction_hash":
        intent["transaction_hash"] = intents[0]["transaction_hash"]
    else:
        intent["transaction_hash"] = intent["transaction_hash"][:-1]
    recovered = fresh_service(evm_service, saved)
    node.calls.clear()
    nonce, block_number = web3.eth.get_transaction_count(author), web3.eth.block_number
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fresh_api(recovered)), base_url="http://test") as client:
        response = await client.post("/api/bot/verify", json=intent)
    assert response.status_code == http_status, response.text
    if http_status == 200:
        assert response.json()["status"] == "mismatch"
        assert response.json()["reason"] == reason
    else:
        assert response.json() == {"detail": reason}
    assert web3.eth.get_transaction_count(author) == nonce
    assert web3.eth.block_number == block_number
    assert not {"eth_estimateGas", "eth_gasPrice", "eth_getBalance"} & {call["method"] for call in node.calls}


@pytest.mark.asyncio
async def test_actual_evm_large_review_inventory_and_getter_rpc_recovery(evm_service, saved):
    svc, tester, web3, node, author, reviewer = evm_service
    address = deploy(evm_service)
    create = {"chain_id": 968, "action": "create_case", "account": author, "contract_address": address,
              "case_id": CASE_ID, "local_version_id": 41}
    first = submit_plan(evm_service, create)
    version_id = first["onchain_ids"]["version_id"]
    artifact = json.loads(svc.artifact_path.read_text(encoding="utf-8"))
    contract = web3.eth.contract(address=Web3.to_checksum_address(address), abi=artifact["abi"])
    for index in range(7):
        tx = contract.functions.addReview(case_id_bytes(CASE_ID), int(version_id),
            bytes.fromhex(saved["versions"][0]["content_hash"]), 1,
            hashlib.sha256(f"prior-public-review-{index}".encode()).digest(), "").transact({"from": author})
        assert web3.eth.wait_for_transaction_receipt(tx).status == 1
    prepared = {**create, "action": "add_review", "account": reviewer,
                "onchain_version_id": version_id, "local_review_id": 21}
    with pytest.raises(BotError, match="review_preflight_required"):
        svc.prepare(prepared)
    first_page = svc.review_preflight({"prepared": prepared})
    assert first_page["status"] == "scanning" and first_page["scanned_count"] == "6"
    ready = svc.review_preflight({"prepared": prepared, "cursor": first_page["cursor"]})
    assert ready["status"] == "ready" and ready["scanned_count"] == "7"
    plan = svc.prepare({**prepared, "review_preflight_cursor": ready["cursor"]})
    tx = web3.eth.send_transaction(evm_tx(plan["transaction"]))
    assert web3.eth.wait_for_transaction_receipt(tx).status == 1
    tester.mine_blocks(2)
    intent = {"chain_id": 968, "transaction_hash": "0x" + tx.hex(), "prepared": prepared}
    original = node.handle
    def unavailable_getter(req):
        payload = json.loads(req.content)
        if payload["method"] == "eth_call":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"],
                "error": {"code": -32000, "message": "provider-private-content"}})
        return original(req)
    node.handle = unavailable_getter
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fresh_api(svc)), base_url="http://test") as client:
        interrupted = await client.post("/api/bot/verify", json=intent)
        assert interrupted.status_code == 503
        assert interrupted.json() == {"detail": "bot_rpc_unavailable"}
        node.handle = original
        recovered = await client.post("/api/bot/verify", json=intent)
    assert recovered.status_code == 200 and recovered.json()["status"] == "verified"
    assert recovered.json()["onchain_ids"]["review_id"] == "8"
    scan = svc.review_preflight({"prepared": prepared})
    duplicate = svc.review_preflight({"prepared": prepared, "cursor": scan["cursor"]})
    assert duplicate["status"] == "duplicate" and duplicate["existing_review_id"] == "8"
    assert max(call["id"] for call in node.calls) <= 20
