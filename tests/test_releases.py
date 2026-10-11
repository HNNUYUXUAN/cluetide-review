"""Real filesystem publication, consumed-grant binding and independent receipt checks."""
import copy
import io
import json
from pathlib import Path
import time
import zipfile

from eth_account import Account
from eth_account.messages import encode_typed_data
import pytest
from pydantic import ValidationError

from cluetide.bundles import canonical_json_bytes
from cluetide.releases import (ChainGateway, CreateTask, CreateVersion, ReleaseError, ReleaseService,
                               digest, receipt_typed_data)


OWNER = "0x" + "1" * 40
REVIEWER = "0x" + "2" * 40
EXECUTOR = "0x" + "3" * 40
CONTRACT = "0x" + "4" * 40
TX = "0x" + "5" * 64


class ConfirmedChain:
    """A test-only boundary fixture; the production gateway always reads RPC."""
    def __init__(self):
        self.calls = 0
        self.change = {}
        self.error = False

    def config(self):
        return {"status": "configured", "chain_id": 677, "contract_address": CONTRACT}

    def authorize_execution(self, *, task, version, attempt_id, start_transaction_hash):
        self.calls += 1
        if self.error:
            raise ReleaseError("The chain authorization could not be verified.", 503)
        return {"status": "verified", "chain_id": 677, "contract_address": CONTRACT, "task_id": task["task_id"],
                "version_id": version["version_id"], "manifest_hash": version["manifest_hash"], "artifact_hash": version["artifact_hash"],
                "target_id": task["target_id"], "executor": task["executor"], "receipt_signer": task["receipt_signer"],
                "attempt_id": attempt_id, "authorization_tx": start_transaction_hash,
                "block_number": 123, "block_hash": "0x" + "6" * 64, "confirmations": 3, **self.change}


@pytest.fixture
def release(tmp_path):
    chain = ConfirmedChain()
    service = ReleaseService(tmp_path / "releases", chain=chain, signing_key=bytes.fromhex("07" * 32))
    task = service.create_task({"title": "Release the approved build", "owner": OWNER, "proposer": OWNER,
                                "reviewer": REVIEWER, "executor": EXECUTOR})
    version = service.create_version(task["task_id"], {"summary": "Publish the approved home page", "source_reference": "git:977c43a",
        "artifact_text": "<!doctype html><html lang=\"en\"><h1>Approved release</h1></html>",
        "test_evidence": ["Build completed; isolated artifact checked."], "expires_at": int(time.time()) + 3600})
    request = {"version_id": 1, "attempt_id": "1", "start_transaction_hash": TX}
    return service, chain, task, version, request


def test_actual_publication_and_independent_eip712_receipt(release):
    service, chain, task, version, request = release
    attempt = service.execute(task["task_id"], request)
    assert attempt["status"] == "succeeded"
    artifact = service.artifact(task["task_id"])
    assert digest(artifact) == version["artifact_hash"]
    envelope = service.receipt("1", task["task_id"])
    assert envelope["verification"] == {"signature_valid": True, "artifact_matches": True, "current_target_matches": True,
                                         "scope": "Executor signature and available local artifact bytes"}
    message = encode_typed_data(full_message=receipt_typed_data(envelope["receipt"], envelope["receipt_hash"]))
    assert Account.recover_message(message, signature=envelope["signature"]).lower() == task["receipt_signer"]
    from cluetide.release_chain import recover_receipt_signer
    assert recover_receipt_signer(677, CONTRACT, task["task_id"], 1, 1, envelope["receipt_hash"], 1,
                                  envelope["signature"]) == task["receipt_signer"]
    assert chain.calls == 1


def test_package_download_preserves_canonical_manifest_and_prebuilt_bytes(release):
    service, _, task, version, _ = release
    first = service.bundle(task["task_id"], 1)
    assert first == service.bundle(task["task_id"], 1)
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == ["manifest.json", "artifact.html"]
        manifest = archive.read("manifest.json")
        assert digest(manifest) == version["manifest_hash"]
        assert canonical_json_bytes(json.loads(manifest)) == manifest
        assert digest(archive.read("artifact.html")) == version["artifact_hash"]


def test_roles_and_manifest_input_limits(release):
    service, _, task, _, _ = release
    with pytest.raises(ValidationError):
        service.create_task({"title": "Same role", "owner": OWNER, "proposer": OWNER, "reviewer": OWNER, "executor": EXECUTOR})
    for extra in ({"command": "anything"}, {"target_path": "../outside"}, {"receipt_signer": REVIEWER}):
        with pytest.raises(ValidationError):
            service.create_task({"title": "Fixed scope", "owner": OWNER, "proposer": OWNER, "reviewer": REVIEWER,
                                 "executor": EXECUTOR, **extra})
    with pytest.raises(ValidationError):
        CreateVersion.model_validate({"summary": "Build", "source_reference": "git:test", "artifact_text": "é" * 20000,
            "test_evidence": ["checked"], "expires_at": int(time.time()) + 3600})


@pytest.mark.parametrize("change", [
    {"status": "pending"}, {"chain_id": 968}, {"version_id": 2}, {"attempt_id": "2"}, {"confirmations": 2},
    {"manifest_hash": "0x" + "7" * 64}, {"artifact_hash": "0x" + "7" * 64}, {"target_id": "0x" + "7" * 64},
    {"executor": OWNER}, {"receipt_signer": OWNER}, {"authorization_tx": "0x" + "7" * 64}])
def test_inexact_or_unconfirmed_grant_cannot_publish(release, change):
    service, chain, task, _, request = release
    chain.change = change
    with pytest.raises(ReleaseError):
        service.execute(task["task_id"], request)
    assert not (service.root / "targets").exists()
    assert not (service.root / "attempts").exists()


def test_rpc_failure_keeps_execution_unstarted(release):
    service, chain, task, _, request = release
    chain.error = True
    with pytest.raises(ReleaseError):
        service.execute(task["task_id"], request)
    assert not (service.root / "targets").exists()


@pytest.mark.parametrize("name", ["artifact.html", "manifest.json"])
def test_replaced_input_cannot_reuse_package_commitment(release, name):
    service, chain, task, _, request = release
    directory = service.root / "tasks" / task["task_id"] / "versions" / "1"
    (directory / name).write_bytes(b"changed")
    with pytest.raises(ReleaseError):
        service.execute(task["task_id"], request)
    assert chain.calls == 0
    assert not (service.root / "targets").exists()


def test_same_attempt_is_idempotent_and_refresh_restores_receipt(release, monkeypatch):
    service, chain, task, _, request = release
    first = service.execute(task["task_id"], request)
    def unexpected(*args):
        raise AssertionError("A confirmed attempt must not publish twice")
    monkeypatch.setattr(service, "_switch_target", unexpected)
    second = service.execute(task["task_id"], request)
    assert second["completed_at"] == first["completed_at"]
    assert second["verification"]["artifact_matches"] is True
    restarted = ReleaseService(service.root, chain=chain, signing_key=bytes.fromhex("07" * 32))
    assert restarted.execute(task["task_id"], request)["status"] == "succeeded"
    with pytest.raises(ReleaseError):
        restarted.execute(task["task_id"], {**request, "start_transaction_hash": "0x" + "8" * 64})


def test_unknown_after_switch_recovers_from_actual_target_without_republish(release, monkeypatch):
    service, _, task, _, request = release
    original = service._switch_target
    def interrupted(*args):
        original(*args)
        raise RuntimeError("Injected interruption after the atomic pointer switch")
    monkeypatch.setattr(service, "_switch_target", interrupted)
    assert service.execute(task["task_id"], request)["status"] == "unknown"
    monkeypatch.setattr(service, "_switch_target", lambda *_: pytest.fail("Recovery must read the existing target"))
    recovered = service.execute(task["task_id"], request)
    assert recovered["status"] == "succeeded"
    assert service.receipt("1", task["task_id"])["verification"]["artifact_matches"]


def test_unknown_without_target_marker_waits_for_reconciliation(release, monkeypatch):
    service, _, task, _, request = release
    monkeypatch.setattr(service, "_switch_target", lambda *_: (_ for _ in ()).throw(RuntimeError("Interrupted")))
    assert service.execute(task["task_id"], request)["status"] == "unknown"
    monkeypatch.setattr(service, "_switch_target", lambda *_: pytest.fail("An unknown result must not blindly execute again"))
    assert service.execute(task["task_id"], request)["status"] == "unknown"


def test_staging_failure_has_signed_failure_receipt(release, monkeypatch):
    service, _, task, _, request = release
    original = service._write
    def fail_artifact(path, payload, **kwargs):
        if "targets" in path.parts and path.name == "artifact.html":
            raise OSError("Injected unavailable staging storage")
        return original(path, payload, **kwargs)
    monkeypatch.setattr(service, "_write", fail_artifact)
    assert service.execute(task["task_id"], request)["status"] == "failed"
    receipt = service.receipt("1", task["task_id"])
    assert receipt["verification"]["signature_valid"]
    assert receipt["receipt"]["output_hash"] is None


def test_signature_tamper_wrong_signer_and_cross_task_replay_fail(release):
    service, _, task, _, request = release
    service.execute(task["task_id"], request)
    envelope = service.receipt("1", task["task_id"])
    for field, value in (("manifest_hash", "0x" + "9" * 64), ("task_id", "0x" + "9" * 64),
                         ("contract_address", OWNER), ("chain_id", 968), ("attempt_id", "2")):
        tampered = copy.deepcopy(envelope)
        tampered["receipt"][field] = value
        tampered["receipt_hash"] = digest(canonical_json_bytes(tampered["receipt"]))
        assert not service.verify_receipt(task["task_id"], "1", tampered)["signature_valid"]
    wrong = copy.deepcopy(envelope)
    wrong["signature"] = "0x" + "00" * 65
    assert not service.verify_receipt(task["task_id"], "1", wrong)["signature_valid"]


def test_output_tamper_is_distinct_from_authentic_executor_signature(release):
    service, _, task, _, request = release
    service.execute(task["task_id"], request)
    (service.root / "targets" / task["task_id"] / "releases" / "1" / "artifact.html").write_bytes(b"changed")
    verification = service.receipt("1", task["task_id"])["verification"]
    assert verification["signature_valid"] and not verification["artifact_matches"]
    with pytest.raises(ReleaseError):
        service.artifact(task["task_id"])


def test_executor_lock_and_path_boundary(release, tmp_path):
    service, _, task, _, request = release
    with service._locked():
        with pytest.raises(ReleaseError):
            service.execute(task["task_id"], request)
        second = ReleaseService(service.root, chain=ConfirmedChain(), signing_key=bytes.fromhex("07" * 32))
        with pytest.raises(ReleaseError):
            second.get_task(task["task_id"])
    with pytest.raises(ReleaseError):
        service._safe(service.root / ".." / "outside")


def test_unconfigured_mainnet_is_explicit_and_key_is_persisted(tmp_path, monkeypatch):
    monkeypatch.delenv("CLUETIDE_RELEASE_CONTRACT", raising=False)
    service = ReleaseService(tmp_path / "releases")
    config = service.config()
    assert config["chain"]["status"] == "unconfigured"
    assert ReleaseService(service.root).config()["executor"]["receipt_signer"] == config["executor"]["receipt_signer"]
    assert "key" not in json.dumps(config)


def test_gateway_maps_only_fixed_task_and_package_fields(release, monkeypatch):
    service, _, task, version, _ = release
    monkeypatch.setenv("CLUETIDE_RELEASE_CONTRACT", CONTRACT)
    class ChainCapture:
        def prepare(self, payload):
            return payload
        def verify(self, payload):
            return payload
    gateway = ChainGateway(service.root, service=ChainCapture())
    service.chain = gateway
    prepared = service.prepare({"action": "submit_version", "account": OWNER, "task_id": task["task_id"], "version_id": 1})
    assert prepared == {"action": "propose_version", "chain_id": 677, "contract_address": CONTRACT,
                        "account": OWNER, "task_id": task["task_id"], "parent_version_id": "0",
                        "manifest_hash": version["manifest_hash"], "artifact_hash": version["artifact_hash"], "deadline": version["expires_at"]}
    verified = service.verify({"action": "start_execution", "account": EXECUTOR, "task_id": task["task_id"],
                               "version_id": 1, "transaction_hash": TX})
    assert verified["prepared"]["action"] == "consume"
    assert verified["min_confirmations"] == 3
    with pytest.raises(ReleaseError):
        service.prepare({"action": "approve", "account": OWNER, "task_id": task["task_id"], "version_id": 1})
    with pytest.raises(ReleaseError):
        service.prepare({"action": "approve", "account": REVIEWER, "task_id": task["task_id"], "version_id": 1,
                         "expires_at": version["expires_at"] + 1})


def test_version_metadata_parent_cannot_diverge_from_frozen_manifest(release):
    service, _, task, _, request = release
    path = service.root / "tasks" / task["task_id"] / "versions" / "1" / "version.json"
    value = json.loads(path.read_text("utf-8"))
    value["parent_version_id"] = 99
    path.write_bytes(canonical_json_bytes(value))
    with pytest.raises(ReleaseError):
        service.execute(task["task_id"], request)
