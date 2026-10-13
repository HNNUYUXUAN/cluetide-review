"""Real-EVM correction decisions and independent reviewer feedback signatures."""
import copy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import httpx

from eth_account import Account
from eth_account.messages import encode_typed_data
from fastapi.testclient import TestClient
import pytest

from cluetide.bundles import canonical_json_bytes
from cluetide.release_feedback import MAX_FEEDBACK_REQUEST_BYTES, feedback_typed_data
from cluetide.release_server import create_release_app
from cluetide.releases import ReleaseError, digest
from test_release_integration_evm import release, create, version, chain_action


def signed_feedback(release, task, package, transaction_hash, *, reason="Correct the project name", signer=2, contract=None, conditions=None):
    service, bridge, _ = release
    data = {"version_id": package["version_id"], "manifest_hash": package["manifest_hash"],
        "correction_transaction_hash": transaction_hash, "reason": reason,
        "acceptance_conditions": conditions if conditions is not None else ["The heading matches the registered project name"]}
    data["content_hash"] = digest(canonical_json_bytes({key: data[key] for key in ("reason", "acceptance_conditions")}))
    typed = feedback_typed_data(task["task_id"], data, contract or service._adapter().config()["contract_address"])
    data["signature"] = Account.sign_message(encode_typed_data(full_message=typed),
        bridge.tester.backend.account_keys[signer]).signature.to_0x_hex()
    return data


def correction(release):
    task = create(release)
    package = version(release, task)
    tx = chain_action(release, "request_correction", task, package)["transaction_hash"]
    return task, package, tx


def test_signed_feedback_after_real_correction_is_public_immutable_and_idempotent(release, tmp_path):
    service, _, _ = release
    task, package, tx = correction(release)
    data = signed_feedback(release, task, package, tx)
    app = create_release_app(service=service, dist=tmp_path / "none")
    with TestClient(app) as client:
        path = "/api/releases/tasks/" + task["task_id"] + "/feedback"
        first = client.post(path, json=data)
        assert first.status_code == 200
        assert first.json()["verification"]["confirmations"] == "3"
        assert client.post(path, json=data).json() == first.json()
        assert client.get(path, params={"version": 1}).json()["feedback"] == [first.json()]
        conflict = signed_feedback(release, task, package, tx, reason="Different request")
        assert client.post(path, json=conflict).status_code == 409
        assert client.get(path).json()["feedback"] == [first.json()]
        assert client.get(path, params={"version": "bad"}).status_code == 422


def test_feedback_http_limit_accepts_full_escaped_unicode_budget(release, tmp_path):
    service, _, _ = release
    task, package, tx = correction(release)
    data = signed_feedback(release, task, package, tx, reason="😀" * 4000, conditions=["😀" * 1000] * 12)
    raw = json.dumps(data, ensure_ascii=True).encode("ascii")
    assert 128 * 1024 < len(raw) < MAX_FEEDBACK_REQUEST_BYTES
    app = create_release_app(service=service, dist=tmp_path / "none")
    with TestClient(app) as client:
        path = "/api/releases/tasks/" + task["task_id"] + "/feedback"
        accepted = client.post(path, content=raw, headers={"Content-Type": "application/json"})
        assert accepted.status_code == 200
        assert accepted.json()["reason"] == data["reason"]
        assert accepted.json()["acceptance_conditions"] == data["acceptance_conditions"]
        assert client.get(path).json()["feedback"] == [accepted.json()]
        assert client.post(path, content=b" " * (MAX_FEEDBACK_REQUEST_BYTES + 1)).status_code == 413
        assert client.post("/api/releases/prepare", content=b" " * (64 * 1024 + 1)).status_code == 413


@pytest.mark.parametrize("change", ["signer", "body", "version", "domain", "manifest"])
def test_feedback_rejects_wrong_signatures_content_and_replay(release, change):
    service, _, _ = release
    task, package, tx = correction(release)
    data = signed_feedback(release, task, package, tx,
        signer=1 if change == "signer" else 2, contract="0x" + "33" * 20 if change == "domain" else None)
    if change == "body":
        data["reason"] = "Tampered request"
    elif change == "version":
        newer = version(release, task, "<h1>New version</h1>")
        data["version_id"], data["manifest_hash"] = newer["version_id"], newer["manifest_hash"]
    elif change == "manifest":
        data["manifest_hash"] = "0x" + "44" * 32
    with pytest.raises(ReleaseError):
        service.submit_feedback(task["task_id"], data)
    assert service.feedback(task["task_id"])["feedback"] == []


def test_signed_feedback_requires_exact_confirmed_correction_transaction(release, monkeypatch):
    service, bridge, _ = release
    task = create(release)
    package = version(release, task)
    approve = chain_action(release, "approve", task, package)
    with pytest.raises(ReleaseError):
        service.submit_feedback(task["task_id"], signed_feedback(release, task, package, approve["transaction_hash"]))
    tx = chain_action(release, "request_correction", task, package)["transaction_hash"]
    data = signed_feedback(release, task, package, tx)
    original = bridge.result
    def shallow(method, params):
        return hex(bridge.web3.eth.block_number - 1) if method == "eth_blockNumber" else original(method, params)
    monkeypatch.setattr(bridge, "result", shallow)
    with pytest.raises(ReleaseError, match="three canonical"):
        service.submit_feedback(task["task_id"], data)
    assert service.feedback(task["task_id"])["feedback"] == []
    monkeypatch.setattr(bridge, "result", original)
    assert service.submit_feedback(task["task_id"], data)["reason"] == data["reason"]


def test_later_revision_keeps_prior_correction_feedback_valid(release):
    service, _, _ = release
    task, package, tx = correction(release)
    data = signed_feedback(release, task, package, tx)
    version(release, task, "<h1>Revised</h1>")
    saved = service.submit_feedback(task["task_id"], data)
    assert saved["version_id"] == 1
    assert service.feedback(task["task_id"], 2)["feedback"] == []


def test_owner_can_revoke_through_service_matching_contract_role(release):
    service, bridge, _ = release
    task = create(release)
    package = version(release, task)
    payload = {"action": "revoke", "account": task["owner"], "task_id": task["task_id"], "version_id": package["version_id"]}
    prepared = service.prepare(payload)
    tx = bridge.send(prepared["transaction"])
    assert service.verify({**payload, "transaction_hash": tx})["status"] == "verified"


def test_nine_transaction_collaboration_loop_with_agent_feedback_and_fresh_execution(release, tmp_path):
    from cluetide.release_exchange import verify_package
    from cluetide.releases import ChainGateway, ReleaseService
    from test_release_agent import agent
    service, bridge, contract = release
    task = create(release)
    v1 = version(release, task, "<h1>Project v1</h1>")
    chain_action(release, "approve", task, v1)
    v2 = version(release, task, "<h1>Incorrect project name</h1>")
    with pytest.raises(ReleaseError):
        service.prepare({"action": "start_execution", "task_id": task["task_id"], "version_id": 1, "account": task["executor"]})
    correction_tx = chain_action(release, "request_correction", task, v2)["transaction_hash"]
    wrong_signature = signed_feedback(release, task, v2, correction_tx, signer=1)
    with pytest.raises(ReleaseError):
        service.submit_feedback(task["task_id"], wrong_signature)
    signed = signed_feedback(release, task, v2, correction_tx, reason="Use ClueTide as the exact project name")
    saved = service.submit_feedback(task["task_id"], signed)
    assert service.submit_feedback(task["task_id"], signed) == saved

    class AgentService:
        def post(self, url, json):
            return httpx.Response(200, json=service.read(json), request=httpx.Request("POST", url))
        def get(self, url, params):
            return httpx.Response(200, json=service.feedback(task["task_id"], params["version"]), request=httpx.Request("GET", url))
    feedback = agent.wait_for_review(AgentService(), "http://local/api/releases",
        SimpleNamespace(task_id=task["task_id"], version_id=2, interval=5, timeout=0, until="decision"))
    assert feedback["status"] == "correction_required"
    assert feedback["feedback"][0]["reason"] == "Use ClueTide as the exact project name"
    v3 = version(release, task, "\ufeff<h1>ClueTide</h1>\r\n")
    chain_action(release, "approve", task, v3)
    started = chain_action(release, "start_execution", task, v3)
    original = verify_package(service.bundle(task["task_id"], 3))["task"]
    runtime = tmp_path / "fresh-publisher"
    runtime.mkdir()
    shutil.copyfile(service.root / "chain.json", runtime / "chain.json")
    publisher = ReleaseService(runtime, chain=ChainGateway(runtime, service=service._adapter().service),
        signing_key=bridge.tester.backend.account_keys[4].to_bytes())
    public_task = {key: original[key] for key in ("task_id", "title", "created_at", "owner", "proposer", "reviewer", "executor", "receipt_signer", "target_id")}
    for item in original["versions"]:
        publisher.import_version({"task": public_task, "manifest": item["manifest"],
            "manifest_text": item["manifest_text"], "artifact_text": item["artifact_text"]})
    assert publisher.get_task(task["task_id"])["chain_records"] == []
    execution = {"version_id": 3, "attempt_id": started["onchain_ids"]["attempt_id"],
                 "start_transaction_hash": started["transaction_hash"]}
    assert publisher.execute(task["task_id"], execution)["status"] == "succeeded"
    assert publisher.artifact(task["task_id"]) == "\ufeff<h1>ClueTide</h1>\r\n".encode("utf-8")
    chain_action((publisher, bridge, contract), "record_receipt", task, v3, execution["attempt_id"])
    assert publisher.execute(task["task_id"], execution)["status"] == "succeeded"
    state = publisher.read({"task_id": task["task_id"], "version_id": 3, "attempt_id": "1"})
    assert agent.workflow_status(state, 3, int(bridge.web3.eth.get_block("latest").timestamp)) == "receipt_recorded"
