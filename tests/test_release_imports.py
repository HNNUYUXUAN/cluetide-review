"""Confirmed browser packages retain immutable evidence and publisher boundaries."""
import copy
import time

from fastapi.testclient import TestClient
import pytest

from cluetide.release_server import create_release_app
from cluetide.releases import ReleaseError, ReleaseService


class ImportChain:
    def __init__(self, task, version):
        self.task, self.version = task, version
        self.change = {}

    def read_import(self, *, task, version_id):
        record = {key: self.task[key] for key in ("owner", "proposer", "reviewer", "executor", "receipt_signer")}
        record.update(target_hash=self.task["target_id"], version_count="1")
        version = {"version_id": str(version_id), "parent_version_id": "0",
                   "manifest_hash": self.version["manifest_hash"], "artifact_hash": self.version["artifact_hash"],
                   "deadline": str(self.version["expires_at"]), **self.change}
        return {"getter_state": {"task": record, "version": version}, "read_block_number": "123"}


@pytest.fixture()
def browser_package(tmp_path):
    key = bytes.fromhex("07" * 32)
    source = ReleaseService(tmp_path / "browser-source", signing_key=key)
    task = source.create_task({"title": "Browser package", "owner": "0x" + "11" * 20,
                              "proposer": "0x" + "11" * 20, "reviewer": "0x" + "22" * 20,
                              "executor": "0x" + "11" * 20})
    text = '<!doctype html><h1>Reviewed release</h1>\n'
    version = source.create_version(task["task_id"], {"summary": "Publish reviewed HTML", "source_reference": "git:test",
        "artifact_text": text, "test_evidence": ["Test fixture"], "expires_at": int(time.time()) + 3600})
    fields = ("title", "owner", "proposer", "reviewer", "executor", "task_id", "receipt_signer", "target_id", "created_at")
    payload = {"task": {k: task[k] for k in fields}, "manifest": version["manifest"], "artifact_text": text}
    chain = ImportChain(task, version)
    target = ReleaseService(tmp_path / "publisher", chain=chain, signing_key=key)
    return target, chain, task, version, payload


def test_browser_import_is_exact_idempotent_and_issues_no_write_capability(browser_package):
    service, _, task, version, payload = browser_package
    first = service.import_version(payload)
    assert first == service.import_version(payload)
    assert first["manifest_hash"] == version["manifest_hash"]
    assert service.get_task(task["task_id"])["version_count"] == 1
    assert not list((service.root / "task-capabilities").glob("*.json"))
    assert "write_key" not in first
    assert service._version(service._task(task["task_id"]), 1)["manifest_hash"] == version["manifest_hash"]


@pytest.mark.parametrize("field,value", [("manifest_hash", "0x" + "ff" * 32), ("artifact_hash", "0x" + "ee" * 32),
                                         ("deadline", "1"), ("parent_version_id", "4")])
def test_chain_commitment_mismatch_does_not_store_task(browser_package, field, value):
    service, chain, task, _, payload = browser_package
    chain.change[field] = value
    with pytest.raises(ReleaseError, match="chain commitments"):
        service.import_version(payload)
    assert not service._task_path(task["task_id"]).exists()


def test_tampered_bytes_roles_and_signer_are_rejected(browser_package):
    service, _, task, _, payload = browser_package
    changed = copy.deepcopy(payload)
    changed["artifact_text"] += "altered"
    with pytest.raises(ReleaseError, match="manifest"):
        service.import_version(changed)
    changed = copy.deepcopy(payload)
    changed["task"]["reviewer"] = "0x" + "33" * 20
    with pytest.raises(ReleaseError, match="roles or target"):
        service.import_version(changed)
    changed = copy.deepcopy(payload)
    changed["task"]["receipt_signer"] = "0x" + "33" * 20
    with pytest.raises(ReleaseError, match="different publication signer"):
        service.import_version(changed)
    assert not service._task_path(task["task_id"]).exists()


def test_pages_origin_preflight_and_import_have_an_explicit_boundary(browser_package, tmp_path):
    service, _, task, _, payload = browser_package
    app = create_release_app(service=service, frontend_origins=("https://hnnuyuxuan.github.io",), dist=tmp_path / "none")
    with TestClient(app) as client:
        headers = {"Origin": "https://hnnuyuxuan.github.io", "Sec-Fetch-Site": "cross-site"}
        preflight = client.options("/api/releases/imports", headers={**headers, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == headers["Origin"]
        assert "access-control-allow-credentials" not in preflight.headers
        response = client.post("/api/releases/imports", json=payload, headers=headers)
        assert response.status_code == 200 and response.json()["task_id"] == task["task_id"]
        assert "write_key" not in response.json()
        assert client.post("/api/releases/imports", json=payload, headers={"Origin": "https://other.example", "Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert client.post(f'/api/releases/tasks/{task["task_id"]}/versions', json={}, headers=headers).status_code == 403
