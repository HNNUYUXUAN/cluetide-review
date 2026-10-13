"""Cross-language exchange limits, original bytes and portable receipt evidence."""
import copy
import io
import json
from pathlib import Path
import socket
import time
import zipfile

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from cluetide.bundles import canonical_json_bytes
from cluetide.release_exchange import (MAX_PACKAGE_BYTES, PUBLIC_CONTRACT, read_zip, strict_json, validate_task,
                                      verify_package)
from cluetide.release_server import create_release_app
from cluetide.releases import CreateTask, CreateVersion, ReleaseService, digest
from test_releases import release


def make_zip(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries:
            archive.writestr(name, value)
    return output.getvalue()


def test_service_package_has_whitelisted_roles_and_complete_selected_history(release):
    service, _, task, _, _ = release
    service.create_version(task["task_id"], {"summary": "Version two", "source_reference": "git:two", "artifact_text": "\ufeff<p>中文😀</p>\r\n",
        "test_evidence": ["Bytes checked"], "expires_at": int(time.time()) + 3600})
    first, second = (verify_package(service.bundle(task["task_id"], n)) for n in (1, 2))
    assert len(first["task"]["versions"]) == 1
    assert len(second["task"]["versions"]) == 2
    assert second["complete_history"] and not second["current_chain_verified"]
    task_bytes = read_zip(service.bundle(task["task_id"], 2))["task.json"]
    value = strict_json(task_bytes)
    assert set(value) == {"task_id", "title", "created_at", "owner", "proposer", "reviewer", "executor", "receipt_signer",
        "target", "target_id", "version_count", "versions", "attempts", "chain_records"}
    assert second["task"]["versions"][1]["artifact_text"].encode("utf-8") == b"\xef\xbb\xbf<p>\xe4\xb8\xad\xe6\x96\x87\xf0\x9f\x98\x80</p>\r\n"
    assert b"write_key" not in task_bytes


@pytest.mark.parametrize("number,manifest_hash,artifact_hash", [
    (1, "0x3f1041f90ed5cbcf6971e564f7471ac93c92a8d5f9817ad53d3525bf4d7ea3e5", "0x47b7d3ccc731cc6eac707e070d90b3dc73edaf21de80a939ad659d496e45e747"),
    (2, "0x0db83692d90ac3916706664e6ee1cedd70b07b848c0a931e68ab4609b12c7b37", "0xd90118354c28a4f730e628303f8984c795066e6bcfb7075f4118fd1032f77881")])
def test_recorded_mainnet_originals_retain_their_committed_hashes(number, manifest_hash, artifact_hash):
    directory = Path(__file__).resolve().parents[1] / "data/demo/public-wallet-20261012"
    task = validate_task(strict_json((directory / "task.json").read_bytes()))
    version = task["versions"][number - 1]
    assert digest((directory / f"v{number}-manifest.json").read_bytes()) == version["manifest_hash"] == manifest_hash
    assert digest((directory / f"v{number}-artifact.html").read_bytes()) == version["artifact_hash"] == artifact_hash
    assert version["artifact_text"].encode("utf-8") == (directory / f"v{number}-artifact.html").read_bytes()


@pytest.mark.parametrize("member", ["manifest.json", "artifact.html", "task.json"])
def test_duplicate_representation_tampering_is_rejected(release, member):
    service, _, task, _, _ = release
    entries = read_zip(service.bundle(task["task_id"], 1))
    if member == "manifest.json":
        value = strict_json(entries[member]); value["summary"] = "Changed"
        entries[member] = canonical_json_bytes(value)
    elif member == "task.json":
        value = strict_json(entries[member]); value["versions"][0]["artifact_text"] += "Changed"
        entries[member] = canonical_json_bytes(value)
    else:
        entries[member] += b"Changed"
    with pytest.raises(ValueError):
        verify_package(make_zip(entries.items()))


@pytest.mark.parametrize("entries", [
    [("task.json", b"{}"), ("task.json", b"{}")], [("../task.json", b"{}")], [("/task.json", b"{}")],
    [("task.json/", b"{}")], [("C:task.json", b"{}")],
    [("file" + str(i), b"x") for i in range(257)], [("task.json", b"a" * (MAX_PACKAGE_BYTES + 1))]])
def test_zip_member_and_expansion_boundaries(entries):
    with pytest.raises(ValueError):
        read_zip(make_zip(entries))


def test_zip_backslash_paths_are_rejected_before_normalization():
    payload = make_zip([("folder/task.json", b"{}")]).replace(b"folder/task.json", b"folder\\task.json")
    with pytest.raises(ValueError):
        read_zip(payload)


@pytest.mark.parametrize("raw", [b'{"task_id":1,"task_id":2}', b'{"a":{"b":1,"b":2}}', b'{"a":NaN}'])
def test_json_duplicate_keys_and_nonfinite_values_are_rejected(raw):
    with pytest.raises(ValueError):
        strict_json(raw)


def test_legacy_two_file_and_sparse_packages_remain_inspectable(release):
    service, _, task, _, _ = release
    entries = read_zip(service.bundle(task["task_id"], 1))
    del entries["task.json"]
    result = verify_package(make_zip(entries.items()))
    assert result["chain_roles_required"] and not result["complete_history"]
    assert result["selected_version"]["version_id"] == 1


def test_original_manifest_whitespace_bom_and_past_deadline_are_preserved(release):
    service, _, task, _, _ = release
    value = verify_package(service.bundle(task["task_id"], 1))["task"]
    version = value["versions"][0]
    version["manifest"]["summary"] = " "
    version["summary"] = " "
    version["manifest"]["expires_at"] = 1
    version["expires_at"] = 1
    original = "\ufeff" + json.dumps(version["manifest"], ensure_ascii=False, indent=2).replace("\n", "\r\n") + "\r\n"
    version["manifest_text"] = original
    version["manifest_hash"] = digest(original.encode("utf-8"))
    verified = validate_task(value)
    assert verified["versions"][0]["manifest_text"] == original
    assert verified["versions"][0]["manifest"]["summary"] == " "


def test_new_text_has_scalar_and_nonblank_limits():
    base = {"summary": "😀" * 1000, "source_reference": "ref", "test_evidence": ["passed"],
            "artifact_text": "<p>😀</p>", "expires_at": 2_000_000_000}
    assert CreateVersion.model_validate(base).summary == base["summary"]
    for change in ({"summary": "😀" * 1001}, {"summary": "\t"}, {"source_reference": " "},
                   {"test_evidence": ["\n"]}, {"artifact_text": " "}, {"summary": "\ud800"}):
        with pytest.raises((ValidationError, UnicodeError)):
            CreateVersion.model_validate({**base, **change})
    with pytest.raises(ValidationError):
        CreateTask.model_validate({"title": " ", "owner": "0x" + "11" * 20, "proposer": "0x" + "11" * 20,
                                  "reviewer": "0x" + "22" * 20, "executor": "0x" + "11" * 20})


def test_version_request_supports_escaped_32kib_but_other_routes_keep_limit(release, tmp_path):
    service, _, task, _, _ = release
    key = service.issue_task_write_key(task["task_id"])
    app = create_release_app(service=service, dist=tmp_path / "missing")
    payload = {"summary": "Boundary", "source_reference": "ref", "test_evidence": ["passed"],
               "artifact_text": "x" + "\x01" * 32767, "expires_at": int(time.time()) + 3600}
    raw = json.dumps(payload).encode()
    assert len(raw) > 60 * 1024
    with TestClient(app) as client:
        result = client.post(f'/api/releases/tasks/{task["task_id"]}/versions', content=raw,
                             headers={"X-ClueTide-Task-Key": key, "Content-Type": "application/json"})
        assert result.status_code == 201
        assert client.post("/api/releases/prepare", content=raw).status_code == 413
        assert client.post("/api/releases/imports", content=b" " * (512 * 1024 + 1)).status_code == 413


def test_acceptance_verification_is_offline_and_authenticates_original_receipt(release, monkeypatch):
    service, chain, task, _, request = release
    chain.change["contract_address"] = PUBLIC_CONTRACT
    monkeypatch.setattr(chain, "config", lambda: {"status": "configured", "chain_id": 677, "contract_address": PUBLIC_CONTRACT})
    service.execute(task["task_id"], request)
    local = verify_package(service.bundle(task["task_id"], 1))["task"]
    version = local["versions"][0]
    envelope = service.receipt("1", task["task_id"])
    entries = {"task.json": canonical_json_bytes(local), "versions/1/manifest.json": version["manifest_text"].encode(),
        "versions/1/artifact.html": version["artifact_text"].encode(), "receipt.json": json.dumps(envelope, indent=2).encode(),
        "published/artifact.html": service.artifact(task["task_id"])}
    metadata = {"schema": "cluetide.acceptance-bundle.v1", "chain_id": 677,
        "contract_address": envelope["receipt"]["contract_address"], "task_id": task["task_id"], "version_id": 1, "attempt_id": "1",
        "observed_at": "2026-10-13T00:00:00Z", "start_reference": {"schema": "cluetide.start-reference.v1",
            "task_id": task["task_id"], "version_id": 1, "transaction_hash": request["start_transaction_hash"]},
        "transactions": [{"action": "start_execution", "transaction_hash": request["start_transaction_hash"]}],
        "observations": {"chain": {"verified": True, "confirmations": 999}},
        "files": [{"path": name, "bytes": len(raw), "sha256": digest(raw)} for name, raw in entries.items()]}
    entries["acceptance.json"] = canonical_json_bytes(metadata)
    def network_forbidden(*args, **kwargs):
        raise AssertionError("Offline verification attempted network access")
    monkeypatch.setattr(socket, "socket", network_forbidden)
    result = verify_package(make_zip(entries.items()))
    assert result["receipt_signature_valid"] and result["stored_artifact_matches"]
    assert not result["current_chain_verified"] and not result["live_artifact_verified"]
    envelope["receipt"]["authorization_tx"] = "0x" + "ef" * 32
    entries["receipt.json"] = canonical_json_bytes(envelope)
    metadata["files"] = [{"path": name, "bytes": len(raw), "sha256": digest(raw)} for name, raw in entries.items() if name != "acceptance.json"]
    entries["acceptance.json"] = canonical_json_bytes(metadata)
    with pytest.raises(ValueError):
        verify_package(make_zip(entries.items()))
