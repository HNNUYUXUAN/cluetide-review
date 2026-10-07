import hashlib
import json

import pytest

from cluetide.registry import LocalRegistry, RegistryError

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
