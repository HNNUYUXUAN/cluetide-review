"""Release witness bytecode runs only in an in-process Py-EVM chain."""

import hashlib
import json
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from web3 import EthereumTesterProvider, Web3

from cluetide.release_chain import receipt_typed_data, recover_receipt_signer

ROOT = Path(__file__).resolve().parents[1]
TASK = hashlib.sha256(b"demo release task").digest()
TARGET = hashlib.sha256(b"fixed demo target and policy").digest()
MANIFEST = hashlib.sha256(b"canonical version manifest").digest()
ARTIFACT = hashlib.sha256(b"prebuilt artifact").digest()
RECEIPT = hashlib.sha256(b"signed observation").digest()


@pytest.fixture()
def evm():
    artifact = json.loads((ROOT / "contracts/artifacts/ClueTideReleaseWitness.json").read_text(encoding="utf-8"))
    assert artifact["sourceSha256"] == hashlib.sha256((ROOT / "contracts/ClueTideReleaseWitness.sol").read_bytes()).hexdigest()
    assert artifact["compiler"] == "0.8.30+commit.73712a01"
    assert artifact["evmVersion"] == "paris"
    tester = EthereumTester(backend=PyEVMBackend())
    provider = EthereumTesterProvider(tester)
    assert type(provider) is EthereumTesterProvider
    web3 = Web3(provider)
    factory = web3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    receipt = web3.eth.wait_for_transaction_receipt(factory.constructor().transact({"from": web3.eth.accounts[0]}))
    contract = web3.eth.contract(address=receipt.contractAddress, abi=artifact["abi"])
    assert web3.eth.get_code(contract.address).to_0x_hex().lower() == artifact["deployedBytecode"].lower()
    return web3, contract, tester


def submit(evm, function, sender=0):
    web3, _, _ = evm
    receipt = web3.eth.wait_for_transaction_receipt(function.transact({"from": web3.eth.accounts[sender]}))
    assert receipt.status == 1
    return receipt


def create(evm, task=TASK):
    web3, contract, _ = evm
    return submit(evm, contract.functions.createTask(task, *web3.eth.accounts[1:5], TARGET))


def propose(evm, parent=0, task=TASK):
    web3, contract, _ = evm
    deadline = web3.eth.get_block("latest").timestamp + 1000
    submit(evm, contract.functions.proposeVersion(task, parent, MANIFEST, ARTIFACT, deadline), 1)
    return deadline


def approve(evm, version=1, deadline=None, task=TASK):
    contract = evm[1]
    deadline = deadline or contract.functions.getVersion(task, version).call()[4]
    return submit(evm, contract.functions.approve(task, version, MANIFEST, ARTIFACT, deadline), 2)


def consume(evm, version=1, task=TASK):
    return submit(evm, evm[1].functions.consume(task, version, MANIFEST, ARTIFACT), 3)


def signature(evm, attempt=1, receipt=RECEIPT, result=1, **overrides):
    web3, contract, tester = evm
    version = contract.functions.getAttempt(TASK, attempt).call()[1]
    values = dict(chain_id=web3.eth.chain_id, contract_address=contract.address, task_id="0x" + TASK.hex(),
        version_id=version, attempt_id=attempt, receipt_hash="0x" + receipt.hex(), result=result)
    values.update(overrides)
    return Account.sign_message(encode_typed_data(full_message=receipt_typed_data(**values)), tester.backend.account_keys[4]).signature


def test_release_approval_consumption_and_signed_receipt(evm):
    web3, contract, _ = evm
    create(evm)
    deadline = propose(evm)
    approve(evm, deadline=deadline)
    consume(evm)
    task = contract.functions.getTask(TASK).call()
    assert task[:6] == (*web3.eth.accounts[:5], TARGET)
    assert task[6:8] == (1, 1)
    version = contract.functions.getVersion(TASK, 1).call()
    assert version[:6] == (1, 0, MANIFEST, ARTIFACT, deadline, 4)
    assert version[7] == 1
    sig = signature(evm)
    message = encode_typed_data(full_message=receipt_typed_data(web3.eth.chain_id, contract.address,
        "0x" + TASK.hex(), 1, 1, "0x" + RECEIPT.hex(), 1))
    assert Account.sign_message(message, evm[2].backend.account_keys[4]).message_hash == contract.functions.receiptDigest(TASK, 1, RECEIPT, 1).call()
    assert recover_receipt_signer(web3.eth.chain_id, contract.address, "0x" + TASK.hex(), 1, 1,
        "0x" + RECEIPT.hex(), 1, "0x" + sig.hex()) == web3.eth.accounts[4].lower()
    submit(evm, contract.functions.recordReceipt(TASK, 1, RECEIPT, 1, sig), 8)
    attempt = contract.functions.getAttempt(TASK, 1).call()
    assert attempt[:2] == (1, 1)
    assert attempt[3:5] == (RECEIPT, 1)
    submit(evm, contract.functions.recordReceipt(TASK, 1, RECEIPT, 1, sig), 9)
    assert contract.functions.getAttempt(TASK, 1).call() == attempt


def test_fixed_roles_and_distinct_reviewer(evm):
    web3, contract, _ = evm
    with pytest.raises(TransactionFailed):
        contract.functions.createTask(TASK, web3.eth.accounts[1], web3.eth.accounts[1], web3.eth.accounts[3], web3.eth.accounts[4], TARGET).transact({"from": web3.eth.accounts[0]})
    create(evm)
    with pytest.raises(TransactionFailed):
        create(evm)
    with pytest.raises(TransactionFailed):
        contract.functions.proposeVersion(TASK, 0, MANIFEST, ARTIFACT, 2**64 - 1).transact({"from": web3.eth.accounts[0]})
    deadline = propose(evm)
    for function in (contract.functions.approve(TASK, 1, MANIFEST, ARTIFACT, deadline),
                     contract.functions.revoke(TASK, 1), contract.functions.requestCorrection(TASK, 1)):
        with pytest.raises(TransactionFailed):
            function.transact({"from": web3.eth.accounts[1]})
    approve(evm)
    with pytest.raises(TransactionFailed):
        contract.functions.consume(TASK, 1, MANIFEST, ARTIFACT).transact({"from": web3.eth.accounts[2]})


def test_approval_binds_exact_hashes_deadline_and_current_version(evm):
    web3, contract, _ = evm
    create(evm)
    deadline = propose(evm)
    for mh, ah, expiry in ((ARTIFACT, ARTIFACT, deadline), (MANIFEST, MANIFEST, deadline), (MANIFEST, ARTIFACT, deadline + 1)):
        with pytest.raises(TransactionFailed):
            contract.functions.approve(TASK, 1, mh, ah, expiry).transact({"from": web3.eth.accounts[2]})
    approve(evm)
    with pytest.raises(TransactionFailed):
        contract.functions.consume(TASK, 1, ARTIFACT, ARTIFACT).transact({"from": web3.eth.accounts[3]})
    propose(evm, parent=1)
    with pytest.raises(TransactionFailed):
        consume(evm)
    with pytest.raises(TransactionFailed):
        propose(evm, parent=1)
    approve(evm, version=2)
    consume(evm, version=2)
    assert contract.functions.getAttempt(TASK, 1).call()[1] == 2


@pytest.mark.parametrize("decision,sender", [("revoke", 0), ("revoke", 2), ("requestCorrection", 2)])
def test_revocation_correction_require_fresh_version(evm, decision, sender):
    web3, contract, _ = evm
    create(evm)
    propose(evm)
    approve(evm)
    submit(evm, getattr(contract.functions, decision)(TASK, 1), sender)
    with pytest.raises(TransactionFailed):
        consume(evm)
    with pytest.raises(TransactionFailed):
        approve(evm)
    propose(evm, parent=1)
    approve(evm, version=2)
    consume(evm, version=2)


def test_expiry_duplicate_consumption_and_used_grant_boundary(evm):
    web3, contract, tester = evm
    create(evm)
    deadline = propose(evm)
    approve(evm)
    tester.time_travel(deadline + 1)
    tester.mine_block()
    with pytest.raises(TransactionFailed):
        consume(evm)
    propose(evm, parent=1)
    approve(evm, version=2)
    consume(evm, version=2)
    for function in (contract.functions.consume(TASK, 2, MANIFEST, ARTIFACT), contract.functions.revoke(TASK, 2)):
        with pytest.raises(TransactionFailed):
            function.transact({"from": web3.eth.accounts[3] if function.fn_name == "consume" else web3.eth.accounts[0]})
    propose(evm, parent=2)
    submit(evm, contract.functions.recordReceipt(TASK, 1, RECEIPT, 1, signature(evm)), 8)
    assert contract.functions.getAttempt(TASK, 1).call()[1] == 2


@pytest.mark.parametrize("override", [
    {"chain_id": 677}, {"contract_address": "0x" + "11" * 20}, {"task_id": "0x" + "22" * 32},
    {"version_id": 2}, {"attempt_id": 2}, {"receipt_hash": "0x" + "33" * 32}, {"result": 2},
])
def test_receipt_domain_and_statement_replay_rejected(evm, override):
    web3, contract, _ = evm
    create(evm)
    propose(evm)
    approve(evm)
    consume(evm)
    with pytest.raises(TransactionFailed):
        contract.functions.recordReceipt(TASK, 1, RECEIPT, 1, signature(evm, **override)).transact({"from": web3.eth.accounts[9]})
    assert contract.functions.getAttempt(TASK, 1).call()[4] == 0


def test_receipt_signature_and_conflicting_observation(evm):
    web3, contract, _ = evm
    create(evm)
    propose(evm)
    approve(evm)
    consume(evm)
    valid = signature(evm)
    for bad in (b"", valid[:-1], valid[:-1] + b"\x00", valid[:32] + bytes.fromhex("ff" * 32) + valid[64:]):
        with pytest.raises(TransactionFailed):
            contract.functions.recordReceipt(TASK, 1, RECEIPT, 1, bad).transact({"from": web3.eth.accounts[9]})
    submit(evm, contract.functions.recordReceipt(TASK, 1, RECEIPT, 1, valid), 8)
    for receipt, result in ((ARTIFACT, 1), (RECEIPT, 2)):
        with pytest.raises(TransactionFailed):
            contract.functions.recordReceipt(TASK, 1, receipt, result, signature(evm, receipt=receipt, result=result)).transact({"from": web3.eth.accounts[9]})


def test_unknown_ids_and_empty_commitments_fail(evm):
    web3, contract, _ = evm
    for getter in (contract.functions.getTask(TASK), contract.functions.getVersion(TASK, 1), contract.functions.getAttempt(TASK, 1)):
        with pytest.raises(TransactionFailed):
            getter.call()
    create(evm)
    for mh, ah, deadline in ((bytes(32), ARTIFACT, 2**64 - 1), (MANIFEST, bytes(32), 2**64 - 1), (MANIFEST, ARTIFACT, 1)):
        with pytest.raises(TransactionFailed):
            contract.functions.proposeVersion(TASK, 0, mh, ah, deadline).transact({"from": web3.eth.accounts[1]})
