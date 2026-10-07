"""Immutable model v1 and corrected public v2, with no external model calls."""

import asyncio
import copy
import io
import json
import zipfile

import httpx
import pytest
from pydantic_ai import models

from cluetide import app as module
from cluetide.agent import AgentOutcome, Conclusion
from cluetide.bundles import export_bundle, manifest_sha256
from cluetide.registry import LocalRegistry
from cluetide.schemas import MAX_SAFE_INTEGER
from cluetide.store import CaseStore


@pytest.fixture
def quality_app(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "LOCAL_DATA", tmp_path)
    monkeypatch.setattr(module, "store", CaseStore(tmp_path / "cases.sqlite3"))
    monkeypatch.setattr(module, "registry", LocalRegistry(tmp_path / "registry.json"))
    monkeypatch.setattr(module, "tasks", {})
    monkeypatch.setattr(module, "stops", {})
    monkeypatch.setattr(module, "mutation_lock", asyncio.Lock())
    monkeypatch.setattr(module, "paid_session_available", lambda: True)
    monkeypatch.setattr(module, "budget_snapshot", lambda: {})
    faulty_report = Conclusion(
        summary="The Transfer value is 100000000000000000000000000 wei. This transaction is not an attack.",
        classification="governance_explained",
        claims=[{"text": "The ERC-20 Transfer amount is 100000000000000000000000000 wei.",
                 "evidence_ids": ["receipt:synthetic"], "interpretation": False},
                {"text": "The transaction is not an attack.",
                 "evidence_ids": ["source:synthetic"], "interpretation": True}],
        limitations=["Synthetic report text for offline quality validation."])
    async def synthetic_paid(*args, **kwargs):
        return AgentOutcome(status="completed", report=faulty_report.model_copy(deep=True),
                            model_requests=3, tool_attempts=2, model_names=["deepseek-v3.2"])
    monkeypatch.setattr(module, "run_paid", synthetic_paid)
    with models.override_allow_model_requests(False):
        yield module.app


async def completed_document(client, case_id):
    for _ in range(100):
        document = (await client.get(f"/api/investigations/{case_id}")).json()
        if document["status"] != "running":
            return document
        await asyncio.sleep(0.01)
    raise AssertionError("Public cache execution did not complete")


def json_member(bundle, name):
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        return json.loads(archive.read(name))


@pytest.mark.asyncio
async def test_flag_v1_recheck_corrections_and_preserve_original(quality_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=quality_app),
                                 base_url="http://127.0.0.1:5186") as client:
        payload = {key: module.PRESET[key] for key in ("address", "token_address", "from_block", "to_block")}
        started = await client.post("/api/investigations", json={**payload, "agent_mode": "live"})
        assert started.status_code == 202
        case_id = started.json()["id"]
        v1 = await completed_document(client, case_id)
        assert v1["status"] == "completed", v1
        report = v1["report"]
        assert report["quality_validation"]["status"] == "needs_review"
        assert {flag["code"] for flag in report["quality_validation"]["flags"]} == {
            "erc20_wei_unit", "categorical_attack_exclusion"}
        assert report["quality_validation"]["report_modified"] is False
        facts = report["transfer_facts"]
        assert len(facts) == 1
        assert facts[0]["raw_amount"] == "100000000000000000000000000"
        assert facts[0]["decimals"] == 18 and facts[0]["formatted_amount"] == "100000000"
        assert facts[0]["symbol"] == "UNI"
        assert report["conclusion"] == v1["agent"]["report"]
        original_bundle = (await client.get(f"/api/investigations/{case_id}/bundle")).content
        original_raw = json_member(original_bundle, "raw.json")
        original_version = v1["versions"][0]["version_id"]
        original_report = json_member(original_bundle, "report.json")
        assert original_report["quality_validation"] == report["quality_validation"]

        reviewed = await client.post(f"/api/investigations/{case_id}/reviews", json={
            "reviewer": "local-reviewer", "version_id": original_version,
            "comment": "Correct ERC-20 units and retain uncertainty about attack classification."})
        assert reviewed.status_code == 200
        note_only = await client.post(f"/api/investigations/{case_id}/versions", json={
            "author": "local-author", "parent_version_id": original_version,
            "correction": "Review was recorded; current conclusion still requires a semantic correction."})
        assert note_only.status_code == 200, note_only.text
        assert note_only.json()["report"]["quality_validation"]["status"] == "needs_review"

        corrected = await client.post(f"/api/investigations/{case_id}/versions", json={
            "author": "local-author", "parent_version_id": note_only.json()["versions"][-1]["version_id"],
            "correction": "Use exact token decimals and qualify the governance interpretation.",
            "corrected_summary": "The observed ERC-20 Transfer is 100000000 UNI using 18 decimals. Governance sources support an explanation; attack classification remains unresolved.",
            "claim_replacements": {
                "0": "ERC-20 Transfer amount: 100000000 UNI; raw token base units: 100000000000000000000000000; decimals: 18.",
                "1": "The available governance source supports an explanation. This evidence does not independently establish the transaction's full cause."}})
        assert corrected.status_code == 200, corrected.text
        revised = corrected.json()
        assert revised["report"]["quality_validation"]["status"] == "no_flags_detected"
        assert revised["report"]["quality_validation"]["flags"] == []
        assert revised["agent"] == v1["agent"]
        assert revised["report"]["agent"] == report["agent"]
        assert revised["manifest_hash"] != v1["manifest_hash"]
        corrected_bundle = (await client.get(f"/api/investigations/{case_id}/bundle")).content
        assert json_member(corrected_bundle, "raw.json") == original_raw
        assert json_member(corrected_bundle, "report.json")["agent"]["report"] == report["conclusion"]
        retained_original = (await client.get(f"/api/investigations/{case_id}/bundle",
                                             params={"version": original_version})).content
        assert retained_original == original_bundle
        assert original_report["conclusion"] == report["conclusion"]
        verified = await client.post("/api/bundles/import", files={
            "file": ("revised.zip", corrected_bundle, "application/zip")})
        assert verified.status_code == 200, verified.text
        assert verified.json()["report"]["quality_validation"]["status"] == "no_flags_detected"
        assert verified.json()["report_quality"]["status"] == "no_flags_detected"
        assert verified.json()["report"]["agent"]["report"] == report["conclusion"]


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_quality", [None, "no_flags_detected"])
async def test_import_recomputes_legacy_or_self_declared_quality(quality_app, declared_quality):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=quality_app),
                                 base_url="http://127.0.0.1:5186") as client:
        payload = {key: module.PRESET[key] for key in ("address", "token_address", "from_block", "to_block")}
        started = await client.post("/api/investigations", json={**payload, "agent_mode": "live"})
        document = await completed_document(client, started.json()["id"])
        legacy_report = copy.deepcopy(document["report"])
        legacy_report.pop("quality_validation")
        legacy_report.pop("transfer_facts")
        if declared_quality is not None:
            legacy_report["quality_validation"] = {"status": declared_quality, "flags": []}
        path = module.LOCAL_DATA / "legacy.zip"
        manifest = export_bundle(document["evidence"], legacy_report, "# Original model report\n", path)
        payload = path.read_bytes()
        imported = await client.post("/api/bundles/import", files={
            "file": ("legacy.zip", payload, "application/zip")})
        assert imported.status_code == 200, imported.text
        verified = imported.json()
        assert verified["manifest_hash"] == manifest_sha256(manifest)
        assert verified["report"] == legacy_report
        assert path.read_bytes() == payload
        assert verified["report_quality"]["status"] == "needs_review"
        assert {flag["code"] for flag in verified["report_quality"]["flags"]} == {
            "erc20_wei_unit", "categorical_attack_exclusion"}
        assert verified["report_quality"]["report_modified"] is False
        assert verified["transfer_facts"][0]["raw_amount"] == "100000000000000000000000000"
        assert verified["transfer_facts"][0]["formatted_amount"] == "100000000"
        assert verified["transfer_facts"][0]["decimals_observation_ids"]


@pytest.mark.asyncio
@pytest.mark.parametrize("malformation", ["amount", "decimals", "claim"])
async def test_import_bad_evidence_or_claim_schema_returns_safe_error(quality_app, malformation):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=quality_app),
                                 base_url="http://127.0.0.1:5186") as client:
        payload = {key: module.PRESET[key] for key in ("address", "token_address", "from_block", "to_block")}
        started = await client.post("/api/investigations", json={**payload, "agent_mode": "live"})
        document = await completed_document(client, started.json()["id"])
        malformed = copy.deepcopy(document["evidence"])
        malformed_report = copy.deepcopy(document["report"])
        if malformation == "amount":
            malformed["transfers"][0]["value_raw"] = "untrusted malformed amount"
        elif malformation == "decimals":
            malformed["metadata"]["decimals"] = "x"
        else:
            malformed_report["conclusion"]["claims"] = [7]
        path = module.LOCAL_DATA / "malformed.zip"
        export_bundle(malformed, malformed_report, "# Report\n", path)
        imported = await client.post("/api/bundles/import", files={
            "file": ("malformed.zip", path.read_bytes(), "application/zip")})
        assert imported.status_code == 422
        assert imported.json()["detail"] == "Bundle content has an unsupported schema for deterministic report checks"
        assert "untrusted malformed amount" not in imported.text


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ["standard", "generic"])
@pytest.mark.parametrize("citation_shape", ["missing", "null", "string"])
async def test_import_rejects_malformed_citation_lists(quality_app, layout, citation_shape):
    report = {"schema_version": "cluetide-report/v1" if layout == "standard" else "generic-evidence-report/v0",
              "conclusion": {"summary": "Synthetic cited report.", "classification": "unresolved",
                             "claims": [{"text": "An observation remains incomplete.",
                                         "evidence_ids": ["synthetic-observation"], "interpretation": False}],
                             "limitations": []}}
    claim = report["conclusion"]["claims"][0]
    if citation_shape == "missing":
        claim.pop("evidence_ids")
    elif citation_shape == "null":
        claim["evidence_ids"] = None
    else:
        claim["evidence_ids"] = "untrusted-citation-text"
    path = module.LOCAL_DATA / "malformed-citations.zip"
    export_bundle({"transfers": [], "metadata": {}}, report, "# Synthetic report\n", path)
    original_bytes = path.read_bytes()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=quality_app),
                                 base_url="http://127.0.0.1:5186") as client:
        imported = await client.post("/api/bundles/import", files={
            "file": ("malformed-citations.zip", original_bytes, "application/zip")})
        assert imported.status_code == 422
        assert imported.json()["detail"] == "Bundle content has an unsupported schema for deterministic report checks"
        assert "untrusted-citation-text" not in imported.text
        assert path.read_bytes() == original_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ["standard", "generic"])
async def test_import_preserves_null_partial_conclusion(quality_app, layout):
    report = {"schema_version": "cluetide-report/v1" if layout == "standard" else "generic-evidence-report/v0",
              "case_id": None, "revision": None, "corrections": [],
              "conclusion": None, "agent": {"status": "partial", "report": None}}
    path = module.LOCAL_DATA / "partial.zip"
    manifest = export_bundle({"transfers": [], "metadata": {}}, report, "# Partial report\n", path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=quality_app),
                                 base_url="http://127.0.0.1:5186") as client:
        imported = await client.post("/api/bundles/import", files={
            "file": ("partial.zip", path.read_bytes(), "application/zip")})
        assert imported.status_code == 200, imported.text
        assert imported.json()["report"] == report
        assert imported.json()["manifest_hash"] == manifest_sha256(manifest)
        assert imported.json()["transfer_facts"] == []


@pytest.mark.asyncio
async def test_import_generic_typed_conclusion_without_classification(quality_app):
    report = {"schema_version": "generic-evidence-report/v0",
              "case_id": "legacy-case-1", "revision": MAX_SAFE_INTEGER,
              "corrections": [{"text": "Legacy correction remains available.", "legacy_note": True}],
              "conclusion": {"summary": "Legacy report with typed citations.",
                             "claims": [{"text": "A bounded observation.",
                                         "evidence_ids": ["legacy-observation"], "interpretation": False}]},
              "legacy_metadata": "Retained unchanged"}
    path = module.LOCAL_DATA / "generic.zip"
    manifest = export_bundle({"transfers": [], "metadata": {}}, report, "# Legacy report\n", path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=quality_app),
                                 base_url="http://127.0.0.1:5186") as client:
        imported = await client.post("/api/bundles/import", files={
            "file": ("generic.zip", path.read_bytes(), "application/zip")})
        assert imported.status_code == 200, imported.text
        assert imported.json()["report"] == report
        assert imported.json()["manifest_hash"] == manifest_sha256(manifest)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("case_id", {}), ("case_id", "unsafe/path"), ("case_id", "x" * 129),
    ("revision", {}), ("revision", 0), ("revision", True),
    ("corrections", "not-list"), ("corrections", [None]), ("corrections", [{"text": {}}]),
])
async def test_import_rejects_unsafe_display_wrapper_types(quality_app, field, value):
    report = {"schema_version": "cluetide-report/v1", "case_id": "synthetic-case", "revision": 1,
              "corrections": [], "conclusion": None}
    report[field] = value
    path = module.LOCAL_DATA / "malformed-wrapper.zip"
    export_bundle({"transfers": [], "metadata": {}}, report, "# Synthetic report\n", path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=quality_app),
                                 base_url="http://127.0.0.1:5186") as client:
        imported = await client.post("/api/bundles/import", files={
            "file": ("malformed-wrapper.zip", path.read_bytes(), "application/zip")})
        assert imported.status_code == 422
        assert imported.json()["detail"] == "Bundle content has an unsupported schema for deterministic report checks"


@pytest.mark.asyncio
async def test_import_standard_evidence_uses_strict_types(quality_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=quality_app),
                                 base_url="http://127.0.0.1:5186") as client:
        payload = {key: module.PRESET[key] for key in ("address", "token_address", "from_block", "to_block")}
        started = await client.post("/api/investigations", json={**payload, "agent_mode": "live"})
        document = await completed_document(client, started.json()["id"])
        malformed = copy.deepcopy(document["evidence"])
        assert malformed["schema_version"] == "cluetide-evidence/v1"
        malformed["request"]["chain_id"] = True
        path = module.LOCAL_DATA / "malformed-standard-evidence.zip"
        export_bundle(malformed, document["report"], "# Synthetic report\n", path)
        imported = await client.post("/api/bundles/import", files={
            "file": ("malformed-standard-evidence.zip", path.read_bytes(), "application/zip")})
        assert imported.status_code == 422
        assert imported.json()["detail"] == "Bundle content has an unsupported schema for deterministic report checks"
