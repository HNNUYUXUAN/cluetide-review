"""Local-only integration: real Py-EVM, chain adapter and filesystem executor.

Every HTTP request terminates in MockTransport. All signed transactions use
ephemeral EthereumTester accounts and remain in the in-process chain.
"""

import json
from pathlib import Path
import shutil

import httpx
import pytest
from eth_account import Account
from eth_tester import EthereumTester, PyEVMBackend
from web3 import EthereumTesterProvider, Web3

from cluetide.bundles import canonical_json_bytes
from cluetide.release_chain import ReleaseChainService
from cluetide.releases import ChainGateway, ReleaseError, ReleaseService

ROOT = Path(__file__).resolve().parents[1]


class LocalEVMBridge:
    """Translate read-only JSON-RPC into actual in-process Web3 operations."""

    def __init__(self):
        self.tester = EthereumTester(backend=PyEVMBackend())
        self.tester.backend.chain.chain_id = 677
        self.web3 = Web3(EthereumTesterProvider(self.tester))
        assert type(self.web3.provider) is EthereumTesterProvider
        self.calls = []
        self.errors = []

    def transaction(self, payload):
        result = {}
        for key in ("from", "to", "data", "value", "gas", "gasPrice"):
            if key not in payload:
                continue
            value = payload[key]
            if key in ("from", "to"):
                value = Web3.to_checksum_address(value)
            elif key in ("value", "gas", "gasPrice"):
                value = int(value, 16) if isinstance(value, str) else value
            result[key] = value
        result.setdefault("from", self.web3.eth.accounts[0])
        return result

    def result(self, method, params):
        web3 = self.web3
        if method == "eth_chainId":
            # EthereumTesterProvider reports a fixed test chain ID separately
            # from the backend's EVM CHAINID and actual EIP-155 signatures.
            return hex(self.tester.backend.chain.chain_id)
        if method == "eth_blockNumber":
            return hex(web3.eth.block_number)
        if method == "eth_getBlockByNumber":
            block = web3.eth.get_block(int(params[0], 16))
            return {"number": hex(block.number), "hash": block.hash.to_0x_hex(), "timestamp": hex(block.timestamp)}
        if method == "eth_getCode":
            return web3.eth.get_code(Web3.to_checksum_address(params[0]), int(params[1], 16)).to_0x_hex()
        if method == "eth_getBalance":
            return hex(web3.eth.get_balance(Web3.to_checksum_address(params[0]), int(params[1], 16)))
        if method == "eth_gasPrice":
            return hex(web3.eth.gas_price)
        if method == "eth_call":
            return web3.eth.call(self.transaction(params[0]), block_identifier=int(params[1], 16)).to_0x_hex()
        if method == "eth_estimateGas":
            # Py-EVM estimates at its head. No mining can occur inside a
            # preparation call, so this is the requested pinned head state.
            assert int(params[1], 16) == web3.eth.block_number
            return hex(web3.eth.estimate_gas(self.transaction(params[0])))
        if method == "eth_getTransactionReceipt":
            receipt = web3.eth.get_transaction_receipt(params[0])
            return {"transactionHash": receipt.transactionHash.to_0x_hex(), "from": receipt["from"],
                "to": receipt.to or None, "contractAddress": receipt.contractAddress,
                "blockNumber": hex(receipt.blockNumber), "blockHash": receipt.blockHash.to_0x_hex(),
                "status": hex(receipt.status), "gasUsed": hex(receipt.gasUsed), "effectiveGasPrice": hex(receipt.effectiveGasPrice)}
        if method == "eth_getTransactionByHash":
            tx = web3.eth.get_transaction(params[0])
            chain_id = tx.get("chainId", (tx.v - 35) // 2)
            assert chain_id == 677
            return {"hash": tx.hash.to_0x_hex(), "from": tx["from"], "to": tx.to or None,
                "input": tx.input.to_0x_hex(), "value": hex(tx.value), "chainId": hex(chain_id),
                "blockNumber": hex(tx.blockNumber), "blockHash": tx.blockHash.to_0x_hex()}
        raise AssertionError(f"Forbidden integration RPC method: {method}")

    def handle(self, request):
        assert str(request.url) == "https://rpc.botchain.ai"
        body = json.loads(request.content)
        self.calls.append(body["method"])
        assert not body["method"].startswith(("eth_send", "personal_"))
        try:
            result = self.result(body["method"], body["params"])
            response = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        except Exception as exc:
            self.errors.append((body["method"], type(exc).__name__, str(exc)))
            response = {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32000, "message": "Local EVM operation reverted"}}
        return httpx.Response(200, json=response)

    def send(self, unsigned):
        """Sign only a local test transaction with EthereumTester's test key."""
        tx = self.transaction(unsigned)
        sender = tx.pop("from")
        key_index = self.web3.eth.accounts.index(sender)
        tx.update(chainId=677, nonce=self.web3.eth.get_transaction_count(sender))
        signed = Account.sign_transaction(tx, self.tester.backend.account_keys[key_index])
        transaction_hash = self.web3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.web3.eth.wait_for_transaction_receipt(transaction_hash)
        assert receipt.status == 1
        self.tester.mine_blocks(2)
        return transaction_hash.to_0x_hex()


@pytest.fixture()
def release(tmp_path, monkeypatch):
    monkeypatch.delenv("CLUETIDE_RELEASE_CONTRACT", raising=False)
    bridge = LocalEVMBridge()
    adapter = ReleaseChainService(client_factory=lambda: httpx.Client(transport=httpx.MockTransport(bridge.handle)))
    state = tmp_path / "state"
    gateway = ChainGateway(state, service=adapter)
    service = ReleaseService(state, chain=gateway, signing_key=bridge.tester.backend.account_keys[4].to_bytes(),
        clock=lambda: bridge.web3.eth.get_block("latest").timestamp)
    deployment = service.prepare({"action": "deploy", "account": bridge.web3.eth.accounts[0]})
    tx_hash = bridge.send(deployment["transaction"])
    verified = service.verify({"action": "deploy", "account": bridge.web3.eth.accounts[0], "transaction_hash": tx_hash})
    assert verified["status"] == "verified"
    state.joinpath("chain.json").write_bytes(canonical_json_bytes({"chain_id": 677, "contract_address": verified["contract_address"]}))
    artifact = json.loads((ROOT / "contracts/artifacts/ClueTideReleaseWitness.json").read_text(encoding="utf-8"))
    contract = bridge.web3.eth.contract(address=Web3.to_checksum_address(verified["contract_address"]), abi=artifact["abi"])
    return service, bridge, contract


def chain_action(release, action, task=None, version=None, attempt=None):
    service, bridge, _ = release
    role = {"create_task": 0, "submit_version": 1, "approve": 2, "revoke": 2, "request_correction": 2,
        "start_execution": 3, "record_receipt": 8}[action]
    payload = {"action": action, "account": bridge.web3.eth.accounts[role]}
    if task:
        payload["task_id"] = task["task_id"]
    if version:
        payload["version_id"] = version["version_id"]
    if attempt:
        payload["attempt_id"] = attempt
    prepared = service.prepare(payload)
    assert prepared["ready_to_send"]
    tx_hash = bridge.send(prepared["transaction"])
    verified = service.verify({**payload, "transaction_hash": tx_hash})
    assert verified["status"] == "verified"
    assert verified["confirmations"] == "3"
    assert int(verified["actual_fee_wei"]) == int(verified["gas_used"]) * int(verified["effective_gas_price"])
    return verified


def create(release):
    service, bridge, _ = release
    task = service.create_task({"title": "Local EVM release", **dict(zip(("owner", "proposer", "reviewer", "executor"), bridge.web3.eth.accounts[:4]))})
    chain_action(release, "create_task", task)
    return task


def version(release, task, text="<!doctype html><h1>Approved release</h1>"):
    service, bridge, _ = release
    result = service.create_version(task["task_id"], {"summary": "Publish the reviewed static artifact", "source_reference": "local-test-commit",
        "artifact_text": text, "test_evidence": ["Local integration fixture"], "expires_at": bridge.web3.eth.get_block("latest").timestamp + 3600})
    chain_action(release, "submit_version", task, result)
    return result


def start(release, task, package):
    chain_action(release, "approve", task, package)
    consumed = chain_action(release, "start_execution", task, package)
    return {"version_id": package["version_id"], "attempt_id": consumed["onchain_ids"]["attempt_id"],
        "start_transaction_hash": consumed["transaction_hash"]}


def test_real_local_publish_signed_onchain_receipt_and_repeat(release):
    service, bridge, contract = release
    task = create(release)
    package = version(release, task)
    execute = start(release, task, package)
    result = service.execute(task["task_id"], execute)
    assert result["status"] == "succeeded"
    assert service.artifact(task["task_id"], execute["attempt_id"]) == b"<!doctype html><h1>Approved release</h1>"
    envelope = service.receipt(execute["attempt_id"], task["task_id"])
    assert envelope["verification"]["signature_valid"] is True
    assert envelope["verification"]["artifact_matches"] is True
    recorded = chain_action(release, "record_receipt", task, package, execute["attempt_id"])
    assert recorded["receipt_signer"] == task["receipt_signer"]
    attempt = contract.functions.getAttempt(bytes.fromhex(task["task_id"][2:]), 1).call()
    assert attempt[3] == bytes.fromhex(envelope["receipt_hash"][2:])
    assert attempt[4] == 1
    # A receipt accepted by the EVM proves that EVM CHAINID and the backend's
    # EIP-712 signature both use 677, beyond the transport metadata adapter.
    assert bridge.tester.backend.chain.chain_id == 677
    repeated = service.execute(task["task_id"], execute)
    assert repeated["status"] == "succeeded"
    assert service.receipt(execute["attempt_id"], task["task_id"])["receipt_hash"] == envelope["receipt_hash"]
    assert not any(method.startswith("eth_send") for method in bridge.calls)


def test_browser_package_import_into_fresh_publisher_and_real_execution(release, tmp_path):
    source, bridge, _ = release
    task = create(release)
    package = version(release, task)
    execute = start(release, task, package)
    root = tmp_path / "imported-publisher"
    root.mkdir()
    shutil.copyfile(source.root / "chain.json", root / "chain.json")
    gateway = ChainGateway(root, service=source._adapter().service)
    publisher = ReleaseService(root, chain=gateway, signing_key=bridge.tester.backend.account_keys[4].to_bytes())
    payload = {"task": {key: task[key] for key in ("title", "owner", "proposer", "reviewer", "executor", "task_id", "receipt_signer", "target_id", "created_at")},
               "manifest": package["manifest"], "artifact_text": "<!doctype html><h1>Approved release</h1>"}
    imported = publisher.import_version(payload)
    assert imported["manifest_hash"] == package["manifest_hash"]
    assert int(imported["read_block_number"]) == bridge.web3.eth.block_number - 2
    assert publisher.import_version(payload) == imported
    assert publisher.execute(task["task_id"], execute)["status"] == "succeeded"
    assert publisher.receipt("1", task["task_id"])["verification"]["signature_valid"] is True


@pytest.mark.parametrize("decision", ["revoke", "request_correction"])
def test_real_revocation_or_correction_prevents_publication(release, decision):
    service, _, contract = release
    task = create(release)
    package = version(release, task)
    chain_action(release, "approve", task, package)
    chain_action(release, decision, task, package)
    with pytest.raises(ReleaseError):
        chain_action(release, "start_execution", task, package)
    assert contract.functions.getTask(bytes.fromhex(task["task_id"][2:])).call()[7] == 0
    assert not (service.root / "targets" / task["task_id"] / "current.json").exists()


def test_new_manifest_requires_its_own_approval_and_used_grant_survives(release):
    service, _, contract = release
    task = create(release)
    first = version(release, task)
    chain_action(release, "approve", task, first)
    second = version(release, task, "<!doctype html><h1>Reviewed revision</h1>")
    assert second["manifest_hash"] != first["manifest_hash"]
    with pytest.raises(ReleaseError):
        chain_action(release, "start_execution", task, first)
    with pytest.raises(ReleaseError):
        chain_action(release, "start_execution", task, second)
    execute = start(release, task, second)
    version(release, task, "<!doctype html><h1>Future revision</h1>")
    assert service.execute(task["task_id"], execute)["status"] == "succeeded"
    assert contract.functions.getAttempt(bytes.fromhex(task["task_id"][2:]), 1).call()[1] == 2
    assert service.artifact(task["task_id"], "1") == b"<!doctype html><h1>Reviewed revision</h1>"


def test_tampered_manifest_blocks_execution_of_real_consumed_grant(release):
    service, _, _ = release
    task = create(release)
    package = version(release, task)
    execute = start(release, task, package)
    manifest_path = service.root / "tasks" / task["task_id"] / "versions/1/manifest.json"
    value = json.loads(manifest_path.read_bytes())
    value["summary"] = "Changed after approval"
    manifest_path.write_bytes(canonical_json_bytes(value))
    with pytest.raises(ReleaseError, match="integrity"):
        service.execute(task["task_id"], execute)
    assert not (service.root / "targets" / task["task_id"] / "current.json").exists()


def test_unknown_after_publication_reconciles_without_second_target_switch(release, monkeypatch):
    service, _, contract = release
    task = create(release)
    package = version(release, task)
    execute = start(release, task, package)
    switches = []
    original_switch, original_finish = service._switch_target, service._finish
    def switch(*args):
        switches.append(1)
        return original_switch(*args)
    def interrupted(*args):
        raise OSError("Local test crash after publication")
    monkeypatch.setattr(service, "_switch_target", switch)
    monkeypatch.setattr(service, "_finish", interrupted)
    assert service.execute(task["task_id"], execute)["status"] == "unknown"
    monkeypatch.setattr(service, "_finish", original_finish)
    recovered = service.execute(task["task_id"], execute)
    assert recovered["status"] == "succeeded"
    assert switches == [1]
    assert contract.functions.getTask(bytes.fromhex(task["task_id"][2:])).call()[7] == 1
    assert service.receipt("1", task["task_id"])["verification"]["signature_valid"] is True


def test_unknown_without_observed_target_stays_unknown_until_fresh_authorization(release, monkeypatch):
    service, _, contract = release
    task = create(release)
    package = version(release, task)
    execute = start(release, task, package)
    switches = []
    original_switch = service._switch_target
    def interrupted(*args):
        switches.append(1)
        raise OSError("Local test crash before target observation")
    monkeypatch.setattr(service, "_switch_target", interrupted)
    assert service.execute(task["task_id"], execute)["status"] == "unknown"
    assert service.execute(task["task_id"], execute)["status"] == "unknown"
    assert switches == [1]
    assert not (service.root / "targets" / task["task_id"] / "current.json").exists()
    monkeypatch.setattr(service, "_switch_target", original_switch)
    second = version(release, task, "<!doctype html><h1>Explicit retry</h1>")
    retry = start(release, task, second)
    assert retry["attempt_id"] == "2"
    assert service.execute(task["task_id"], retry)["status"] == "succeeded"
    assert contract.functions.getTask(bytes.fromhex(task["task_id"][2:])).call()[7] == 2


def test_reported_attempt_cannot_publish_from_fresh_local_executor_state(release, tmp_path):
    service, bridge, _ = release
    task = create(release)
    package = version(release, task)
    execute = start(release, task, package)
    assert service.execute(task["task_id"], execute)["status"] == "succeeded"
    chain_action(release, "record_receipt", task, package, execute["attempt_id"])
    restored = tmp_path / "restored"
    shutil.copytree(service.root / "tasks", restored / "tasks")
    restored.joinpath("chain.json").write_bytes(service.root.joinpath("chain.json").read_bytes())
    gateway = ChainGateway(restored, service=service.chain.service)
    executor = ReleaseService(restored, chain=gateway, signing_key=bridge.tester.backend.account_keys[4].to_bytes())
    with pytest.raises(ReleaseError):
        executor.execute(task["task_id"], execute)
    assert not (restored / "targets" / task["task_id"] / "current.json").exists()
