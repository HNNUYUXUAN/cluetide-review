"""Directed assessment revisions preserve evidence bindings and prior bundles."""
import asyncio
import copy

import httpx
import pytest
from pydantic_ai import models

from cluetide import app as module
from cluetide.agent import AgentOutcome, Conclusion
from cluetide.bundles import import_bundle
from cluetide.investigation_scope import select_investigation_transaction
from cluetide.registry import LocalRegistry
from cluetide.store import CaseStore


@pytest.fixture
def revision_store(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "LOCAL_DATA", tmp_path)
    monkeypatch.setattr(module, "store", CaseStore(tmp_path / "cases.sqlite3"))
    monkeypatch.setattr(module, "registry", LocalRegistry(tmp_path / "registry.json"))
    monkeypatch.setattr(module, "tasks", {})
    monkeypatch.setattr(module, "stops", {})
    monkeypatch.setattr(module, "mutation_lock", asyncio.Lock())
    def forbidden_network(*args, **kwargs):
        raise AssertionError("Assessment corrections use recorded public evidence")
    monkeypatch.setattr(module, "ReadOnlyRpcClient", forbidden_network)
    monkeypatch.setattr(module, "run_paid", forbidden_network)
    with models.override_allow_model_requests(False):
        yield tmp_path


async def published_case():
    original = import_bundle(module.ROOT / "data/demo/bundles/real-acceptance-v1.zip")
    evidence = {**copy.deepcopy(original.evidence), "raw": copy.deepcopy(original.raw)}
    start = module.StartRequest(**{key: module.PRESET[key] for key in
                                  ("address", "token_address", "from_block", "to_block")})
    observations = [
        {"evidence_id": "receipt:selected", "kind": "receipt", "status": "ok",
         "payload": {"transactionHash": module.TX_HASH}},
        {"evidence_id": "source:governance", "kind": "governance_source", "status": "ok",
         "payload": {"category": "governance"}},
        {"evidence_id": "state:before", "kind": "token_state", "status": "ok",
         "payload": {"token_address": start.token_address, "block_number": 24106377,
                     "total_supply_raw": "1000000000000000000000000000"}},
        {"evidence_id": "state:after", "kind": "token_state", "status": "ok",
         "payload": {"token_address": start.token_address, "block_number": 24106378,
                     "total_supply_raw": "1000000000000000000000000000"}},
        {"evidence_id": "attempt:failed", "kind": "token_state", "status": "error",
         "payload": {"error_code": "unavailable"}},
    ]
    report = Conclusion(summary="Recorded sources support the governance execution explanation.",
        classification="governance_explained",
        claims=[{"text": "The selected transaction receipt was read.",
                 "evidence_ids": ["receipt:selected"], "interpretation": False}],
        assessments=[
            {"explanation_id": "governance_execution", "explanation": "Governance execution",
             "status": "supported", "support_evidence_ids": ["source:governance"],
             "counter_evidence_ids": [], "unknowns": [], "checks": ["Read the public governance source."]},
            {"explanation_id": "supply_decrease", "explanation": "A net decrease in total supply",
             "status": "unknown", "support_evidence_ids": [], "counter_evidence_ids": [],
             "unknowns": ["The supply observations require directed comparison."],
             "checks": ["Read the historical getter observations."]},
        ])
    document = {"id": "assessment-revision", "title": "Assessment revision", "status": "running",
                "input": start.model_dump(), "evidence": evidence, "agent": None, "report": None,
                "registry": None, "versions": [], "reviews": []}
    module.store.put(document["id"], document)
    _, scope = select_investigation_transaction(evidence)
    outcome = AgentOutcome(status="completed", report=report, evidence=observations,
                           model_requests=2, tool_attempts=5).model_dump(mode="json")
    return await module.publish_execution(document, start, outcome, scope)


def correction_body(document, **changes):
    return {"parent_version_id": document["versions"][-1]["version_id"],
            "correction": "The local reviewer revised the directed explanation assessment.", **changes}


def unknown_governance():
    return {"explanation_id": "governance_execution", "explanation": "Governance execution",
            "status": "unknown", "support_evidence_ids": [], "counter_evidence_ids": [],
            "unknowns": ["The source-to-transaction causal link requires further review."],
            "checks": ["Reassess the captured governance source and receipt."]}


@pytest.mark.asyncio
async def test_governance_and_classification_revision_publish_together(revision_store):
    document = await published_case()
    original_agent = copy.deepcopy(document["report"]["agent"])
    original_hash = document["versions"][0]["content_hash"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                               base_url="http://127.0.0.1:8765") as client:
        original_zip = (await client.get(f"/api/investigations/{document['id']}/bundle?revision=1")).content
        response = await client.post(f"/api/investigations/{document['id']}/versions", json=correction_body(document,
            corrected_classification="unresolved", corrected_summary="The causal explanation requires further review.",
            assessment_replacements={"0": unknown_governance()}))
        assert response.status_code == 200, response.text
        revised = response.json()
        assert revised["report"]["conclusion"]["classification"] == "unresolved"
        assert revised["report"]["conclusion"]["assessments"][0] == unknown_governance()
        assert revised["report"]["agent"] == original_agent
        assert revised["versions"][0]["content_hash"] == original_hash
        assert (await client.get(f"/api/investigations/{document['id']}/bundle?revision=1")).content == original_zip
        v2 = await client.get(f"/api/investigations/{document['id']}/bundle?revision=2")
        imported = await client.post("/api/bundles/import", files={"file": ("v2.zip", v2.content, "application/zip")})
        assert imported.status_code == 200, imported.text
        assert imported.json()["manifest_hash"] == revised["versions"][1]["content_hash"]
        assert imported.json()["report"]["agent"] == original_agent
        assert "governance_execution: Governance execution (unknown)" in imported.json()["report_markdown"]


@pytest.mark.asyncio
async def test_supply_comparison_can_be_corrected_with_two_successful_observations(revision_store):
    document = await published_case()
    replacement = {"explanation_id": "supply_decrease", "explanation": "A net decrease in total supply",
                   "status": "refuted", "support_evidence_ids": [],
                   "counter_evidence_ids": ["state:before", "state:after"], "unknowns": [],
                   "checks": ["Compare the two captured historical raw supply values."]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                               base_url="http://127.0.0.1:8765") as client:
        response = await client.post(f"/api/investigations/{document['id']}/versions",
                                     json=correction_body(document, assessment_replacements={"1": replacement}))
    assert response.status_code == 200, response.text
    assert response.json()["report"]["conclusion"]["assessments"][1] == replacement
    assert response.json()["report"]["agent"]["report"]["assessments"][1]["status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["unknown_id", "failed_id", "index", "identifier", "classification", "supply_direction"])
async def test_invalid_assessment_revision_preserves_committed_state(revision_store, invalid):
    document = await published_case()
    before = module.store.get(document["id"])
    files_before = sorted(path.name for path in (revision_store / "bundles").glob("*.zip"))
    replacement = copy.deepcopy(document["report"]["conclusion"]["assessments"][0])
    index = "0"
    if invalid in {"unknown_id", "failed_id"}:
        replacement["support_evidence_ids"] = ["source:missing" if invalid == "unknown_id" else "attempt:failed"]
    elif invalid == "index":
        index = "99"
    elif invalid == "identifier":
        replacement["explanation_id"] = "other_candidate"
    elif invalid == "classification":
        replacement = unknown_governance()
    else:
        index = "1"
        replacement = {"explanation_id": "supply_decrease", "explanation": "A net decrease in total supply",
                       "status": "supported", "support_evidence_ids": ["state:before", "state:after"],
                       "counter_evidence_ids": [], "unknowns": [], "checks": ["Compare captured getter values."]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                               base_url="http://127.0.0.1:8765") as client:
        response = await client.post(f"/api/investigations/{document['id']}/versions",
                                     json=correction_body(document, assessment_replacements={index: replacement}))
    assert response.status_code == 422, response.text
    assert module.store.get(document["id"]) == before
    assert sorted(path.name for path in (revision_store / "bundles").glob("*.zip")) == files_before
    assert module.registry.get_case(document["id"]) == before["registry"]


@pytest.mark.asyncio
async def test_assessment_replacement_requires_typed_unknowns(revision_store):
    document = await published_case()
    replacement = unknown_governance()
    replacement["unknowns"] = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                               base_url="http://127.0.0.1:8765") as client:
        response = await client.post(f"/api/investigations/{document['id']}/versions",
                                     json=correction_body(document, corrected_classification="unresolved",
                                                          assessment_replacements={"0": replacement}))
    assert response.status_code == 422
    assert len(module.store.get(document["id"])["versions"]) == 1
