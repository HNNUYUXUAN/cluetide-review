"""GCC tool evidence keeps transaction identities and source scope consistent."""
import asyncio
import copy

import pytest

from cluetide.adapters import CachedRpc, InvestigationTools
from cluetide.case_catalog import load_catalog_case
from cluetide.rpc import RpcError
from cluetide.schemas import InvestigationRequest, MAX_UINT256


@pytest.fixture
def captured_case():
    preset, snapshot, sources = load_catalog_case("uniswap93")
    scope = InvestigationRequest(**{key: preset[key] for key in
                                   ("address", "token_address", "from_block", "to_block")})
    return preset, snapshot, sources, scope


class ReturningRpc:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def call(self, method, params):
        key = "receipt" if method == "eth_getTransactionReceipt" else "transaction"
        return copy.deepcopy(self.snapshot[key])


def read_transaction(backend, tx_hash, receipt):
    return asyncio.run(backend.get_receipt(tx_hash) if receipt else backend.get_transaction(tx_hash))


@pytest.mark.parametrize("receipt", [True, False])
@pytest.mark.parametrize("field,value", [
    ("from", None), ("from", "bad"), ("from", 1),
    ("to", "bad"), ("to", False),
])
def test_transaction_participants_require_rpc_addresses(captured_case, receipt, field, value):
    preset, snapshot, _, scope = captured_case
    key = "receipt" if receipt else "transaction"
    snapshot[key][field] = value
    backend = InvestigationTools(ReturningRpc(snapshot), scope, [])
    with pytest.raises(RpcError, match="participant"):
        read_transaction(backend, preset["tx_hash"], receipt)
    assert backend.observations[0]["result"] == snapshot[key]


@pytest.mark.parametrize("receipt", [True, False])
@pytest.mark.parametrize("field", ["from", "to"])
def test_missing_participant_is_distinct_from_contract_creation(captured_case, receipt, field):
    preset, snapshot, _, scope = captured_case
    del snapshot["receipt" if receipt else "transaction"][field]
    backend = InvestigationTools(ReturningRpc(snapshot), scope, [])
    with pytest.raises(RpcError, match="participant"):
        read_transaction(backend, preset["tx_hash"], receipt)
    assert len(backend.observations) == 1


def test_contract_creation_has_an_explicit_null_recipient(captured_case):
    preset, snapshot, _, scope = captured_case
    snapshot["receipt"]["to"] = snapshot["transaction"]["to"] = None
    backend = InvestigationTools(ReturningRpc(snapshot), scope, [])
    for receipt in (True, False):
        evidence = read_transaction(backend, preset["tx_hash"], receipt)
        assert evidence["status"] == "ok"
        assert evidence["payload"]["to"] is None


@pytest.mark.parametrize("value", [None, True, 1, "1", "-0x1", "0x", "0x00", "0x01", hex(MAX_UINT256 + 1)])
def test_native_value_requires_a_canonical_uint256_quantity(captured_case, value):
    preset, snapshot, _, scope = captured_case
    snapshot["transaction"]["value"] = value
    backend = InvestigationTools(ReturningRpc(snapshot), scope, [])
    with pytest.raises(RpcError, match="input or native value"):
        read_transaction(backend, preset["tx_hash"], False)
    assert backend.observations[0]["result"]["value"] == value


@pytest.mark.parametrize("value", ["0x0", hex(2**53 + 1), hex(MAX_UINT256)])
def test_native_value_keeps_full_precision_independently_of_token_amount(captured_case, value):
    preset, snapshot, _, scope = captured_case
    snapshot["transaction"]["value"] = value
    backend = InvestigationTools(ReturningRpc(snapshot), scope, [])
    evidence = read_transaction(backend, preset["tx_hash"], False)
    assert evidence["payload"]["value"] == value
    assert backend.observations[0]["result"]["value"] == value


@pytest.mark.parametrize("value", [None, True, "execute(93)", "0x1", "0xgg"])
def test_transaction_input_requires_rpc_bytes(captured_case, value):
    preset, snapshot, _, scope = captured_case
    snapshot["transaction"]["input"] = value
    backend = InvestigationTools(ReturningRpc(snapshot), scope, [])
    with pytest.raises(RpcError, match="input or native value"):
        read_transaction(backend, preset["tx_hash"], False)
    assert backend.observations[0]["result"]["input"] == value


def test_plain_value_transfer_accepts_empty_input(captured_case):
    preset, snapshot, _, scope = captured_case
    snapshot["transaction"]["input"] = "0x"
    backend = InvestigationTools(ReturningRpc(snapshot), scope, [])
    assert read_transaction(backend, preset["tx_hash"], False)["payload"]["input"] == "0x"


@pytest.mark.parametrize("first_receipt", [True, False])
@pytest.mark.parametrize("field,value", [
    ("from", "0x" + "f" * 40), ("to", "0x" + "e" * 40), ("to", None),
    ("blockNumber", hex(24106379)), ("blockHash", "0x" + "a" * 64),
])
def test_transaction_and_receipt_share_one_identity(captured_case, first_receipt, field, value):
    preset, snapshot, _, scope = captured_case
    backend = InvestigationTools(ReturningRpc(snapshot), scope, [])
    read_transaction(backend, preset["tx_hash"], first_receipt)
    key = "transaction" if first_receipt else "receipt"
    snapshot[key][field] = value
    if key == "receipt" and field in {"blockNumber", "blockHash"}:
        for log in snapshot[key]["logs"]:
            log[field] = value
    with pytest.raises(RpcError, match="previous transaction observation"):
        read_transaction(backend, preset["tx_hash"], not first_receipt)
    assert len(backend.observations) == 2
    assert backend.observations[1]["result"] == snapshot[key]


def test_participant_case_does_not_change_identity_or_original_payload(captured_case):
    preset, snapshot, _, scope = captured_case
    for field in ("from", "to"):
        snapshot["transaction"][field] = "0x" + snapshot["transaction"][field][2:].upper()
    backend = InvestigationTools(ReturningRpc(snapshot), scope, [])
    read_transaction(backend, preset["tx_hash"], True)
    transaction = read_transaction(backend, preset["tx_hash"], False)
    assert transaction["payload"]["from"] == snapshot["transaction"]["from"]
    assert transaction["payload"]["to"] == snapshot["transaction"]["to"]


@pytest.mark.parametrize("case_id", ["uniswap93", "euler-20230313"])
def test_public_cases_keep_exact_participants_and_source_read_scope(case_id):
    preset, snapshot, sources = load_catalog_case(case_id)
    scope = InvestigationRequest(**{key: preset[key] for key in
                                   ("address", "token_address", "from_block", "to_block")})
    original = copy.deepcopy(snapshot)
    backend = InvestigationTools(CachedRpc(snapshot), scope, sources)
    for receipt in (True, False):
        evidence = read_transaction(backend, preset["tx_hash"], receipt)
        raw = snapshot["receipt" if receipt else "transaction"]
        assert evidence["payload"]["from"] == raw["from"]
        assert evidence["payload"]["to"] == raw["to"]
    for source in sources:
        evidence = asyncio.run(backend.get_governance_source(source["source_id"]))
        assert evidence["payload"] == source
    assert snapshot == original


def test_conflicting_source_ids_cannot_silently_replace_a_read_scope(captured_case):
    _, _, sources, scope = captured_case
    source = sources[0]
    conflict = {**source, "scope": "A different source acquisition scope."}
    with pytest.raises(ValueError, match="source ID"):
        InvestigationTools(ReturningRpc({}), scope, [source, conflict])


def test_sparse_legacy_sources_and_identical_duplicates_remain_readable(captured_case):
    _, _, _, scope = captured_case
    sparse = {"title": "Legacy source"}
    named = {"source_id": "notice", "excerpt": "Captured excerpt."}
    backend = InvestigationTools(ReturningRpc({}), scope, [sparse, named, copy.deepcopy(named)])
    assert asyncio.run(backend.get_governance_source("0"))["payload"] == sparse
    assert asyncio.run(backend.get_governance_source("notice"))["payload"] == named
    with pytest.raises(KeyError):
        asyncio.run(backend.get_governance_source("unknown"))


def test_source_payload_is_an_independent_snapshot(captured_case):
    _, _, sources, scope = captured_case
    original = copy.deepcopy(sources[0])
    backend = InvestigationTools(ReturningRpc({}), scope, sources)
    sources[0]["scope"] = "Caller mutation"
    evidence = asyncio.run(backend.get_governance_source(original["source_id"]))
    assert evidence["payload"] == original
    evidence["payload"]["scope"] = "Consumer mutation"
    assert asyncio.run(backend.get_governance_source(original["source_id"]))["payload"] == original


def test_captured_decimals_case_block_can_be_read_without_another_metadata_read(captured_case):
    preset, snapshot, _, _ = captured_case
    block = int(snapshot["case_block"]["number"], 16)
    decimals = "0x" + format(18, "064x")
    snapshot["token_state"] = {"decimals_case_block_number": block, "decimals_case_block": decimals}
    rpc = CachedRpc(snapshot)
    result = asyncio.run(rpc.call("eth_call", [
        {"to": preset["token_address"], "data": "0x313ce567"}, hex(block)]))
    assert result == decimals
