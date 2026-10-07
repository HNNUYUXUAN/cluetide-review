"""Actual bytecode execution on an in-process Py-EVM backend only."""

import hashlib
import json
from pathlib import Path

import pytest
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from web3 import EthereumTesterProvider, Web3

ROOT = Path(__file__).resolve().parents[1]
CASE_A = hashlib.sha256(b"uniswap-93").digest()
CASE_B = hashlib.sha256(b"second-case").digest()
HASH_1 = hashlib.sha256(b"evidence v1").digest()
HASH_2 = hashlib.sha256(b"evidence v2").digest()
REVIEW_HASH = hashlib.sha256(b"review exact v1").digest()


@pytest.fixture()
def evm():
    artifact = json.loads((ROOT / "contracts" / "artifacts" / "ClueTideRegistry.json").read_text(encoding="utf-8"))
    assert artifact["sourceSha256"] == hashlib.sha256((ROOT / "contracts" / "ClueTideRegistry.sol").read_bytes()).hexdigest()
    assert artifact["evmVersion"] == "paris"
    tester = EthereumTester(backend=PyEVMBackend())
    provider = EthereumTesterProvider(tester)
    assert type(provider) is EthereumTesterProvider  # No HTTP/WebSocket provider can reach this fixture.
    web3 = Web3(provider)
    author, reviewer = web3.eth.accounts[:2]
    factory = web3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    transaction = factory.constructor().transact({"from": author})
    receipt = web3.eth.wait_for_transaction_receipt(transaction)
    assert receipt.status == 1
    contract = web3.eth.contract(address=receipt.contractAddress, abi=artifact["abi"])
    return web3, contract, author, reviewer


def submit(evm, function, sender=None):
    web3, _, author, _ = evm
    receipt = web3.eth.wait_for_transaction_receipt(function.transact({"from": sender or author}))
    assert receipt.status == 1
    return receipt


def create(evm, case_id=CASE_A, content_hash=HASH_1):
    return submit(evm, evm[1].functions.createCase(case_id, content_hash, "cluetide.evidence.v1", "bundle://local/v1"))


def test_compiled_create_review_corrected_v2_getters(evm):
    web3, contract, author, reviewer = evm
    create(evm)
    case = contract.functions.getCase(CASE_A).call()
    assert case[:4] == (author, 1, 1, 0)
    first = contract.functions.getVersion(1).call()
    assert first[:5] == (CASE_A, 1, 0, author, HASH_1)
    submit(evm, contract.functions.addReview(CASE_A, 1, HASH_1, 2, REVIEW_HASH, "bundle://review/v1"), reviewer)
    submit(evm, contract.functions.appendVersion(CASE_A, 1, HASH_2, "cluetide.evidence.v1", "bundle://local/v2"))
    second = contract.functions.getVersion(2).call()
    review = contract.functions.getReview(1).call()
    assert second[:5] == (CASE_A, 2, 1, author, HASH_2)
    assert review[:7] == (1, CASE_A, 1, HASH_1, reviewer, 2, REVIEW_HASH)
    # The v1 review retains its original hash after the head changes to v2.
    assert contract.functions.getCase(CASE_A).call()[:4] == (author, 2, 2, 1)
    assert contract.functions.getVersionId(CASE_A, 0).call() == 1
    assert contract.functions.getVersionId(CASE_A, 1).call() == 2
    assert contract.functions.getReviewId(CASE_A, 0).call() == 1
    assert web3.eth.get_code(contract.address).hex() != ""


def test_compiled_author_permission(evm):
    create(evm)
    _, contract, _, reviewer = evm
    with pytest.raises(TransactionFailed):
        contract.functions.appendVersion(CASE_A, 1, HASH_2, "v1", "").transact({"from": reviewer})
    assert contract.functions.getCase(CASE_A).call()[1] == 1


def test_compiled_parent_same_case_and_current_head(evm):
    create(evm)
    create(evm, CASE_B)
    _, contract, author, _ = evm
    with pytest.raises(TransactionFailed):
        contract.functions.appendVersion(CASE_A, 2, HASH_2, "v1", "").transact({"from": author})
    submit(evm, contract.functions.appendVersion(CASE_A, 1, HASH_2, "v1", ""))
    with pytest.raises(TransactionFailed):
        contract.functions.appendVersion(CASE_A, 1, HASH_1, "v1", "").transact({"from": author})
    assert contract.functions.getCase(CASE_A).call()[1] == 3


def test_compiled_review_exact_case_version_hash(evm):
    create(evm)
    create(evm, CASE_B)
    _, contract, _, reviewer = evm
    with pytest.raises(TransactionFailed):
        contract.functions.addReview(CASE_A, 2, HASH_1, 2, REVIEW_HASH, "").transact({"from": reviewer})
    with pytest.raises(TransactionFailed):
        contract.functions.addReview(CASE_A, 1, HASH_2, 2, REVIEW_HASH, "").transact({"from": reviewer})
    with pytest.raises(TransactionFailed):
        contract.functions.addReview(CASE_A, 999, HASH_1, 2, REVIEW_HASH, "").transact({"from": reviewer})
    assert contract.functions.reviewCount().call() == 0


@pytest.mark.parametrize("bad_function", [
    lambda c: c.functions.createCase(bytes(32), HASH_1, "v1", ""),
    lambda c: c.functions.createCase(CASE_B, bytes(32), "v1", ""),
    lambda c: c.functions.createCase(CASE_B, HASH_1, "", ""),
    lambda c: c.functions.createCase(CASE_B, HASH_1, "v" * 65, ""),
    lambda c: c.functions.createCase(CASE_B, HASH_1, "v1", "u" * 513),
    lambda c: c.functions.addReview(CASE_A, 1, HASH_1, 0, REVIEW_HASH, ""),
    lambda c: c.functions.addReview(CASE_A, 1, HASH_1, 4, REVIEW_HASH, ""),
    lambda c: c.functions.addReview(CASE_A, 1, HASH_1, 1, bytes(32), ""),
])
def test_compiled_invalid_metadata_rejected(evm, bad_function):
    create(evm)
    _, contract, author, _ = evm
    with pytest.raises(TransactionFailed):
        bad_function(contract).transact({"from": author})
    assert contract.functions.versionCount().call() == 1


def test_compiled_duplicate_case_unknown_ids_and_indices(evm):
    create(evm)
    _, contract, author, _ = evm
    with pytest.raises(TransactionFailed):
        contract.functions.createCase(CASE_A, HASH_2, "v1", "").transact({"from": author})
    for getter in (contract.functions.getCase(CASE_B), contract.functions.getVersion(999),
                   contract.functions.getReview(999), contract.functions.getVersionId(CASE_A, 1),
                   contract.functions.getReviewId(CASE_A, 0)):
        with pytest.raises(TransactionFailed):
            getter.call()


def test_compiled_self_review_is_recorded_without_independence_claim(evm):
    create(evm)
    _, contract, author, _ = evm
    submit(evm, contract.functions.addReview(CASE_A, 1, HASH_1, 3, REVIEW_HASH, ""), author)
    assert contract.functions.getReview(1).call()[4] == author
