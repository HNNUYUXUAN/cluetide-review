"""BOT public plans and chain binding checks using bounded mock transports."""

import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from eth_abi import decode, encode
from eth_utils import keccak
from pydantic import ValidationError

from cluetide.bot import (Artifact, BotError, BotRPC, BotService, NETWORKS,
                         PrepareRequest, ReadRequest, VerifyRequest)
from cluetide.bundles import canonical_json_bytes, export_bundle, manifest_sha256
from cluetide.registry import case_id_bytes


AUTHOR = "0x" + "1" * 40
REVIEWER = "0x" + "2" * 40
CONTRACT = "0x" + "3" * 40
TX_HASH = "0x" + "4" * 64
BLOCK_HASH = "0x" + "5" * 64
CASE_ID = "saved-bot-investigation"


@pytest.fixture
def saved(tmp_path):
    versions, paths = [], {}
    for local_id, parent_id in [(41, 0), (88, 41)]:
        path = tmp_path / f"v{local_id}.zip"
        report = {"case_id": CASE_ID, "revision": len(versions) + 1, "summary": "Public evidence investigation."}
        if parent_id:
            report["parent_manifest_hash"] = versions[-1]["content_hash"]
        manifest = export_bundle({"schema_version": "cluetide-evidence/v1", "raw": {}}, report, "# Public evidence\n", path)
        versions.append({"case_id": CASE_ID, "case_id_hex": "0x" + case_id_bytes(CASE_ID).hex(),
                         "version_id": local_id, "parent_version_id": parent_id,
                         "content_hash": manifest_sha256(manifest), "schema_version": "cluetide.evidence.v1"})
        paths[str(local_id)] = str(path)
    review = {"case_id": CASE_ID, "version_id": 41, "content_hash": versions[0]["content_hash"],
              "comment": "Clarify the evidence scope.", "reviewer": "local-reviewer"}
    review.update(review_id=21, review_hash=hashlib.sha256(canonical_json_bytes(review)).hexdigest(),
                  decision="correction_requested")
    return {"id": CASE_ID, "versions": versions, "reviews": [review], "bundle_files": paths}


def request(action="deploy", **kwargs):
    fields = {"chain_id": 968, "action": action, "account": AUTHOR}
    if action != "deploy":
        fields.update(contract_address=CONTRACT, case_id=CASE_ID, local_version_id=41)
    fields.update(kwargs)
    return fields


class MockNode:
    def __init__(self):
        self.calls = []
        self.values = {"eth_chainId": "0x3c8", "eth_blockNumber": "0x64",
                       "eth_estimateGas": "0x186a0", "eth_gasPrice": "0x3b9aca00",
                       "eth_getBalance": hex(10**18), "eth_getTransactionReceipt": None,
                       "eth_getBlockByNumber": lambda payload: {"number": payload["params"][0], "hash": BLOCK_HASH, "transactions": []}}

    def handle(self, req):
        assert str(req.url) in {network["rpc_url"] for network in NETWORKS.values()}
        payload = json.loads(req.content)
        self.calls.append(payload)
        value = self.values[payload["method"]]
        if callable(value):
            value = value(payload)
        if isinstance(value, Exception):
            raise value
        body = {"jsonrpc": "2.0", "id": payload["id"]}
        if isinstance(value, dict) and "error" in value:
            body.update(value)
        else:
            body["result"] = value
        return httpx.Response(200, json=body)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle), trust_env=False)


def service(node, saved=None):
    return BotService(lambda _: saved, client_factory=node.client)


def test_deployment_plan_exact_artifact_and_bounded_fees():
    node = MockNode()
    svc = service(node)
    plan = svc.prepare(request())
    assert plan["transaction"] == {"from": AUTHOR, "data": svc.artifact().bytecode, "value": "0x0",
                                   "chainId": "0x3c8", "gas": hex(120000), "gasPrice": hex(10**9)}
    assert plan["fees"] == {"gas_limit": "120000", "gas_price_wei": "1000000000",
                            "estimated_max_fee_wei": "120000000000000", "balance_wei": str(10**18),
                            "sufficient_balance": True}
    assert len(node.calls) == 5
    assert "nonce" not in plan["transaction"]
    assert plan["artifact"]["runtime_sha256"] == hashlib.sha256(bytes.fromhex(svc.artifact().runtime[2:])).hexdigest()


def test_partial_fees_preserve_unknown_and_insufficient_balance():
    node = MockNode()
    node.values["eth_estimateGas"] = {"error": {"code": -1, "message": "private provider details"}}
    plan = service(node).prepare(request())
    assert "gas" not in plan["transaction"]
    assert plan["fees"]["gas_limit"] is None
    assert plan["fees"]["estimated_max_fee_wei"] is None
    assert plan["fees"]["sufficient_balance"] is None
    assert "gas_estimate_unavailable" in plan["warnings"]
    node.values["eth_estimateGas"] = "0x186a0"
    node.values["eth_getBalance"] = "0x0"
    plan = service(node).prepare(request())
    assert plan["fees"]["sufficient_balance"] is False
    assert "insufficient_balance" in plan["warnings"]


@pytest.mark.parametrize("patch", [
    {"chain_id": True}, {"chain_id": "968"}, {"chain_id": 1}, {"account": "private input"},
    {"account": "0x" + "0" * 40}, {"rpc_url": "https://arbitrary.example"},
    {"private_key": "private input"}, {"action": "broadcast"}, {"contract_address": CONTRACT},
])
def test_deployment_request_fixed_fields(patch):
    with pytest.raises(ValidationError):
        PrepareRequest.model_validate({**request(), **patch})


@pytest.mark.parametrize("value", [True, 0, -1, "01", "0", "1.0", 2**53, str(2**256)])
def test_onchain_identifiers_preserve_uint256(value):
    with pytest.raises(ValidationError):
        ReadRequest.model_validate({"chain_id": 968, "contract_address": CONTRACT, "case_id": CASE_ID, "version_id": value})
    assert ReadRequest.model_validate({"chain_id": 968, "contract_address": CONTRACT, "case_id": CASE_ID,
                                       "version_id": str(2**256 - 1)}).version_id == str(2**256 - 1)


@pytest.mark.parametrize("uri", ["https://user:password@example.com/evidence", "https://localhost/evidence",
    "https://127.0.0.1/evidence", "https://10.0.0.1/evidence", "https://example.com/file?api_key=private",
    "https://example.com/file#fragment", "http://example.com/evidence", "x" * 513])
def test_public_uri_validation(uri):
    with pytest.raises(ValidationError):
        PrepareRequest.model_validate(request("create_case", evidence_uri=uri))


def test_artifact_source_binding(tmp_path):
    svc = BotService(lambda _: {})
    altered_source = tmp_path / "source.sol"
    altered_source.write_bytes(svc.source_path.read_bytes() + b"\n")
    with pytest.raises(BotError, match="artifact_integrity_error"):
        Artifact.load(svc.artifact_path, altered_source)


def getter_result(name, values):
    outputs = {"getCase": "(address,uint256,uint256,uint256,uint64)",
               "getVersion": "(bytes32,uint256,uint256,address,bytes32,string,string,uint64)",
               "getReview": "(uint256,bytes32,uint256,bytes32,address,uint8,bytes32,string,uint64)",
               "getVersionId": "uint256", "getReviewId": "uint256"}
    return "0x" + encode([outputs[name]], [values]).hex()


def setup_case(node, svc, saved, *, head=7, author=AUTHOR, content_hash=None):
    node.values["eth_getCode"] = svc.artifact().runtime
    parent_hash = content_hash or saved["versions"][0]["content_hash"]
    def call(payload):
        data = payload["params"][0]["data"]
        if data[:10] == "0x" + keccak(text="getCase(bytes32)")[:4].hex():
            return getter_result("getCase", (author, head, 1, 0, 100))
        if data[:10] == "0x" + keccak(text="getVersionId(bytes32,uint256)")[:4].hex():
            return getter_result("getVersionId", 7)
        return getter_result("getVersion", (case_id_bytes(CASE_ID), 7, 0, author, bytes.fromhex(parent_hash),
                                            "cluetide.evidence.v1", "", 100))
    node.values["eth_call"] = call


def test_append_maps_saved_parent_hash_to_actual_chain_id(saved):
    node = MockNode()
    svc = service(node, saved)
    setup_case(node, svc, saved)
    plan = svc.prepare(request("append_version", local_version_id=88, onchain_version_id="7"))
    assert plan["commitments"]["local_version_id"] == 88
    assert plan["commitments"]["onchain_version_id"] == "7"
    assert plan["commitments"]["parent_content_hash"] == saved["versions"][0]["content_hash"]
    assert "public_evidence_uri_unavailable" in plan["warnings"]
    assert all(call["method"] != "eth_getLogs" for call in node.calls)


@pytest.mark.parametrize("fault,code", [("author", "unauthorized_author"), ("head", "stale_parent"),
                                       ("hash", "onchain_version_mismatch"), ("saved_parent", "saved_parent_mismatch")])
def test_append_requires_author_current_parent_and_saved_manifest(saved, fault, code):
    node = MockNode()
    svc = service(node, saved)
    setup_case(node, svc, saved, head=8 if fault == "head" else 7,
               author=REVIEWER if fault == "author" else AUTHOR,
               content_hash="a" * 64 if fault == "hash" else None)
    if fault == "saved_parent":
        saved["versions"][1]["parent_version_id"] = 100
    with pytest.raises(BotError, match=code):
        svc.prepare(request("append_version", local_version_id=88, onchain_version_id="7"))


def test_review_uses_canonical_saved_review_and_exact_version(saved):
    node = MockNode()
    svc = service(node, saved)
    setup_case(node, svc, saved)
    plan = svc.prepare(request("add_review", account=REVIEWER, onchain_version_id="7", local_review_id=21))
    assert plan["commitments"]["review_hash"] == saved["reviews"][0]["review_hash"]
    assert plan["commitments"]["content_hash"] == saved["versions"][0]["content_hash"]
    saved["reviews"][0]["comment"] += " changed"
    with pytest.raises(BotError, match="saved_review_commitment_mismatch"):
        svc.prepare(request("add_review", account=REVIEWER, onchain_version_id="7", local_review_id=21))


def test_saved_archive_is_reverified_before_rpc(saved):
    node = MockNode()
    archive = Path(saved["bundle_files"]["41"])
    archive.write_bytes(b"damaged archive")
    with pytest.raises(BotError, match="saved_bundle_unavailable"):
        service(node, saved).prepare(request("create_case"))
    assert node.calls == []


def mined_deployment(node, svc, **receipt_patch):
    node.values["eth_getTransactionReceipt"] = {"transactionHash": TX_HASH, "from": AUTHOR, "to": None,
        "blockNumber": "0x63", "blockHash": BLOCK_HASH, "status": "0x1", "contractAddress": CONTRACT,
        "logs": [], **receipt_patch}
    node.values["eth_getTransactionByHash"] = {"hash": TX_HASH, "from": AUTHOR, "to": None,
        "blockNumber": "0x63", "blockHash": BLOCK_HASH, "input": svc.artifact().bytecode,
        "value": "0x0", "chainId": "0x3c8"}
    node.values["eth_getBlockByNumber"] = {"number": "0x63", "hash": BLOCK_HASH, "transactions": [TX_HASH]}
    node.values["eth_getCode"] = svc.artifact().runtime


def verify_deploy(svc, minimum=2):
    return svc.verify({"chain_id": 968, "transaction_hash": TX_HASH,
                       "prepared": request(), "minimum_confirmations": minimum})


def test_verify_deployment_pending_confirmed_and_failed():
    node = MockNode()
    svc = service(node)
    assert verify_deploy(svc)["reason"] == "receipt_pending"
    mined_deployment(node, svc)
    result = verify_deploy(svc)
    assert result["status"] == "verified"
    assert result["confirmations"] == 2
    assert result["contract_address"] == CONTRACT
    assert result["verification_scope"] == "canonical_at_read_time_with_confirmations"
    assert verify_deploy(svc, 3)["reason"] == "confirmations_pending"
    node.values["eth_getTransactionReceipt"]["status"] = "0x0"
    assert verify_deploy(svc)["status"] == "failed"


@pytest.mark.parametrize("record,key,value,code", [
    ("eth_getTransactionByHash", "from", REVIEWER, "transaction_sender_mismatch"),
    ("eth_getTransactionByHash", "to", CONTRACT, "transaction_target_mismatch"),
    ("eth_getTransactionByHash", "input", "0x12", "transaction_input_mismatch"),
    ("eth_getTransactionByHash", "value", "0x1", "transaction_input_mismatch"),
    ("eth_getTransactionByHash", "chainId", "0x2a5", "transaction_chain_mismatch"),
    ("eth_getTransactionByHash", "blockHash", "0x" + "6" * 64, "transaction_block_mismatch"),
    ("eth_getBlockByNumber", "hash", "0x" + "6" * 64, "receipt_block_not_canonical"),
    ("eth_getTransactionReceipt", "contractAddress", None, "deployment_address_missing"),
])
def test_verify_strict_transaction_and_canonical_binding(record, key, value, code):
    node = MockNode()
    svc = service(node)
    mined_deployment(node, svc)
    node.values[record][key] = value
    result = verify_deploy(svc)
    assert result["status"] == ("pending" if code in {"transaction_block_mismatch", "receipt_block_not_canonical"} else "mismatch")
    assert result["reason"] == code


def test_verify_runtime_exact_and_chain_gate():
    node = MockNode()
    svc = service(node)
    mined_deployment(node, svc)
    node.values["eth_getCode"] = "0x1234"
    assert verify_deploy(svc)["reason"] == "contract_runtime_mismatch"
    node.values["eth_chainId"] = "0x2a5"
    with pytest.raises(BotError, match="chain_id_mismatch"):
        verify_deploy(svc)


def test_rpc_sanitization_whitelist_and_failed_request_budget():
    node = MockNode()
    node.values["eth_chainId"] = {"error": {"code": -1, "message": "secret provider details"}}
    with node.client() as client:
        rpc = BotRPC(968, client, max_calls=2)
        with pytest.raises(BotError, match="forbidden_rpc_method"):
            rpc.request("eth_getLogs", [])
        for _ in range(2):
            with pytest.raises(BotError) as exc:
                rpc.request("eth_chainId", [])
            assert str(exc.value) == "rpc_error"
        with pytest.raises(BotError, match="rpc_limit_reached"):
            rpc.request("eth_chainId", [])
    assert len(node.calls) == 2


def test_rpc_reply_bound_and_deadline(monkeypatch):
    def huge(req):
        return httpx.Response(200, content=b" " * 262145)
    with httpx.Client(transport=httpx.MockTransport(huge)) as client:
        rpc = BotRPC(968, client)
        with pytest.raises(BotError, match="rpc_response_too_large"):
            rpc.request("eth_chainId", [])
        monkeypatch.setattr("cluetide.bot.time.monotonic", lambda: rpc.deadline + 1)
        with pytest.raises(BotError, match="rpc_limit_reached"):
            rpc.request("eth_chainId", [])


@pytest.mark.parametrize("body", [
    b'{"jsonrpc":"2.0","id":1,"id":1,"result":"0x3c8"}',
    b'{"jsonrpc":"2.0","id":true,"result":"0x3c8"}',
    b'{"jsonrpc":"2.0","id":1,"result":NaN}',
    b'not json',
])
def test_rpc_json_envelope_strictness(body):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as client:
        rpc = BotRPC(968, client)
        with pytest.raises(BotError, match="invalid_rpc_response"):
            rpc.request("eth_chainId", [])


def test_rpc_conflicting_result_and_revert_preserves_validation_failure():
    revert = "0x" + keccak(text="UnknownCase()")[:4].hex()
    body = {"jsonrpc": "2.0", "id": 1, "result": "0x",
            "error": {"code": -32000, "message": "execution reverted", "data": revert}}
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        rpc = BotRPC(968, client)
        with pytest.raises(BotError, match="invalid_rpc_response"):
            rpc.request("eth_call", [], allowed_revert=revert)
        assert rpc.calls == 1


@pytest.mark.parametrize("error", [None, {"code": True, "message": "failure"},
                                     {"code": -32000, "message": 1}, {"message": "failure"}])
def test_rpc_error_object_schema(error):
    body = {"jsonrpc": "2.0", "id": 1, "error": error}
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        rpc = BotRPC(968, client)
        with pytest.raises(BotError, match="invalid_rpc_response"):
            rpc.request("eth_chainId", [])


def test_stream_deadline_checked_after_chunks(monkeypatch):
    ticks = iter([0, 0, 61])
    monkeypatch.setattr("cluetide.bot.time.monotonic", lambda: next(ticks))
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x3c8"}))) as client:
        rpc = BotRPC(968, client)
        with pytest.raises(BotError, match="rpc_limit_reached"):
            rpc.request("eth_chainId", [])


def test_verify_canonical_membership_and_actual_fees():
    node = MockNode()
    svc = service(node)
    mined_deployment(node, svc, gasUsed="0x5208", effectiveGasPrice=hex(10**9))
    result = verify_deploy(svc)
    assert result["gas_used"] == "21000"
    assert result["effective_gas_price_wei"] == "1000000000"
    assert result["actual_fee_wei"] == "21000000000000"
    assert result["gas_price_source"] == "receipt_effective_gas_price"
    assert result["read_observed_at_utc"].endswith("+00:00")
    node.values["eth_getBlockByNumber"]["transactions"] = []
    assert verify_deploy(svc)["reason"] == "transaction_not_in_canonical_block"


def test_verify_legacy_fee_fallback_and_dynamic_fee_unknown():
    node = MockNode()
    svc = service(node)
    mined_deployment(node, svc, gasUsed="0x5208")
    node.values["eth_getTransactionByHash"].update(gasPrice="0x5", type="0x0")
    result = verify_deploy(svc)
    assert result["gas_price_source"] == "transaction_legacy_gas_price"
    assert result["actual_fee_wei"] == "105000"
    node.values["eth_getTransactionByHash"]["type"] = "0x2"
    result = verify_deploy(svc)
    assert result["effective_gas_price_wei"] is None
    assert result["actual_fee_wei"] is None
    assert result["gas_price_source"] == "unavailable"


def test_saved_report_case_binding_is_checked_before_rpc(saved):
    node = MockNode()
    path = Path(saved["bundle_files"]["41"])
    manifest = export_bundle({"raw": {}}, {"case_id": "different-case"}, "# Other case\n", path)
    saved["versions"][0]["content_hash"] = manifest_sha256(manifest)
    with pytest.raises(BotError, match="saved_report_case_mismatch"):
        service(node, saved).prepare(request("create_case"))
    assert node.calls == []


def test_read_rejects_cross_case_version(saved):
    node = MockNode()
    svc = service(node, saved)
    setup_case(node, svc, saved)
    result = svc.read({"chain_id": 968, "contract_address": CONTRACT, "case_id": CASE_ID, "version_id": "7"})
    assert result["getter_state"]["version"]["content_hash"] == saved["versions"][0]["content_hash"]
    with pytest.raises(BotError, match="onchain_version_mismatch"):
        svc.read({"chain_id": 968, "contract_address": CONTRACT, "case_id": "different-case", "version_id": "7"})


def setup_inventory(node, svc, saved, *, version_count=2, review_count=1):
    node.values["eth_getCode"] = svc.artifact().runtime
    versions = {7 + index: [case_id_bytes(CASE_ID), 7 + index, 0 if index == 0 else 6 + index,
        AUTHOR, bytes.fromhex(saved["versions"][min(index, 1)]["content_hash"]),
        "cluetide.evidence.v1", "urn:cluetide:manifest:public", 100]
        for index in range(version_count)}
    review_hash = saved["reviews"][0]["review_hash"]
    reviews = {31 + index: [31 + index, case_id_bytes(CASE_ID), 7,
        bytes.fromhex(saved["versions"][0]["content_hash"]), REVIEWER, 2,
        bytes.fromhex(review_hash), "urn:cluetide:review:" + review_hash, 100]
        for index in range(review_count)}
    signatures = {"0x" + keccak(text=name + types)[:4].hex(): name for name, types in [
        ("getCase", "(bytes32)"), ("getVersion", "(uint256)"), ("getReview", "(uint256)"),
        ("getVersionId", "(bytes32,uint256)"), ("getReviewId", "(bytes32,uint256)")]}
    def call(payload):
        assert payload["params"][1] == "0x64"
        data = payload["params"][0]["data"]
        name = signatures[data[:10]]
        arguments = bytes.fromhex(data[10:])
        if name == "getCase":
            return getter_result(name, (AUTHOR, 6 + version_count, version_count, review_count, 100))
        if name in ("getVersionId", "getReviewId"):
            case_bytes, index = decode(["bytes32", "uint256"], arguments)
            assert case_bytes == case_id_bytes(CASE_ID)
            return getter_result(name, (7 if name == "getVersionId" else 31) + index)
        identifier = decode(["uint256"], arguments)[0]
        return getter_result(name, tuple((versions if name == "getVersion" else reviews)[identifier]))
    node.values["eth_call"] = call
    return versions, reviews


def test_read_unregistered_case_preserves_exact_revert_and_rpc_errors():
    node = MockNode()
    svc = service(node)
    node.values["eth_getCode"] = svc.artifact().runtime
    node.values["eth_call"] = {"error": {"code": -32000, "message": "execution reverted",
        "data": "0x" + keccak(text="UnknownCase()")[:4].hex()}}
    body = {"chain_id": 968, "contract_address": CONTRACT, "case_id": CASE_ID}
    result = svc.read(body)
    assert result["case_status"] == "unregistered"
    assert result["getter_state"] == {"case": None, "versions": [], "reviews": [], "inventory": {
        kind: {"offset": "0", "total_count": "0", "next_offset": None, "complete": True}
        for kind in ("versions", "reviews")}}
    assert result["read_block_number"] == "100"
    assert len(node.calls) == 6
    with pytest.raises(BotError, match="unknown_case"):
        svc.read({**body, "version_id": "7"})
    for error in [{"code": -32000, "message": "execution reverted", "data": "0x"},
                  {"code": -1, "message": "provider unavailable"}]:
        node.values["eth_call"] = {"error": error}
        with pytest.raises(BotError, match="rpc_error"):
            svc.read(body)
    node.values["eth_call"] = None
    with pytest.raises(BotError, match="invalid_getter_response"):
        svc.read(body)


def test_read_inventory_uses_case_indexes_and_one_block_with_bounded_pages(saved):
    node = MockNode()
    svc = service(node, saved)
    setup_inventory(node, svc, saved, version_count=4, review_count=5)
    body = {"chain_id": 968, "contract_address": CONTRACT, "case_id": CASE_ID,
            "version_id": "7", "review_id": "31"}
    result = svc.read(body)
    state = result["getter_state"]
    assert result["case_status"] == "registered"
    assert [version["version_id"] for version in state["versions"]] == ["7", "8", "9"]
    assert [review["review_id"] for review in state["reviews"]] == ["31", "32", "33"]
    assert state["inventory"]["versions"] == {"offset": "0", "total_count": "4", "next_offset": "3", "complete": False}
    assert state["inventory"]["reviews"] == {"offset": "0", "total_count": "5", "next_offset": "3", "complete": False}
    assert len(node.calls) == 20
    node.calls.clear()
    second = svc.read({**body, "version_offset": 3, "review_offset": 3})["getter_state"]
    assert [version["version_id"] for version in second["versions"]] == ["10"]
    assert [review["review_id"] for review in second["reviews"]] == ["34", "35"]
    assert second["inventory"]["reviews"]["next_offset"] is None
    assert second["inventory"]["reviews"]["complete"] is False
    assert len(node.calls) == 14
    with pytest.raises(BotError, match="inventory_offset_out_of_bounds"):
        svc.read({**body, "version_offset": 5})


@pytest.mark.parametrize("kind", ["version", "review"])
def test_inventory_rejects_cross_case_records(saved, kind):
    node = MockNode()
    svc = service(node, saved)
    versions, reviews = setup_inventory(node, svc, saved)
    if kind == "version":
        versions[7][0] = case_id_bytes("different-case")
    else:
        reviews[31][1] = case_id_bytes("different-case")
    with pytest.raises(BotError, match="onchain_" + kind + "_mismatch"):
        svc.read({"chain_id": 968, "contract_address": CONTRACT, "case_id": CASE_ID})


@pytest.mark.parametrize("uri", [None, "https://example.com/review-location"])
def test_prepare_review_detects_existing_commitment_across_uri(saved, uri):
    node = MockNode()
    svc = service(node, saved)
    setup_inventory(node, svc, saved)
    body = request("add_review", account=REVIEWER, onchain_version_id="7", local_review_id=21)
    if uri is not None:
        body["evidence_uri"] = uri
    with pytest.raises(BotError, match="review_exists"):
        svc.prepare(body)
    assert all(call["method"] != "eth_estimateGas" for call in node.calls)


@pytest.mark.parametrize("index,value", [(4, AUTHOR), (2, 8), (3, bytes.fromhex("a" * 64)),
                                         (5, 1), (6, bytes.fromhex("b" * 64))])
def test_prepare_review_distinguishes_author_version_content_decision_and_review(saved, index, value):
    node = MockNode()
    svc = service(node, saved)
    _, reviews = setup_inventory(node, svc, saved)
    reviews[31][index] = value
    plan = svc.prepare(request("add_review", account=REVIEWER, onchain_version_id="7", local_review_id=21))
    assert plan["commitments"]["review_hash"] == saved["reviews"][0]["review_hash"]
    assert plan["fees"]["sufficient_balance"] is True


def test_prepare_review_requires_complete_inventory_within_rpc_budget(saved):
    node = MockNode()
    svc = service(node, saved)
    _, reviews = setup_inventory(node, svc, saved, review_count=7)
    for review in reviews.values():
        review[4] = AUTHOR
    with pytest.raises(BotError, match="review_preflight_required"):
        svc.prepare(request("add_review", account=REVIEWER, onchain_version_id="7", local_review_id=21))
    assert len(node.calls) == 6
    assert all(call["method"] != "eth_estimateGas" for call in node.calls)


@pytest.mark.parametrize("value", [True, -1, "3", 2**53])
def test_inventory_offsets_are_strict_safe_nonnegative_integers(value):
    with pytest.raises(ValidationError):
        ReadRequest.model_validate({"chain_id": 968, "contract_address": CONTRACT, "case_id": CASE_ID,
                                    "version_offset": value})
