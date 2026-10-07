"""Security boundaries of public bundle exchange, independent of RPC/model calls."""

import hashlib
import json
from pathlib import Path
import stat
import warnings
import zipfile

import pytest

from cluetide.bundles import (
    BUNDLE_FORMAT,
    CANONICALIZATION,
    BundleValidationError,
    INTEGRITY_STATEMENT,
    canonical_json_bytes,
    export_bundle,
    import_bundle,
    manifest_sha256,
)


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


@pytest.fixture
def bundle(tmp_path):
    path = tmp_path / "case.zip"
    evidence = {
        "case_id": "ethereum-uniswap-93",
        "token_address": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
        "amount_raw": str(100_000_000 * 10**18),
        "raw": {"logs": [{"data": "0x" + "a" * 64, "topics": ["0x" + "b" * 64]}]},
    }
    manifest = export_bundle(evidence, {"status": "partial", "claim": "治理证据待复核"}, "# Report\n\n观察转账。", path)
    return path, evidence, manifest


def _payloads(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _write(path, payloads, *, compression=zipfile.ZIP_STORED, custom_info=None):
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, payload in payloads.items():
            archive.writestr(custom_info if custom_info is not None and custom_info.filename == name else name, payload)


def _rehash(payloads, name):
    manifest = json.loads(payloads["manifest.json"])
    manifest["files"][name] = {"sha256": hashlib.sha256(payloads[name]).hexdigest(), "size": len(payloads[name])}
    payloads["manifest.json"] = _json_bytes(manifest)


def test_round_trip_separates_raw_and_preserves_uint256_and_unicode(bundle):
    path, evidence, manifest = bundle
    result = import_bundle(path)
    assert result.verified is True
    assert result.evidence == {key: value for key, value in evidence.items() if key != "raw"}
    assert result.raw == evidence["raw"]
    assert isinstance(result.evidence["amount_raw"], str)
    assert result.report["claim"] == "治理证据待复核"
    assert result.validation["statement"] == INTEGRITY_STATEMENT
    assert result.validation["source_authenticity_verified"] is False
    assert result.validation["reviewer_independence_verified"] is False
    assert manifest["format"] == BUNDLE_FORMAT
    assert manifest["canonicalization"] == CANONICALIZATION
    assert "manifest.json" not in manifest["files"]
    payloads = _payloads(path)
    for name, entry in manifest["files"].items():
        assert entry == {"sha256": hashlib.sha256(payloads[name]).hexdigest(), "size": len(payloads[name])}
    assert payloads["evidence.json"] == _json_bytes(result.evidence)


def test_export_accepts_pydantic_style_model_dump(tmp_path):
    class Evidence:
        def model_dump(self, *, mode):
            assert mode == "json"
            return {"case_id": "demo", "raw": {"receipt": None}}

    path = tmp_path / "pydantic.zip"
    export_bundle(Evidence(), {}, "", path)
    assert import_bundle(path).raw == {"receipt": None}


@pytest.mark.parametrize("filename", ["case.json", "case", ".env", ".env.local", ".env.backup.zip"])
def test_export_requires_a_zip_path_and_preserves_existing_other_files(tmp_path, filename):
    path = tmp_path / filename
    path.write_bytes(b"existing file")
    with pytest.raises(BundleValidationError, match="public evidence archive"):
        export_bundle({"raw": {}}, {}, "", path)
    assert path.read_bytes() == b"existing file"


def test_import_rejects_environment_filenames_before_opening(tmp_path):
    with pytest.raises(BundleValidationError, match="Environment files"):
        import_bundle(tmp_path / ".env")


def test_export_is_deterministic_and_does_not_mutate_input(bundle, tmp_path):
    original_path, evidence, _ = bundle
    second_path = tmp_path / "second.zip"
    export_bundle(evidence, {"claim": "治理证据待复核", "status": "partial"}, "# Report\n\n观察转账。", second_path)
    assert original_path.read_bytes() == second_path.read_bytes()
    assert "raw" in evidence


def test_manifest_commitment_matches_exact_archive_manifest_bytes(bundle):
    path, _, manifest = bundle
    payloads = _payloads(path)
    assert canonical_json_bytes(manifest) == payloads["manifest.json"]
    assert manifest_sha256(manifest) == hashlib.sha256(payloads["manifest.json"]).hexdigest()
    assert len(manifest_sha256(manifest)) == 64
    assert canonical_json_bytes({"b": "观察", "a": 1}) == '{"a":1,"b":"观察"}'.encode("utf-8")


def test_canonicalization_uses_utf16_key_ordering_and_jcs_number_rendering():
    assert canonical_json_bytes({"\ue000": 1, "\U0001f600": 2}) == '{"😀":2,"":1}'.encode("utf-8")
    assert canonical_json_bytes({"number": 2.0, "negative_zero": -0.0}) == b'{"negative_zero":0,"number":2}'
    assert canonical_json_bytes({"value": (1 << 53) - 1}) == b'{"value":9007199254740991}'
    assert canonical_json_bytes({"value": -((1 << 53) - 1)}) == b'{"value":-9007199254740991}'


@pytest.mark.parametrize("value", [1 << 53, -(1 << 53), 10**26, "\ud800"])
def test_canonicalization_rejects_unsafe_integers_and_invalid_unicode(value):
    with pytest.raises(BundleValidationError, match="canonical JSON"):
        canonical_json_bytes({"value": value})


def test_tampered_file_is_rejected(bundle):
    path, _, _ = bundle
    payloads = _payloads(path)
    payloads["report.md"] = payloads["report.md"].replace(b"Report", b"Tamper")
    _write(path, payloads)
    with pytest.raises(BundleValidationError, match="integrity"):
        import_bundle(path)


@pytest.mark.parametrize("unsafe_name", ["../raw.json", "/raw.json", "C:/raw.json", "C:\\raw.json", "sub/raw.json", ".env", "keys.json"])
def test_unsafe_or_unknown_filename_is_rejected_without_extracting(bundle, tmp_path, unsafe_name):
    path, _, _ = bundle
    payloads = _payloads(path)
    payloads[unsafe_name] = payloads.pop("raw.json")
    _write(path, payloads)
    with pytest.raises(BundleValidationError, match="unsafe path"):
        import_bundle(path)
    assert not (tmp_path.parent / "raw.json").exists()


def test_extra_file_is_rejected_even_if_added_to_manifest(bundle):
    path, _, _ = bundle
    payloads = _payloads(path)
    payloads["extra.txt"] = b"extra"
    manifest = json.loads(payloads["manifest.json"])
    manifest["files"]["extra.txt"] = {"sha256": hashlib.sha256(b"extra").hexdigest(), "size": 5}
    payloads["manifest.json"] = _json_bytes(manifest)
    _write(path, payloads)
    with pytest.raises(BundleValidationError, match="exactly five"):
        import_bundle(path)


def test_duplicate_filename_is_rejected(bundle):
    path, _, _ = bundle
    payloads = _payloads(path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as archive:
            for name, payload in payloads.items():
                if name != "report.md":
                    archive.writestr(name, payload)
            archive.writestr("raw.json", b"{}")
    with pytest.raises(BundleValidationError, match="duplicate"):
        import_bundle(path)


@pytest.mark.parametrize("file_type", [stat.S_IFLNK, stat.S_IFIFO])
def test_symlink_and_special_file_are_rejected(bundle, file_type):
    path, _, _ = bundle
    payloads = _payloads(path)
    info = zipfile.ZipInfo("raw.json")
    info.create_system = 3
    info.external_attr = (file_type | 0o644) << 16
    _write(path, payloads, custom_info=info)
    with pytest.raises(BundleValidationError, match="non-regular"):
        import_bundle(path)


def test_compression_bomb_is_rejected_before_json_read(bundle):
    path, _, _ = bundle
    payloads = _payloads(path)
    payloads["raw.json"] = b" " * 300_000
    _write(path, payloads, compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(BundleValidationError, match="compression ratio"):
        import_bundle(path)


def test_per_file_and_total_size_limits_are_enforced(bundle):
    path, _, _ = bundle
    with pytest.raises(BundleValidationError, match="file exceeds"):
        import_bundle(path, max_file_bytes=10)
    with pytest.raises(BundleValidationError, match="total size"):
        import_bundle(path, max_total_bytes=100)


@pytest.mark.parametrize("size", [-1, True, "123", 1.5])
def test_invalid_manifest_size_is_rejected(bundle, size):
    path, _, _ = bundle
    payloads = _payloads(path)
    manifest = json.loads(payloads["manifest.json"])
    manifest["files"]["raw.json"]["size"] = size
    payloads["manifest.json"] = _json_bytes(manifest)
    _write(path, payloads)
    with pytest.raises(BundleValidationError, match="byte size is malformed"):
        import_bundle(path)


@pytest.mark.parametrize("digest", ["a" * 63, "g" * 64, "A" * 64, 12])
def test_invalid_digest_is_rejected(bundle, digest):
    path, _, _ = bundle
    payloads = _payloads(path)
    manifest = json.loads(payloads["manifest.json"])
    manifest["files"]["raw.json"]["sha256"] = digest
    payloads["manifest.json"] = _json_bytes(manifest)
    _write(path, payloads)
    with pytest.raises(BundleValidationError, match="digest is malformed"):
        import_bundle(path)


@pytest.mark.parametrize("field", ["api_key", "Authorization", "privateKey", "password", "credentials", ".env", "keys", "TOKENDANCE_API_KEY", "ethereum_private_key", "X-Auth-Token", "Cookie"])
def test_nested_credentials_are_rejected_before_export_and_existing_file_survives(tmp_path, field):
    path = tmp_path / "case.zip"
    path.write_bytes(b"previous public archive")
    with pytest.raises(BundleValidationError, match="credential field"):
        export_bundle({"raw": {"nested": [{field: "sensitive-value"}]}}, {}, "", path)
    assert path.read_bytes() == b"previous public archive"
    assert not list(tmp_path.glob(".cluetide-bundle-*"))


@pytest.mark.parametrize("credential", [
    "Authorization: Bearer abcdefghijklmnop12345",
    "sk-testabcdefghijklmnop123456",
    "-----BEGIN RSA PRIVATE KEY-----",
    "https://eth-mainnet.g.alchemy.com/v2/abcdef1234567890",
    "https://mainnet.infura.io/v3/abcdef1234567890",
    "https://user:password@rpc.example.test",
    "AKIA1234567890123456",
    "TOKENDANCE_API_KEY=abcdefghijklmnop123456",
])
def test_recognizable_credentials_are_rejected_in_markdown(tmp_path, credential):
    with pytest.raises(BundleValidationError, match="recognizable credential"):
        export_bundle({"raw": {}}, {}, credential, tmp_path / "case.zip")


def test_credentials_are_rejected_on_import_even_with_correct_hashes(bundle):
    path, _, _ = bundle
    payloads = _payloads(path)
    payloads["report.json"] = _json_bytes({"observations": [{"api_key": "credential-value"}]})
    _rehash(payloads, "report.json")
    _write(path, payloads)
    with pytest.raises(BundleValidationError, match="credential field"):
        import_bundle(path)


def test_duplicate_json_keys_are_rejected_even_with_correct_hashes(bundle):
    path, _, _ = bundle
    payloads = _payloads(path)
    payloads["report.json"] = b'{"claim":"first","claim":"second"}'
    _rehash(payloads, "report.json")
    _write(path, payloads)
    with pytest.raises(BundleValidationError, match="Invalid UTF-8 JSON"):
        import_bundle(path)


@pytest.mark.parametrize("payload", [b'{ "claim": "noncanonical" }', b'{"value":9007199254740992}'])
def test_import_enforces_declared_canonicalization_even_with_correct_hashes(bundle, payload):
    path, _, _ = bundle
    payloads = _payloads(path)
    payloads["report.json"] = payload
    _rehash(payloads, "report.json")
    _write(path, payloads)
    with pytest.raises(BundleValidationError, match="canonical JSON"):
        import_bundle(path)


def test_manifest_bytes_must_be_canonical_for_reproducible_commitments(bundle):
    path, _, _ = bundle
    payloads = _payloads(path)
    payloads["manifest.json"] = b" " + payloads["manifest.json"]
    _write(path, payloads)
    with pytest.raises(BundleValidationError, match="manifest.json is not RFC 8785 canonical JSON"):
        import_bundle(path)


@pytest.mark.parametrize("payload", [b'{"value":NaN}', b'{"value":Infinity}', b'{"value":1e999}', b'\xff'])
def test_invalid_or_non_finite_json_is_rejected(bundle, payload):
    path, _, _ = bundle
    payloads = _payloads(path)
    payloads["report.json"] = payload
    _rehash(payloads, "report.json")
    _write(path, payloads)
    with pytest.raises(BundleValidationError):
        import_bundle(path)


@pytest.mark.parametrize("bad_evidence", [{"raw": {}, "value": float("nan")}, {"raw": {}, 1: "nonstring"}, {"raw": {}, "value": object()}])
def test_export_rejects_non_json_values(tmp_path, bad_evidence):
    with pytest.raises(BundleValidationError):
        export_bundle(bad_evidence, {}, "", tmp_path / "case.zip")


def test_manifest_cannot_hash_itself_or_omit_a_data_file(bundle):
    path, _, _ = bundle
    payloads = _payloads(path)
    manifest = json.loads(payloads["manifest.json"])
    manifest["files"]["manifest.json"] = manifest["files"].pop("raw.json")
    payloads["manifest.json"] = _json_bytes(manifest)
    _write(path, payloads)
    with pytest.raises(BundleValidationError, match="exactly the four"):
        import_bundle(path)


def test_unknown_manifest_version_is_rejected(bundle):
    path, _, _ = bundle
    payloads = _payloads(path)
    manifest = json.loads(payloads["manifest.json"])
    manifest["format"] = "cluetide-public-evidence/v2"
    payloads["manifest.json"] = _json_bytes(manifest)
    _write(path, payloads)
    with pytest.raises(BundleValidationError, match="unsupported manifest format"):
        import_bundle(path)


def test_unknown_canonicalization_profile_is_rejected(bundle):
    path, _, _ = bundle
    payloads = _payloads(path)
    manifest = json.loads(payloads["manifest.json"])
    manifest["canonicalization"] = "another-json-profile"
    payloads["manifest.json"] = _json_bytes(manifest)
    _write(path, payloads)
    with pytest.raises(BundleValidationError, match="canonicalization profile"):
        import_bundle(path)


def test_imported_raw_cannot_be_hidden_in_evidence_json(bundle):
    path, _, _ = bundle
    payloads = _payloads(path)
    evidence = json.loads(payloads["evidence.json"])
    evidence["raw"] = {"logs": []}
    payloads["evidence.json"] = _json_bytes(evidence)
    _rehash(payloads, "evidence.json")
    _write(path, payloads)
    with pytest.raises(BundleValidationError, match="confined to raw.json"):
        import_bundle(path)


def test_invalid_zip_is_a_validation_error(tmp_path):
    path = tmp_path / "case.zip"
    path.write_bytes(b"this is not a zip archive")
    with pytest.raises(BundleValidationError, match="invalid or unreadable"):
        import_bundle(path)


def test_missing_archive_does_not_include_path_in_validation_message(tmp_path):
    path = tmp_path / "nonexistent-private-filename.zip"
    with pytest.raises(BundleValidationError, match="invalid or unreadable") as error:
        import_bundle(path)
    assert str(path) not in str(error.value)
