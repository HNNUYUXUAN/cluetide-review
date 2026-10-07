import hashlib
import json

import httpx
import pytest
from eth_abi import encode

from cluetide.registry import GetterOnlyReader, LocalRegistry, RegistryError, case_id_bytes

HASH_1 = hashlib.sha256(b"bundle v1").hexdigest()
HASH_2 = hashlib.sha256(b"bundle v2").hexdigest()
REVIEW_HASH = hashlib.sha256(b"review v1").hexdigest()


def test_local_review_remains_bound_to_original_version(tmp_path):
    path = tmp_path / "registry.json"
    registry = LocalRegistry(path)
    first = registry.create_case("uniswap-93", HASH_1)
    review = registry.add_review("uniswap-93", first["version_id"], HASH_1, REVIEW_HASH)
    second = registry.append_version("uniswap-93", first["version_id"], HASH_2)
    assert second["parent_version_id"] == first["version_id"]
    assert registry.get_review(review["review_id"])["content_hash"] == HASH_1
    assert registry.get_case("uniswap-93")["head_version_id"] == second["version_id"]
    persisted_bytes = path.read_bytes()
    reloaded = LocalRegistry(path)
    assert reloaded.export_state() == registry.export_state()
    assert path.read_bytes() == persisted_bytes  # Loading cannot rewrite partial replay state.
    assert registry.mode == "local_state_simulation"


def test_local_permissions_parent_and_hash_binding():
    registry = LocalRegistry()
    first = registry.create_case("case-a", HASH_1)
    other = registry.create_case("case-b", HASH_1)
    with pytest.raises(RegistryError, match="unauthorized_author"):
        registry.append_version("case-a", first["version_id"], HASH_2, author="reviewer")
    with pytest.raises(RegistryError, match="parent_case_mismatch"):
        registry.append_version("case-a", other["version_id"], HASH_2)
    registry.append_version("case-a", first["version_id"], HASH_2)
    with pytest.raises(RegistryError, match="stale_parent"):
        registry.append_version("case-a", first["version_id"], HASH_2)
    with pytest.raises(RegistryError, match="review_hash_mismatch"):
        registry.add_review("case-a", first["version_id"], HASH_2, REVIEW_HASH)
    with pytest.raises(RegistryError, match="review_case_mismatch"):
        registry.add_review("case-a", other["version_id"], HASH_1, REVIEW_HASH)


def test_local_returns_copies_and_rejects_tampered_saved_links(tmp_path):
    path = tmp_path / "registry.json"
    registry = LocalRegistry(path)
    first = registry.create_case("case-a", HASH_1)
    first["author"] = "intruder"
    assert registry.get_version(1)["author"] == "local-author"
    saved = registry.export_state()
    saved["cases"]["case-a"]["head_version_id"] = 99
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(RegistryError, match="invalid_simulation_state"):
        LocalRegistry(path)
    assert json.loads(path.read_text())["cases"]["case-a"]["head_version_id"] == 99


@pytest.mark.parametrize("field", ["cases", "versions", "reviews"])
def test_local_rejects_malformed_persistence_shape(tmp_path, field):
    path = tmp_path / "registry.json"
    saved = LocalRegistry().export_state()
    saved[field] = []
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(RegistryError, match="invalid_simulation_state"):
        LocalRegistry(path)


def test_getter_only_reader_uses_known_ids_without_logs():
    calls = []
    author = "0x" + "12" * 20

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        if body["method"] == "eth_chainId":
            result = hex(677)
        else:
            assert body["method"] == "eth_call"
            assert body["params"][0]["to"] == "0x" + "34" * 20
            result = "0x" + encode(["(address,uint256,uint256,uint256,uint64)"], [(author, 2, 2, 1, 10)]).hex()
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        reader = GetterOnlyReader("https://rpc.botchain.ai", "0x" + "34" * 20, client=client)
        assert reader.get_case("uniswap-93")["head_version_id"] == 2
        assert reader.get_case("uniswap-93")["review_count"] == 1
        with pytest.raises(RegistryError, match="forbidden_rpc_method"):
            reader._request("eth_getLogs", [])
        with pytest.raises(RegistryError, match="forbidden_rpc_method"):
            reader._request("eth_sendRawTransaction", [])
        with pytest.raises(RegistryError, match="forbidden_getter"):
            reader._call("appendVersion", [])
    assert [call["method"] for call in calls] == ["eth_chainId", "eth_call", "eth_call"]


def test_getter_reader_decodes_exact_version_and_review_bindings():
    author = "0x" + "12" * 20
    responses = [hex(677),
                 "0x" + encode(["(bytes32,uint256,uint256,address,bytes32,string,string,uint64)"],
                                [(case_id_bytes("case-a"), 2, 1, author, bytes.fromhex(HASH_2), "v1", "bundle://v2", 9)]).hex(),
                 "0x" + encode(["(uint256,bytes32,uint256,bytes32,address,uint8,bytes32,string,uint64)"],
                                [(1, case_id_bytes("case-a"), 1, bytes.fromhex(HASH_1), author, 2,
                                  bytes.fromhex(REVIEW_HASH), "bundle://review/v1", 8)]).hex()]

    def handle(request):
        body = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": responses.pop(0)})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        reader = GetterOnlyReader("https://rpc.botchain.ai", "0x" + "34" * 20, client=client)
        version = reader.get_version(2)
        review = reader.get_review(1)
        assert version["content_hash"] == HASH_2
        assert review["content_hash"] == HASH_1
        assert review["version_id"] == 1
        assert review["decision"] == "correction_requested"


def test_getter_reader_chain_mismatch_stops_before_contract_call():
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body["method"])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": "0x1"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        reader = GetterOnlyReader("https://rpc.botchain.ai", "0x" + "34" * 20, client=client)
        with pytest.raises(RegistryError, match="chain_id_mismatch"):
            reader.get_case("case-a")
    assert calls == ["eth_chainId"]


def test_getter_reader_sanitizes_provider_errors_and_transport_urls():
    def handle(request):
        raise httpx.ConnectError("untrusted detail secret-value", request=request)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        reader = GetterOnlyReader("https://rpc.example.invalid?key=secret-value", "0x" + "34" * 20, client=client)
        with pytest.raises(RegistryError) as error:
            reader.get_case("case-a")
        assert str(error.value) == "rpc_transport_error"
        assert error.value.__cause__ is None
