"""Public request boundaries and real release routes with isolated test storage."""
import json
import time
import asyncio
import threading

import httpx
import pytest
from fastapi import FastAPI

from cluetide.releases import MAX_REQUEST_BYTES, ReleaseService
from cluetide.releases_api import create_release_router
from test_releases import ConfirmedChain, EXECUTOR, OWNER, REVIEWER, TX


@pytest.fixture
def service_app(tmp_path):
    service = ReleaseService(tmp_path / "releases", chain=ConfirmedChain(), signing_key=bytes.fromhex("07" * 32))
    app = FastAPI()
    app.include_router(create_release_router(service))
    return service, app


@pytest.mark.asyncio
async def test_api_exact_package_real_execution_and_safe_artifact_download(service_app):
    service, app = service_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/releases/tasks")).json() == {"tasks": []}
        response = await client.post("/api/releases/tasks", json={"title": "Release candidate", "owner": OWNER,
            "proposer": OWNER, "reviewer": REVIEWER, "executor": EXECUTOR})
        assert response.status_code == 201
        task_id = response.json()["task_id"]
        response = await client.post(f"/api/releases/tasks/{task_id}/versions", json={"summary": "Publish build",
            "source_reference": "git:test", "artifact_text": "<h1>Approved build</h1>", "test_evidence": ["Build passed"],
            "expires_at": int(time.time()) + 3600})
        assert response.status_code == 201
        assert (await client.get(response.json()["bundle_url"])).headers["content-type"] == "application/zip"
        response = await client.post(f"/api/releases/tasks/{task_id}/execute", json={"version_id": 1,
            "attempt_id": "1", "start_transaction_hash": TX})
        assert response.json()["status"] == "succeeded"
        receipt = await client.get(response.json()["receipt_url"])
        assert receipt.json()["verification"]["signature_valid"]
        artifact = await client.get(response.json()["artifact_url"])
        assert artifact.text == "<h1>Approved build</h1>"
        assert artifact.headers["content-type"].startswith("text/plain")
        assert artifact.headers["content-disposition"].startswith("attachment")
        assert "sandbox" in artifact.headers["content-security-policy"]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"private_key": "sensitive-input"}, {"title": "sensitive-input"}, ["sensitive-input"],
    {"title": "sensitive-input", "owner": OWNER, "proposer": OWNER, "reviewer": OWNER, "executor": EXECUTOR}])
async def test_input_errors_do_not_echo_fields(service_app, payload):
    _, app = service_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/releases/tasks", json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "The release request could not be validated."}
    assert "sensitive-input" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b'{"title":"first","title":"second"}', b"[" * 1100 + b"0" + b"]" * 1100, b'{"value":NaN}'])
async def test_duplicate_deep_and_nonfinite_json_rejected(service_app, payload):
    _, app = service_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/releases/tasks", content=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "The release request could not be validated."}


@pytest.mark.asyncio
async def test_actual_stream_byte_limit(service_app):
    _, app = service_app
    async def chunks():
        yield b" " * (MAX_REQUEST_BYTES // 2)
        yield b" " * (MAX_REQUEST_BYTES // 2 + 1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/releases/tasks", content=chunks())
    assert response.status_code == 413
    assert response.json() == {"detail": "The release request exceeds the size limit."}


@pytest.mark.asyncio
async def test_client_cannot_self_report_chain_approval(service_app):
    service, app = service_app
    task = service.create_task({"title": "Bounded task", "owner": OWNER, "proposer": OWNER, "reviewer": REVIEWER, "executor": EXECUTOR})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/releases/tasks/{task['task_id']}/execute", json={"version_id": 1,
            "attempt_id": "1", "start_transaction_hash": TX, "approved": True, "chain_id": 677})
    assert response.status_code == 422
    assert not (service.root / "targets").exists()


@pytest.mark.asyncio
async def test_concurrent_initial_config_and_task_list_both_complete(service_app):
    service, app = service_app
    entered, release = threading.Event(), threading.Event()
    original = service.chain.config
    def observed_config():
        entered.set()
        assert release.wait(timeout=2)
        return original()
    service.chain.config = observed_config
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        config_request = asyncio.create_task(client.get("/api/releases/config"))
        assert await asyncio.to_thread(entered.wait, 2)
        tasks_request = asyncio.create_task(client.get("/api/releases/tasks"))
        await asyncio.sleep(0.05)
        release.set()
        config, tasks = await asyncio.gather(config_request, tasks_request)
    assert config.status_code == tasks.status_code == 200
    assert config.json()["executor"]["receipt_signer"].startswith("0x")
    assert tasks.json() == {"tasks": []}
