import asyncio
import io
import zipfile

import httpx
import pytest

from cluetide import app as module
from cluetide.registry import LocalRegistry
from cluetide.store import CaseStore


@pytest.fixture
def local_app(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "LOCAL_DATA", tmp_path)
    monkeypatch.setattr(module, "store", CaseStore(tmp_path / "cases.sqlite3"))
    monkeypatch.setattr(module, "registry", LocalRegistry(tmp_path / "registry.json"))
    monkeypatch.setattr(module, "tasks", {})
    monkeypatch.setattr(module, "stops", {})
    monkeypatch.setattr(module, "mutation_lock", asyncio.Lock())
    monkeypatch.setattr(module, "paid_session_available", lambda: False)
    monkeypatch.setattr(module, "budget_snapshot", lambda: {"cap_rmb": "5", "reserved_rmb": "0", "remaining_rmb": "5"})
    return module.app


async def finished(client, case_id):
    for _ in range(150):
        result = await client.get(f"/api/investigations/{case_id}")
        document = result.json()
        if document["status"] != "running":
            return document
        await asyncio.sleep(0.02)
    raise AssertionError("Offline investigation did not finish")


@pytest.mark.asyncio
async def test_investigate_export_import_review_correct(local_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app), base_url="http://127.0.0.1:8765") as first:
        preset = (await first.get("/api/cases/uniswap93")).json()
        fields = {k: preset[k] for k in ("address", "token_address", "from_block", "to_block")}
        started = await first.post("/api/investigations", json=fields)
        assert started.status_code == 202
        case_id = started.json()["id"]
        doc = await finished(first, case_id)
        assert doc["status"] == "completed", doc
        assert doc["evidence"]["coverage"]["status"] == "complete"
        assert len(doc["evidence"]["transfers"]) == 1
        assert doc["evidence"]["transfers"][0]["value_raw"] == "100000000000000000000000000"
        assert len(doc["evidence"]["alerts"]) == 1
        assert doc["agent"]["tool_attempts"] >= 2
        assert doc["report"]["conclusion"]["classification"] == "governance_explained"
        parent = doc["versions"][-1]
        v1 = (await first.get(f"/api/investigations/{case_id}/bundle")).content
        # A separate client has no first-client cookies or storage.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app), base_url="http://127.0.0.1:8765") as second:
            verified = await second.post("/api/bundles/import", files={"file": ("case.zip", v1, "application/zip")})
            assert verified.status_code == 200, verified.text
            assert verified.json()["manifest_hash"] == parent["content_hash"]
            assert verified.json()["registry_verification"].startswith("unknown")
            reviewed = await second.post(f"/api/investigations/{case_id}/reviews", json={"reviewer": "local-reviewer", "comment": "Clarify that transfer to dead alone does not establish supply decrease."})
            assert reviewed.status_code == 200
        corrected = await first.post(f"/api/investigations/{case_id}/versions", json={"author": "local-author", "parent_version_id": parent["version_id"], "correction": "State the supply limitation explicitly and preserve the observed transfer.", "corrected_summary": "Evidence supports the governance explanation; the transfer alone does not prove a supply change."})
        assert corrected.status_code == 200, corrected.text
        v2doc = corrected.json()
        assert v2doc["versions"][-1]["parent_version_id"] == parent["version_id"]
        assert v2doc["manifest_hash"] != parent["content_hash"]
        assert v2doc["report"]["parent_manifest_hash"] == parent["content_hash"]
        assert v2doc["report"]["conclusion"]["summary"] == "Evidence supports the governance explanation; the transfer alone does not prove a supply change."
        assert v2doc["agent"]["report"]["summary"] == doc["agent"]["report"]["summary"]
        original_again = (await first.get(f"/api/investigations/{case_id}/bundle", params={"version": parent["version_id"]})).content
        assert original_again == v1
        stale = await first.post(f"/api/investigations/{case_id}/versions", json={"author": "local-author", "parent_version_id": parent["version_id"], "correction": "stale"})
        assert stale.status_code == 409
        bad_author = await first.post(f"/api/investigations/{case_id}/versions", json={"author": "other-author", "parent_version_id": v2doc["versions"][-1]["version_id"], "correction": "unauthorized"})
        assert bad_author.status_code == 409
        tampered = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(v1)) as original, zipfile.ZipFile(tampered, "w") as altered:
            for name in original.namelist():
                payload = original.read(name)
                if name == "report.md":
                    payload += b"x"
                altered.writestr(name, payload)
        invalid = await first.post("/api/bundles/import", files={"file": ("tampered.zip", tampered.getvalue(), "application/zip")})
        assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_api_admission_and_same_origin(local_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app), base_url="http://127.0.0.1:8765") as client:
        payload = {k: module.PRESET[k] for k in ("address", "token_address", "from_block", "to_block")}
        paid = await client.post("/api/investigations", json={**payload, "agent_mode": "live"})
        assert paid.status_code == 409
        cross_site = await client.post("/api/investigations", json=payload, headers={"Origin": "https://evil.example"})
        assert cross_site.status_code == 403
        dns_rebind = await client.get("/api/health", headers={"Host": "evil.example"})
        assert dns_rebind.status_code == 403
        mismatch_cache = await client.post("/api/investigations", json={**payload, "from_block": 24106369})
        assert mismatch_cache.status_code == 422
        huge_window = await client.post("/api/investigations", json={**payload, "to_block": 99999999})
        assert huge_window.status_code == 422


@pytest.mark.asyncio
async def test_stop_immediately_and_refresh(local_app, monkeypatch):
    async def blocked_collection(*args, **kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(module, "collect_transfers", blocked_collection)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app), base_url="http://127.0.0.1:8765") as client:
        payload = {k: module.PRESET[k] for k in ("address", "token_address", "from_block", "to_block")}
        started = await client.post("/api/investigations", json=payload)
        case_id = started.json()["id"]
        stopped = await client.post(f"/api/investigations/{case_id}/stop")
        assert stopped.json()["status"] == "stopped"
        assert case_id not in module.tasks
        assert case_id not in module.stops
        assert (await client.get(f"/api/investigations/{case_id}")).json()["status"] == "stopped"
        assert (await client.post(f"/api/investigations/{case_id}/stop")).json()["status"] == "stopped"


@pytest.mark.asyncio
async def test_paid_api_wiring_without_external_requests(local_app, monkeypatch):
    from cluetide.agent import run_investigation, build_offline_model
    calls = []
    async def fake_paid(backend, request, *, stop_event, deadline_seconds, outcome_callback=None):
        calls.append(request)
        return await run_investigation(build_offline_model(), backend, request, stop_event=stop_event, outcome_callback=outcome_callback)
    monkeypatch.setattr(module, "paid_session_available", lambda: True)
    monkeypatch.setattr(module, "run_paid", fake_paid)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app), base_url="http://127.0.0.1:8765") as client:
        payload = {k: module.PRESET[k] for k in ("address", "token_address", "from_block", "to_block")}
        started = await client.post("/api/investigations", json={**payload, "agent_mode": "live"})
        document = await finished(client, started.json()["id"])
        assert document["status"] == "completed"
        assert document["report"]["execution_mode"] == "Real model, public cache tools"
        assert len(calls) == 1
        assert all(item.get("kind") != "token_state" for item in calls[0].initial_observations)
        supply = next(item for item in document["report"]["conclusion"]["assessments"]
                      if item["explanation_id"] == "supply_decrease")
        assert supply["status"] == "unknown" and supply["unknowns"]
        derived = next(item for item in calls[0].initial_observations if item.get("evidence_id") == "derived:transfer-amounts")
        assert derived["payload"]["facts"][0]["raw_amount"] == "100000000000000000000000000"
        assert derived["payload"]["facts"][0]["formatted_amount"] == "100000000"
        assert derived["payload"]["source_evidence_ids"] == [derived["payload"]["facts"][0]["evidence_id"]]
        assert document["report"]["review_status"] == "awaiting_human_review"
