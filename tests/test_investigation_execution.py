"""Focused transaction selection and atomically published execution records."""
import asyncio
import copy
import io
import zipfile

import httpx
import pytest
from pydantic_ai import models

from cluetide import app as module
from cluetide.agent import AgentOutcome, Conclusion
from cluetide.bundles import import_bundle, manifest_sha256
from cluetide.registry import LocalRegistry
from cluetide.schemas import EvidenceSet
from cluetide.store import CaseStore


@pytest.fixture
def isolated_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "LOCAL_DATA", tmp_path)
    monkeypatch.setattr(module, "store", CaseStore(tmp_path / "cases.sqlite3"))
    monkeypatch.setattr(module, "registry", LocalRegistry(tmp_path / "registry.json"))
    monkeypatch.setattr(module, "tasks", {})
    monkeypatch.setattr(module, "stops", {})
    monkeypatch.setattr(module, "mutation_lock", asyncio.Lock())
    monkeypatch.setattr(module, "paid_session_available", lambda: True)
    def forbidden_network(*args, **kwargs):
        raise AssertionError("Execution regressions use public fixtures only")
    monkeypatch.setattr(module, "ReadOnlyRpcClient", forbidden_network)
    with models.override_allow_model_requests(False):
        yield module


def fields():
    return {key: module.PRESET[key] for key in ("address", "token_address", "from_block", "to_block")}


@pytest.mark.asyncio
async def test_running_progress_is_readable_before_report_and_polling_is_read_only(isolated_execution, monkeypatch):
    observed, release = asyncio.Event(), asyncio.Event()
    async def runtime(backend, request, *, progress_callback, **kwargs):
        progress_callback({"stage":"reading", "model_requests":1, "tool_attempts":1,
                           "trace":[{"event":"tool_selected", "tool":"get_receipt"}]})
        observed.set()
        await release.wait()
        return AgentOutcome(status="partial", model_requests=1, tool_attempts=1, stop_reason="synthetic_end")
    monkeypatch.setattr(module, "run_paid", runtime)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1:5186") as client:
        started = (await client.post("/api/investigations", json={**fields(), "agent_mode":"live"})).json()
        try:
            await asyncio.wait_for(observed.wait(), 2)
            before = module.store.get(started["id"])
            doc = (await client.get(f"/api/investigations/{started['id']}")).json()
            assert doc["status"] == "running" and doc["report"] is None
            assert doc["progress"]["model_requests"] == 1 and doc["progress"]["stage"] == "reading"
            assert doc["created_at"] and module.store.get(started["id"]) == before
        finally:
            release.set()
            await finished(client, started["id"])


async def finished(client, case_id):
    for _ in range(200):
        document = (await client.get(f"/api/investigations/{case_id}")).json()
        if document["status"] != "running" and document.get("versions"):
            return document
        await asyncio.sleep(.01)
    raise AssertionError("An execution record was not published")


async def verify_publication(client, document):
    case_id = document["id"]
    bundle = await client.get(f"/api/investigations/{case_id}/bundle?revision=1")
    assert bundle.status_code == 200, bundle.text
    imported = await client.post("/api/bundles/import", files={"file": ("execution.zip", bundle.content, "application/zip")})
    assert imported.status_code == 200, imported.text
    assert imported.json()["manifest_hash"] == document["versions"][0]["content_hash"]
    assert imported.json()["report"]["execution_status"] == document["status"]
    path = document["bundle_files"][str(document["versions"][0]["version_id"])] if "bundle_files" in document else module.store.get(case_id)["bundle_files"][str(document["versions"][0]["version_id"])]
    from pathlib import Path
    verified = import_bundle(Path(path))
    assert manifest_sha256(verified.manifest) == document["manifest_hash"]
    assert verified.report == document["report"]
    assert module.registry.get_case(case_id) == document["registry"]
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        markdown = archive.read("report.md").decode("utf-8")
    return imported.json(), markdown, bundle.content


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "partial", "budget_exhausted", "stopped"])
async def test_each_runtime_terminal_status_exports_observations_and_counts(isolated_execution, monkeypatch, status):
    async def recorded_runtime(backend, request, *, outcome_callback, **kwargs):
        receipt = await backend.get_receipt(request.tx_hash)
        conclusion = Conclusion(summary="A confirmed receipt was read; its full cause remains unresolved.",
            classification="unresolved", claims=[{"text": "A receipt for the selected transaction was read.",
                "evidence_ids": [receipt["evidence_id"]], "interpretation": False}],
            assessments=[{"explanation_id": "event_explanation", "explanation": "Cause of the selected event",
                "status": "unknown", "support_evidence_ids": [], "counter_evidence_ids": [],
                "unknowns": ["Further causal evidence is required."], "checks": ["Read the receipt."]}]) if status == "completed" else None
        outcome = AgentOutcome(status=status, report=conclusion, evidence=[receipt,
            {"evidence_id": "attempt:failed", "kind": "token_state", "status": "error", "payload": {"error_code": "unavailable"}}],
            model_requests=2, tool_attempts=2, stop_reason=None if status == "completed" else "synthetic_terminal",
            trace=[{"event": "tool_failure", "number": 2}], model_names=["deepseek-v3.2"])
        outcome_callback(outcome)
        return outcome
    monkeypatch.setattr(module, "run_paid", recorded_runtime)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1:5186") as client:
        started = await client.post("/api/investigations", json={**fields(), "agent_mode": "live"})
        document = await finished(client, started.json()["id"])
        assert document["status"] == status
        assert document["agent"]["model_requests"] == document["agent"]["tool_attempts"] == 2
        assert document["agent"]["trace"][0]["event"] == "tool_failure"
        assert document["evidence"]["raw"]["agent_rpc_observations"]
        imported, markdown, original_bytes = await verify_publication(client, document)
        assert imported["report"]["agent"]["evidence"][1]["status"] == "error"
        assert "## Explanation assessments" in markdown
        assert document["investigation_scope"]["investigated_transaction_hash"] == module.TX_HASH
        before = module.store.get(document["id"])
        stopped_again = await client.post(f"/api/investigations/{document['id']}/stop")
        assert stopped_again.status_code == 200 and module.store.get(document["id"]) == before
        assert (await client.get(f"/api/investigations/{document['id']}/bundle")).content == original_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["stop", "deadline"])
async def test_collection_interruption_preserves_reads_and_exports_partial_coverage(isolated_execution, monkeypatch, termination):
    entered = asyncio.Event()
    async def interrupted_collection(request, rpc, **kwargs):
        await rpc.call("eth_chainId", [])
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(module, "collect_transfers", interrupted_collection)
    if termination == "deadline":
        timeout = asyncio.timeout
        monkeypatch.setattr(module.asyncio, "timeout", lambda seconds: timeout(.05 if seconds == 180 else seconds))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1:5186") as client:
        started = await client.post("/api/investigations", json=fields())
        case_id = started.json()["id"]
        await asyncio.wait_for(entered.wait(), 1)
        if termination == "stop":
            assert (await client.post(f"/api/investigations/{case_id}/stop")).status_code == 200
        document = await finished(client, case_id)
        assert document["status"] == ("stopped" if termination == "stop" else "partial")
        assert document["evidence"]["coverage"]["status"] == "partial"
        assert document["agent"]["model_requests"] == document["agent"]["tool_attempts"] == 0
        imported, _, _ = await verify_publication(client, document)
        assert imported["evidence"]["transfers"] == []
        assert document["evidence"]["raw"]["rpc_observations"][0]["result"] == "0x1"
        assert document["report"]["conclusion_source"] == "deterministic_execution_record"
        assert case_id not in module.tasks and case_id not in module.stops


@pytest.mark.asyncio
async def test_stop_before_executor_enters_publishes_zero_request_record(isolated_execution, monkeypatch):
    async def pending_executor(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr(module, "execute", pending_executor)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1:5186") as client:
        started = await client.post("/api/investigations", json=fields())
        case_id = started.json()["id"]
        stopped = await client.post(f"/api/investigations/{case_id}/stop")
        assert stopped.status_code == 200, stopped.text
        document = await finished(client, case_id)
        assert document["status"] == "stopped" and document["agent"]["stop_reason"] == "user_stop"
        assert document["evidence"]["coverage"]["status"] == "error"
        await verify_publication(client, document)


@pytest.mark.asyncio
async def test_alerted_large_transaction_drives_agent_focus(isolated_execution, monkeypatch):
    original = import_bundle(module.ROOT / "data/demo/bundles/real-acceptance-v1.zip")
    evidence = {**copy.deepcopy(original.evidence), "raw": copy.deepcopy(original.raw)}
    large = evidence["transfers"][0]
    small = copy.deepcopy(large)
    small.update(evidence_id="transfer:earlier-small", transaction_hash="0x" + "22" * 32,
                 value_raw="1", log_index=0, block_number=large["block_number"] - 1)
    evidence["transfers"] = [small, large]
    async def collected(*args, **kwargs):
        return EvidenceSet.model_validate(evidence)
    monkeypatch.setattr(module, "collect_transfers", collected)
    seen = []
    async def focused(backend, request, **kwargs):
        seen.append(request)
        receipt = await backend.get_receipt(request.tx_hash)
        return AgentOutcome(status="partial", evidence=[receipt], model_requests=1, tool_attempts=1, stop_reason="bounded_focus")
    monkeypatch.setattr(module, "run_paid", focused)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1:5186") as client:
        started = await client.post("/api/investigations", json={**fields(), "agent_mode": "live"})
        document = await finished(client, started.json()["id"])
        assert seen[0].tx_hash == large["transaction_hash"]
        focus = next(item["payload"]["facts"] for item in seen[0].initial_observations if item.get("evidence_id") == "derived:transfer-amounts")
        assert {fact["evidence_id"] for fact in focus} == {large["evidence_id"]}
        scope = document["investigation_scope"]
        assert scope["selection_basis"] == "largest_alerted_transfer"
        assert scope["investigated_transaction_hash"] == large["transaction_hash"]
        assert scope["uninvestigated_transaction_hashes"] == [small["transaction_hash"]]
        _, markdown, _ = await verify_publication(client, document)
        assert small["transaction_hash"] in markdown


@pytest.mark.asyncio
async def test_catalog_euler_uses_case_threshold_and_incident_context(isolated_execution):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1:5186") as client:
        catalog = (await client.get("/api/cases")).json()["cases"]
        assert {item["case_id"] for item in catalog} == {"uniswap93", "euler-20230313"}
        selected = (await client.get("/api/cases/euler-20230313")).json()
        assert selected["data_status"] == "public_cache_available"
        assert selected["source_category"] == "incident_postmortem"
        payload = {key: selected[key] for key in ("address", "token_address", "from_block", "to_block")}
        started = await client.post("/api/investigations", json=payload)
        assert started.status_code == 202, started.text
        document = await finished(client, started.json()["id"])
        assert document["status"] == "completed", document
        assert document["input"]["alert_threshold_raw"] == selected["alert_threshold_raw"]
        assert document["report"]["conclusion"]["classification"] != "governance_explained"
        assert document["report"]["conclusion"]["assessments"]
        assert any(item["kind"] == "public_context_source" for item in document["agent"]["evidence"])
        assert document["investigation_scope"]["selected_transaction_hash"] == selected["tx_hash"]
        await verify_publication(client, document)


@pytest.mark.asyncio
async def test_historical_report_without_assessments_imports_unchanged(isolated_execution):
    original_path = module.ROOT / "data/demo/bundles/real-acceptance-v1.zip"
    original_bytes = original_path.read_bytes()
    original = import_bundle(original_path)
    assert "assessments" not in original.report["conclusion"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1:5186") as client:
        imported = await client.post("/api/bundles/import", files={"file": ("historical.zip", original_bytes, "application/zip")})
        assert imported.status_code == 200, imported.text
        assert imported.json()["report"] == original.report
        assert imported.json()["manifest_hash"] == manifest_sha256(original.manifest)
    assert original_path.read_bytes() == original_bytes


@pytest.mark.asyncio
async def test_service_shutdown_publishes_task_that_has_not_started(isolated_execution, monkeypatch):
    async def pending_executor(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr(module, "execute", pending_executor)
    async with module.lifespan(module.app):
        started = await module.start_investigation(module.StartRequest(**fields()))
        case_id = started["id"]
        assert module.store.get(case_id)["status"] == "running"
    document = module.store.get(case_id)
    assert document["status"] == "stopped"
    assert document["agent"]["stop_reason"] == "service_shutdown"
    assert document["agent"]["model_requests"] == 0
    assert case_id not in module.tasks and case_id not in module.stops
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1:5186") as client:
        await verify_publication(client, document)
