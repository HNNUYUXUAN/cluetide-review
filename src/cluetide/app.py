"""Loopback-only workbench with immutable evidence bundles and local review."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import re
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .adapters import CachedRpc, InvestigationTools, load_case
from .agent import Conclusion, ExplanationAssessment, validate_assessment_bindings
from .bundles import BundleValidationError, canonical_json_bytes, export_bundle, import_bundle, manifest_sha256
from .bot_api import create_bot_router
from .collector import collect_transfers
from .collection_report import empty_window_outcome
from .case_catalog import list_cases, load_catalog_case, match_catalog_case
from .citation_validation import citation_validation
from .investigation_scope import select_investigation_transaction
from .registry import LocalRegistry, RegistryError
from .report_quality import deterministic_transfer_facts, report_quality
from .rpc import ReadOnlyRpcClient
from .request_limits import RequestBodyLimitMiddleware
from .schemas import Coverage, EvidenceSet, InvestigationRequest, MAX_SAFE_INTEGER
from .settings import ROOT, LOCAL_DATA, ethereum_endpoint, ethereum_provider_label
from .store import CaseStore
from .live import paid_session_available, budget_snapshot, run_paid

CASE_DIR = ROOT / "data" / "cases" / "uniswap93"
TX_HASH = "0x091f0083242a777d55821c1189e568d6d033d9da501b75087dc736fa143d2c1e"
PRESET = {"case_id": "uniswap93", "title": "Uniswap 提案 93", "address": "0x1a9c8182c09f50c8318d769245bea52c32be35bc", "token_address": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", "from_block": 24106368, "to_block": 24106388, "tx_hash": TX_HASH, "alert_threshold_raw": "10000000000000000000000000"}
@asynccontextmanager
async def lifespan(_app):
    global registry
    with store.service_lease():
        registry = store.initialize_registry(registry.path)
        store.recover_interrupted()
        try:
            yield
        finally:
            pending = list(tasks.items())
            for _, task in pending:
                task.cancel("service_shutdown")
            if pending:
                await asyncio.gather(*(task for _, task in pending), return_exceptions=True)
            for case_id, _ in pending:
                document = store.get(case_id)
                if document is None or document.get("versions") or document.get("status") != "running":
                    continue
                start = StartRequest.model_validate(document["input"])
                if not document.get("evidence"):
                    document["evidence"] = interrupted_collection(start, [], "service_shutdown")
                outcome = copy.deepcopy(document.get("agent") or {"report": None, "evidence": [], "trace": [],
                    "model_requests": 0, "tool_attempts": 0, "model_names": []})
                outcome.update(status="stopped", stop_reason="service_shutdown")
                _, scope = select_investigation_transaction(document["evidence"])
                await publish_execution(document, start, outcome, scope)
                stops.pop(case_id, None)
                tasks.pop(case_id, None)


app = FastAPI(title="ClueTide", docs_url=None, redoc_url=None, lifespan=lifespan)
store = CaseStore(LOCAL_DATA / "cases.sqlite3")
registry = LocalRegistry()
registry.path = LOCAL_DATA / "registry.json"
tasks: dict[str, asyncio.Task] = {}
stops: dict[str, asyncio.Event] = {}
mutation_lock = asyncio.Lock()


@app.middleware("http")
async def loopback_only(request: Request, call_next):
    if request.url.hostname not in {"127.0.0.1", "localhost", "testserver"}:
        return JSONResponse({"detail": "Loopback host required"}, status_code=403)
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed_origin = urlsplit(origin)
            allowed_origin = parsed_origin.hostname in {"127.0.0.1", "localhost"} and parsed_origin.port == request.url.port
        except ValueError:
            allowed_origin = False
        if not allowed_origin:
            return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
    if request.method != "GET" and request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


app.add_middleware(RequestBodyLimitMiddleware)


class StartRequest(InvestigationRequest):
    mode: Literal["offline", "rpc"] = "offline"
    agent_mode: Literal["offline", "live"] = "offline"


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str = Field(default="local-reviewer", min_length=1, max_length=128)
    comment: str = Field(min_length=1, max_length=4000)
    version_id: int | None = Field(default=None, ge=1)


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    author: str = Field(default="local-author", min_length=1, max_length=128)
    correction: str = Field(min_length=1, max_length=4000)
    parent_version_id: int = Field(ge=1)
    corrected_summary: str | None = Field(default=None, min_length=1, max_length=1800)
    corrected_classification: Literal["governance_explained", "unresolved", "needs_review"] | None = None
    claim_replacements: dict[int, str] = Field(default_factory=dict)
    assessment_replacements: dict[int, ExplanationAssessment] = Field(default_factory=dict, max_length=12)


def required(case_id: str) -> dict:
    document = store.get(case_id)
    if document is None:
        raise HTTPException(404, "Unknown investigation")
    return document


def public_doc(document: dict) -> dict:
    return {k: v for k, v in document.items() if k != "bundle_files"}


def validate_imported_report(report: dict) -> None:
    """Check displayed wrapper/claim types without changing the report."""
    case_id = report.get("case_id")
    if case_id is not None and (not isinstance(case_id, str) or
                               re.fullmatch(r"[A-Za-z0-9_-]{1,128}", case_id) is None):
        raise ValueError("Unsupported case identifier schema")
    revision = report.get("revision")
    if revision is not None and (type(revision) is not int or not 1 <= revision <= MAX_SAFE_INTEGER):
        raise ValueError("Unsupported revision schema")
    if "corrections" in report:
        corrections = report["corrections"]
        if not isinstance(corrections, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("text"), str) for item in corrections):
            raise ValueError("Unsupported corrections schema")
    if "conclusion" in report:
        conclusion = report["conclusion"]
    elif "report" in report:
        conclusion = report["report"]
    elif "summary" in report or "claims" in report:
        conclusion = report
    else:
        return
    # A bounded run may end without a conclusion. Its partial report remains
    # importable and must not be represented as an invented completed result.
    if conclusion is None:
        return
    if report.get("schema_version") == "cluetide-report/v1":
        Conclusion.model_validate(conclusion, strict=True)
        return
    # Generic/older report layouts need the same safe display types, while
    # retaining their existing metadata and optional classification fields.
    if not isinstance(conclusion, dict) or not isinstance(conclusion.get("summary"), str):
        raise ValueError("Unsupported conclusion schema")
    claims = conclusion.get("claims")
    if not isinstance(claims, list):
        raise ValueError("Unsupported claim schema")
    for claim in claims:
        if not isinstance(claim, dict) or not isinstance(claim.get("text"), str):
            raise ValueError("Unsupported claim schema")
        ids = claim.get("evidence_ids")
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
            raise ValueError("Unsupported citation schema")
        if not isinstance(claim.get("interpretation", False), bool):
            raise ValueError("Unsupported interpretation schema")
    if "assessments" in conclusion:
        if not isinstance(conclusion["assessments"], list):
            raise ValueError("Unsupported explanation assessment schema")
        for assessment in conclusion["assessments"]:
            ExplanationAssessment.model_validate(assessment, strict=True)


def report_markdown(report: dict) -> str:
    conclusion = report.get("conclusion") or {}
    lines = ["# ClueTide investigation", "", conclusion.get("summary", "Evidence remains incomplete."), "", f"Coverage: {report['coverage']['status']}", "", "## Deterministic transfer facts", ""]
    for fact in report.get("transfer_facts", []):
        amount = fact.get("formatted_amount")
        unit = fact.get("symbol") or fact["unit"]
        if amount is not None:
            lines.append(f"- {amount} {unit}; raw ERC-20 token base units: `{fact['raw_amount']}`; decimals: {fact['decimals']}. [{fact['evidence_id']}]")
        else:
            lines.append(f"- Raw ERC-20 token base units: `{fact['raw_amount']}`; decimals unavailable. [{fact['evidence_id']}]")
    quality = report.get("quality_validation", {})
    lines.extend(["", "## Report quality checks", "", f"Status: {quality.get('status', 'not_checked')}"])
    lines.extend("- " + flag["message"] for flag in quality.get("flags", []))
    if quality.get("scope"):
        lines.append(quality["scope"])
    citations = report.get("citation_validation", {})
    lines.extend(["", "## Citation structure checks", "", f"Status: {citations.get('status', 'not_checked')}"])
    lines.extend("- " + issue["message"] for issue in citations.get("issues", []))
    if citations.get("scope"):
        lines.append(citations["scope"])
    lines.extend(["", "## Claims and evidence", ""])
    for claim in conclusion.get("claims", []):
        lines.append(f"- {claim['text']} [{', '.join(claim['evidence_ids'])}]")
    scope = report.get("investigation_scope") or {}
    if scope:
        lines.extend(["", "## Transaction investigation scope", "",
            "Selection: " + scope.get("selection_reason", ""),
            "Selected transaction: " + (scope.get("selected_transaction_hash") or "None"),
            "Successfully read transaction: " + (scope.get("investigated_transaction_hash") or "None"),
            "Other recorded transactions awaiting investigation: " + ", ".join(scope.get("uninvestigated_transaction_hashes", [])),
            scope.get("statement", "")])
    lines.extend(["", "## Explanation assessments", ""])
    for assessment in conclusion.get("assessments", []):
        lines.extend([f"- {assessment['explanation_id']}: {assessment['explanation']} ({assessment['status']})",
            "  Supporting evidence: " + ", ".join(assessment["support_evidence_ids"]),
            "  Counter evidence: " + ", ".join(assessment["counter_evidence_ids"]),
            "  Unknowns: " + "; ".join(assessment["unknowns"]),
            "  Checks: " + "; ".join(assessment["checks"])])
    lines.extend(["", "Execution status: " + report.get("execution_status", (report.get("agent") or {}).get("status", "unknown")),
                  "Stop reason: " + (report.get("stop_reason") or "None")])
    lines.extend(["", "## Limits", ""])
    lines.extend("- " + item for item in conclusion.get("limitations", []))
    lines.extend(["- Hashes establish byte integrity, not factual truth.", "- Local roles and browser profiles are controlled demo roles; independent review has not been established."])
    if report.get("corrections"):
        lines.extend(["", "## Reviewer-driven corrections", ""])
        lines.extend("- " + item["text"] for item in report["corrections"])
    return "\n".join(lines) + "\n"


def make_bundle(document: dict, report: dict, revision: int) -> tuple[str, dict, str]:
    # Recheck the currently published conclusion on every revision. Original
    # model text stays in report.agent and in the immutable previous bundle.
    if (report.get("conclusion") or {}).get("assessments"):
        validate_assessment_bindings(Conclusion.model_validate(report["conclusion"], strict=True),
            report.get("agent", {}).get("evidence", []), require_assessments=True)
    report["transfer_facts"] = deterministic_transfer_facts(document["evidence"])
    report["quality_validation"] = report_quality(report, document["evidence"])
    report["citation_validation"] = citation_validation(report, document["evidence"])
    if report["quality_validation"]["status"] == "needs_review" or report["citation_validation"]["status"] == "needs_review":
        report["review_status"] = "needs_review"
    path = LOCAL_DATA / "bundles" / f"{document['id']}-v{revision}-{uuid.uuid4().hex}.zip"
    manifest = export_bundle(document["evidence"], report, report_markdown(report), path, immutable=True)
    return str(path), manifest, manifest_sha256(manifest)


class RecordedRpc:
    """Retain public reads while a collection is still being normalized."""
    def __init__(self, reader):
        self.reader = reader
        self.observations = []

    def captured_headers(self):
        """Expose the wrapped public cache's captured block identities."""
        headers = getattr(self.reader, "captured_headers", None)
        return copy.deepcopy(headers()) if callable(headers) else {}

    async def call(self, method, params):
        item = {"observation_id": f"execution-rpc:{len(self.observations) + 1}",
                "method": method, "params": copy.deepcopy(params)}
        self.observations.append(item)
        try:
            value = await self.reader.call(method, params)
            item["result"] = value
            return value
        except asyncio.CancelledError:
            item["error"] = "RPC read interrupted"
            raise
        except Exception:
            item["error"] = "RPC read unavailable"
            raise

    async def close(self):
        await self.reader.close()


def interrupted_collection(start: StartRequest, observations: list, reason: str) -> dict:
    request = InvestigationRequest.model_validate(start.model_dump(exclude={"mode", "agent_mode"}))
    chunks = (request.to_block - request.from_block + request.log_chunk_size) // request.log_chunk_size
    result = EvidenceSet(request=request, coverage=Coverage(
        status="partial" if observations else "error", requested_from_block=request.from_block,
        requested_to_block=request.to_block, planned_queries=2 * chunks,
        issues=["Collection normalization did not finish; recorded responses remain available for review."]),
        raw={"rpc_observations": copy.deepcopy(observations),
             "capture_mode": "public_cache" if start.mode == "offline" else "live_read_only",
             "collection_stop_reason": reason},
        warnings=["Coverage is incomplete; captured responses have not all been normalized."])
    return result.model_dump(mode="json")


def finalize_investigation_scope(scope: dict, outcome: dict) -> dict:
    scope = copy.deepcopy(scope)
    selected = scope.get("selected_transaction_hash")
    for item in outcome.get("evidence", []):
        payload = item.get("payload")
        if item.get("status") != "ok" or not isinstance(payload, dict):
            continue
        key = "transactionHash" if item.get("kind") == "receipt" else "hash" if item.get("kind") == "transaction" else None
        identity = payload.get(key) if key else None
        if isinstance(identity, str) and identity.lower() == selected:
            scope["investigated_transaction_hash"] = selected
            break
    scope["uninvestigated_transaction_hashes"] = [value for value in scope.get("observed_transaction_hashes", [])
        if value != scope.get("investigated_transaction_hash")]
    scope["execution_status"] = outcome["status"]
    scope["stop_reason"] = outcome.get("stop_reason")
    return scope


def execution_report(document: dict, start: StartRequest, outcome: dict, scope: dict) -> dict:
    outcome = copy.deepcopy(outcome)
    coverage = document["evidence"]["coverage"]
    if outcome["status"] == "completed" and coverage["status"] not in {"complete", "empty"}:
        outcome.update(status="partial", stop_reason=outcome.get("stop_reason") or "collection_incomplete")
    scope = finalize_investigation_scope(scope, outcome)
    conclusion = copy.deepcopy(outcome.get("report"))
    conclusion_source = "agent" if conclusion is not None else "deterministic_execution_record"
    if conclusion is None:
        identifier = "execution:investigation-status"
        record = {"evidence_id": identifier, "kind": "investigation_execution_status", "status": "ok",
            "payload": {"status": outcome["status"], "stop_reason": outcome.get("stop_reason"),
                "coverage_status": coverage["status"], "model_requests": outcome.get("model_requests", 0),
                "tool_attempts": outcome.get("tool_attempts", 0), "investigation_scope": scope}}
        outcome.setdefault("evidence", []).append(record)
        summary = f"调查执行状态为 {outcome['status']}，窗口采集覆盖为 {coverage['status']}；已记录模型请求 {outcome.get('model_requests', 0)} 次、工具尝试 {outcome.get('tool_attempts', 0)} 次。事件原因仍需依据保留的观察继续复核。"
        conclusion = {"summary": summary, "classification": "unresolved",
            "claims": [{"text": summary, "evidence_ids": [identifier], "interpretation": False}],
            "assessments": [{"explanation_id": "event_explanation", "explanation": "代表交易的事件原因",
                "status": "unknown", "support_evidence_ids": [], "counter_evidence_ids": [],
                "unknowns": ["本次执行尚未形成经引用约束验收的事件解释。"],
                "checks": ["保留执行计数、终止原因、覆盖状态及公开原始观察。"]}],
            "limitations": [scope["statement"], "执行记录说明已观察的进度；交易解释需进一步复核。",
                "覆盖状态说明本次节点返回及采集范围。"]}
    execution_mode = "FunctionModel offline simulation" if outcome.get("model_requests", 0) else "Offline simulation requested; no model request sent"
    if start.agent_mode == "live":
        execution_mode = ("Real model, public cache tools" if start.mode == "offline" else "Real model, read-only RPC tools") if outcome.get("model_requests", 0) else "Live model requested; no model request sent"
    if outcome.get("execution_source") == "deterministic_collection":
        execution_mode = "Deterministic empty-window report; no model request sent"
        conclusion_source = "deterministic_collection"
        if not conclusion.get("assessments"):
            conclusion["assessments"] = [{"explanation_id": "event_explanation", "explanation": "匹配交易的事件原因",
                "status": "unknown", "support_evidence_ids": [], "counter_evidence_ids": [],
                "unknowns": ["窗口内没有匹配 Transfer，本报告未形成某笔交易的因果解释。"],
                "checks": ["核读成功空结果日志查询与覆盖记录。"]}]
    document.update(agent=outcome, investigation_scope=scope, status=outcome["status"])
    return {"schema_version": "cluetide-report/v1", "case_id": document["id"], "revision": 1,
        "coverage": coverage, "conclusion": conclusion, "conclusion_source": conclusion_source,
        "agent": outcome, "investigation_scope": scope, "execution_status": outcome["status"],
        "stop_reason": outcome.get("stop_reason"), "corrections": [], "parent_manifest_hash": None,
        "review_records": [], "review_status": "awaiting_human_review", "requested_agent_mode": start.agent_mode,
        "execution_mode": execution_mode}


async def publish_execution(document: dict, start: StartRequest, outcome: dict, scope: dict) -> dict:
    target = copy.deepcopy(document)
    target["report"] = execution_report(target, start, outcome, scope)
    case_id = target["id"]
    async with mutation_lock:
        def publish_initial(current, candidate):
            if current is None:
                raise RegistryError("publication_state_mismatch")
            if current.get("versions"):
                return current
            path, manifest, digest = make_bundle(target, target["report"], 1)
            version = candidate.create_case(case_id, digest, author="local-author",
                evidence_uri=f"/api/investigations/{case_id}/bundle?revision=1")
            target.update(registry=candidate.get_case(case_id), versions=[version], reviews=[],
                manifest_hash=digest, manifest=manifest, bundle_files={str(version["version_id"]): path})
            return target
        return store.mutate_publication(registry, case_id, publish_initial)


async def execute(case_id: str, start: StartRequest):
    from .agent import AgentRequest, RuntimeLimits, build_offline_model, run_investigation
    document = required(case_id)
    rpc = backend = None
    outcome_snapshot = None
    outcome = None
    scope = None
    def capture_outcome(value):
        nonlocal outcome_snapshot
        outcome_snapshot = value.model_dump(mode="json")
    started = time.monotonic()
    try:
        try:
            async with asyncio.timeout(180):
                preset = match_catalog_case(start)
                snapshot, sources = {}, []
                if preset:
                    preset, snapshot, sources = load_catalog_case(preset["case_id"])
                reader = CachedRpc(snapshot) if start.mode == "offline" else ReadOnlyRpcClient(ethereum_endpoint())
                rpc = RecordedRpc(reader)
                request = InvestigationRequest.model_validate(start.model_dump(exclude={"mode", "agent_mode"}))
                evidence = await collect_transfers(request, rpc, deadline_seconds=90)
                evidence.raw["capture_mode"] = "public_cache" if start.mode == "offline" else "live_read_only"
                if start.mode == "rpc":
                    evidence.raw["rpc_provider"] = ethereum_provider_label()
                else:
                    evidence.warnings.append("Public snapshot replay; this execution made no fresh chain reads.")
                    evidence.raw["case_snapshot"] = snapshot
                selected_tx, scope = select_investigation_transaction(evidence)
                if not preset or selected_tx != preset["tx_hash"]:
                    sources = []
                evidence.sources = sources
                document.update(evidence=evidence.model_dump(mode="json"), investigation_scope=scope)
                store.put(case_id, document)
                if evidence.coverage.status == "empty" and evidence.finalized_anchor:
                    outcome = empty_window_outcome(evidence)
                elif not selected_tx or not evidence.finalized_anchor:
                    outcome = {"status": "partial", "report": None, "evidence": [], "trace": [],
                        "model_requests": 0, "tool_attempts": 0, "model_names": [],
                        "stop_reason": "missing_finalized_anchor_or_transaction"}
                else:
                    selected_ids = set(scope["selected_evidence_ids"])
                    initial = [*[alert for alert in document["evidence"]["alerts"] if set(alert["evidence_ids"]) & selected_ids],
                        {"coverage": document["evidence"]["coverage"]}, {"investigation_scope": scope},
                        {"evidence_id": "metadata:" + request.token_address, "kind": "token_metadata",
                            "payload": document["evidence"]["metadata"], "status": "ok" if evidence.metadata.status == "complete" else "error"}]
                    transfer_facts = [fact for fact in deterministic_transfer_facts(document["evidence"]) if fact["evidence_id"] in selected_ids]
                    if transfer_facts:
                        initial.append({"evidence_id": "derived:transfer-amounts", "kind": "deterministic_transfer_amounts", "status": "ok",
                            "payload": {"facts": transfer_facts, "source_evidence_ids": [fact["evidence_id"] for fact in transfer_facts],
                                "derivation": "Exact string formatting of observed ERC-20 raw units using captured token decimals; metadata may be unavailable."}})
                    agent_request = AgentRequest(tx_hash=selected_tx, token_address=request.token_address,
                        from_block=request.from_block, to_block=request.to_block, finalized_block=evidence.finalized_anchor.number,
                        initial_observations=initial, governance_sources={source.get("source_id", str(index)): source.get("url", "") for index, source in enumerate(sources)})
                    expected_blocks = {item.transaction_hash: {"block_number": item.block_number, "block_hash": item.block_hash}
                        for item in evidence.transfers if item.transaction_hash == selected_tx}
                    block_anchors = {item.block_number: item.block_hash for item in evidence.transfers}
                    if evidence.window_end_anchor:
                        block_anchors[evidence.window_end_anchor.number] = evidence.window_end_anchor.block_hash
                    backend = InvestigationTools(rpc, request, sources, expected_transaction_blocks=expected_blocks,
                        block_anchors=block_anchors)
                    remaining = 180 - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError
                    if start.agent_mode == "live":
                        value = await run_paid(backend, agent_request, stop_event=stops[case_id], deadline_seconds=remaining, outcome_callback=capture_outcome)
                    else:
                        value = await run_investigation(build_offline_model(), backend, agent_request, stop_event=stops[case_id],
                            limits=RuntimeLimits(deadline_seconds=remaining), outcome_callback=capture_outcome)
                    outcome = value.model_dump(mode="json")
        except asyncio.CancelledError as exc:
            reason = "user_stop" if stops.get(case_id) and stops[case_id].is_set() else "service_shutdown" if exc.args == ("service_shutdown",) else "externally_cancelled"
            outcome = copy.deepcopy(outcome_snapshot or outcome or {"evidence": [], "trace": [], "model_requests": 0, "tool_attempts": 0, "model_names": []})
            outcome.update(status="stopped", stop_reason=reason)
            document["error"] = "Investigation execution stopped; captured evidence is retained"
        except TimeoutError:
            outcome = copy.deepcopy(outcome_snapshot or outcome or {"evidence": [], "trace": [], "model_requests": 0, "tool_attempts": 0, "model_names": []})
            outcome.update(status="partial", stop_reason="deadline_exceeded")
            document["error"] = "Investigation reached its 180-second deadline"
        except Exception:
            outcome = copy.deepcopy(outcome_snapshot or outcome or {"evidence": [], "trace": [], "model_requests": 0, "tool_attempts": 0, "model_names": []})
            outcome.update(status="partial", stop_reason="collection_or_runtime_unavailable")
            document["error"] = "Investigation execution is incomplete; captured evidence is retained"
        if not document.get("evidence"):
            document["evidence"] = interrupted_collection(start, rpc.observations if rpc else [], outcome.get("stop_reason"))
        if backend is not None:
            document["evidence"]["raw"]["agent_rpc_observations"] = copy.deepcopy(backend.observations)
        if scope is None:
            _, scope = select_investigation_transaction(document["evidence"])
        while True:
            try:
                document = await publish_execution(document, start, outcome, scope)
                break
            except asyncio.CancelledError:
                outcome.update(status="stopped", stop_reason="user_stop" if stops.get(case_id) and stops[case_id].is_set() else "externally_cancelled")
            except Exception:
                current = required(case_id)
                if not current.get("versions"):
                    document.update(status="partial", error="Local publication is unavailable; captured evidence is retained",
                        agent=outcome, report=None, versions=[])
                    store.put(case_id, document)
                break
    finally:
        try:
            if rpc is not None:
                async with asyncio.timeout(2):
                    await rpc.close()
        except Exception:
            pass
        finally:
            stops.pop(case_id, None)
            tasks.pop(case_id, None)


@app.get("/api/health")
def health():
    available = paid_session_available()
    return {"status": "ok", "version": "0.1.0", "rpc_available": True, "paid_agent_available": available, "live_agent_available": available, "model_budget": budget_snapshot(), "registry_mode": "local_state_simulation", "publication_storage": "sqlite_atomic", "registry_mirror_pending": store.registry_mirror_pending, "network_scope": "loopback", "paid_agent_reason": "Session authorization, current route quote, spendable balance and the shared hard budget are checked before paid execution."}


@app.get("/api/cases")
def case_catalog():
    return {"cases": [public_preset(item["case_id"]) for item in list_cases()]}


def public_preset(case_id: str) -> dict:
    selected = next((item for item in list_cases() if item["case_id"] == case_id), None)
    if selected is None:
        raise HTTPException(404, "Unknown public case")
    try:
        selected, _, sources = load_catalog_case(case_id)
        return {**selected, "data_status": "public_cache_available", "sources": sources}
    except ValueError:
        return {**selected, "data_status": "capture_pending", "sources": []}


@app.get("/api/cases/{case_id}")
def preset(case_id: str):
    return public_preset(case_id)


@app.get("/api/investigations")
def investigations():
    return [public_doc(item) for item in store.list()]


@app.post("/api/investigations", status_code=202)
async def start_investigation(start: StartRequest):
    if start.agent_mode == "live" and not paid_session_available():
        raise HTTPException(409, "Paid agent execution is locked pending verified route pricing and acceptance")
    selected_preset = match_catalog_case(start)
    if selected_preset and "alert_threshold_raw" not in start.model_fields_set:
        start = start.model_copy(update={"alert_threshold_raw": selected_preset["alert_threshold_raw"]})
    if start.mode == "offline":
        if not selected_preset:
            raise HTTPException(422, "Public cache requires an exact catalogued investigation window")
        try:
            load_catalog_case(selected_preset["case_id"])
        except ValueError:
            raise HTTPException(422, "The selected public case capture is unavailable") from None
    if len(tasks) >= 2:
        raise HTTPException(429, "At most two local investigations may run concurrently")
    case_id = uuid.uuid4().hex
    document = {"id": case_id, "title": selected_preset["title"] if selected_preset else "Ethereum 调查", "status": "running", "input": start.model_dump(), "evidence": None, "agent": None, "report": None, "registry": None, "versions": [], "reviews": []}
    store.put(case_id, document)
    stops[case_id] = asyncio.Event()
    tasks[case_id] = asyncio.create_task(execute(case_id, start))
    return public_doc(document)


@app.get("/api/investigations/{case_id}")
def investigation(case_id: str):
    # A GET may return an older snapshot during execution, but never writes it
    # over a result that committed after the read. Recovery runs at startup.
    return public_doc(required(case_id))


@app.post("/api/investigations/{case_id}/stop")
async def stop_investigation(case_id: str):
    document = required(case_id)
    if document.get("versions"):
        return public_doc(document)
    if case_id in stops:
        stops[case_id].set()
        document.update(status="stopped", error="User stopped the investigation")
        store.put(case_id, document)
        task = tasks[case_id]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        current = required(case_id)
        if not current.get("versions"):
            start = StartRequest.model_validate(current["input"])
            if not current.get("evidence"):
                current["evidence"] = interrupted_collection(start, [], "user_stop")
            outcome = copy.deepcopy(current.get("agent") or {"report": None, "evidence": [], "trace": [],
                "model_requests": 0, "tool_attempts": 0, "model_names": []})
            outcome.update(status="stopped", stop_reason="user_stop")
            _, scope = select_investigation_transaction(current["evidence"])
            await publish_execution(current, start, outcome, scope)
        stops.pop(case_id, None)
        tasks.pop(case_id, None)
    return public_doc(required(case_id))


@app.get("/api/investigations/{case_id}/bundle")
def download_bundle(case_id: str, version: int | None = None, revision: int | None = None):
    document = required(case_id)
    if not document.get("bundle_files"):
        raise HTTPException(409, "No evidence bundle is ready")
    if revision is not None:
        if not 1 <= revision <= len(document["versions"]):
            raise HTTPException(404, "Unknown revision")
        version_id = document["versions"][revision - 1]["version_id"]
    else:
        version_id = version if version is not None else document["versions"][-1]["version_id"]
    path = document["bundle_files"].get(str(version_id))
    if path is None:
        raise HTTPException(404, "Unknown version")
    committed = next((item for item in document["versions"] if item["version_id"] == version_id), None)
    try:
        verified = import_bundle(Path(path))
        if committed is None or manifest_sha256(verified.manifest) != committed["content_hash"]:
            raise HTTPException(409, "The archive does not match its version commitment")
    except BundleValidationError:
        raise HTTPException(409, "The committed archive is unavailable or damaged") from None
    return FileResponse(path, media_type="application/zip", filename=f"cluetide-{case_id}-v{version_id}.zip")


@app.post("/api/bundles/import")
async def verify_bundle(file: UploadFile = File(...)):
    payload = await file.read(20_000_001)
    if len(payload) > 20_000_000:
        raise HTTPException(413, "Evidence bundle exceeds the 20 MB import limit")
    path = LOCAL_DATA / "imports" / (uuid.uuid4().hex + ".zip")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    try:
        verified = import_bundle(path)
        digest = manifest_sha256(verified.manifest)
        # The archive's report may predate quality fields or claim its own
        # validation. Compute separate checks without changing its bytes/hash.
        computation_evidence = {**verified.evidence, "raw": verified.raw}
        try:
            if verified.evidence.get("schema_version") == "cluetide-evidence/v1":
                EvidenceSet.model_validate(computation_evidence, strict=True)
            validate_imported_report(verified.report)
            computed_facts = deterministic_transfer_facts(computation_evidence)
            computed_quality = report_quality(verified.report, computation_evidence)
            computed_citations = citation_validation(verified.report, computation_evidence)
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422, "Bundle content has an unsupported schema for deterministic report checks") from None
        return {"status": "verified", "validation": verified.validation, "manifest": verified.manifest, "manifest_hash": digest, "evidence": verified.evidence, "report": verified.report, "report_markdown": verified.report_markdown, "report_quality": computed_quality, "citation_validation": computed_citations, "transfer_facts": computed_facts, "registry_verification": "unknown: independent chain commitment was not read", "independence": "Second browser profile on the same host and local server; controlled demo roles."}
    except BundleValidationError as exc:
        raise HTTPException(422, str(exc)) from None
    finally:
        path.unlink(missing_ok=True)


@app.post("/api/investigations/{case_id}/reviews")
async def review(case_id: str, body: ReviewRequest):
    async with mutation_lock:
        def publish_review(document, candidate):
            if document is None:
                raise HTTPException(404, "Unknown investigation")
            if not document["versions"]:
                raise HTTPException(409, "No committed version is ready")
            version_id = body.version_id or document["versions"][-1]["version_id"]
            version = next((v for v in document["versions"] if v["version_id"] == version_id), None)
            if version is None:
                raise HTTPException(404, "Unknown version")
            review_data = {"case_id": case_id, "version_id": version_id, "content_hash": version["content_hash"], "comment": body.comment, "reviewer": body.reviewer}
            digest = hashlib.sha256(canonical_json_bytes(review_data)).hexdigest()
            record = candidate.add_review(case_id, version_id, version["content_hash"], digest, reviewer=body.reviewer)
            document["reviews"].append({**record, "comment": body.comment})
            document["registry"] = candidate.get_case(case_id)
            return document
        document = store.mutate_publication(registry, case_id, publish_review)
        return public_doc(document)


@app.post("/api/investigations/{case_id}/versions")
async def correction(case_id: str, body: CorrectionRequest):
    async with mutation_lock:
        def publish_correction(document, candidate):
            if document is None:
                raise HTTPException(404, "Unknown investigation")
            return build_correction(document, candidate, case_id, body)
        document = store.mutate_publication(registry, case_id, publish_correction)
        return public_doc(document)


def build_correction(document: dict, candidate: LocalRegistry, case_id: str, body: CorrectionRequest) -> dict:
    if not document["versions"]:
        raise HTTPException(409, "No committed version is ready")
    parent = document["versions"][-1]
    if body.author != document["registry"]["author"] or body.parent_version_id != parent["version_id"]:
        raise HTTPException(409, "Author permission or current-parent check failed")
    revision = len(document["versions"]) + 1
    report = copy.deepcopy(document["report"])
    report.update(revision=revision, parent_manifest_hash=parent["content_hash"], review_records=document["reviews"], review_status="revised_by_local_author", corrections=[*report.get("corrections", []), {"text": body.correction, "author": body.author, "parent_version_id": body.parent_version_id}])
    if body.corrected_summary is not None:
        if report.get("conclusion") is None:
            raise HTTPException(409, "No cited conclusion is available to revise")
        report["conclusion"]["summary"] = body.corrected_summary
    for index, text in body.claim_replacements.items():
        claims = (report.get("conclusion") or {}).get("claims", [])
        if not 0 <= index < len(claims) or not 1 <= len(text) <= 1400:
            raise HTTPException(422, "Claim replacement index or text is invalid")
        claims[index]["text"] = text
    if body.corrected_classification is not None:
        if report.get("conclusion") is None:
            raise HTTPException(409, "No cited conclusion is available to revise")
        report["conclusion"]["classification"] = body.corrected_classification
    assessments = (report.get("conclusion") or {}).get("assessments", [])
    for index, assessment in body.assessment_replacements.items():
        if not 0 <= index < len(assessments):
            raise HTTPException(422, "Assessment replacement index is invalid")
        if assessment.explanation_id != assessments[index]["explanation_id"]:
            raise HTTPException(422, "Assessment replacement must retain its explanation identifier")
        assessments[index] = assessment.model_dump(mode="json")
    if body.assessment_replacements or body.corrected_classification is not None:
        try:
            conclusion = Conclusion.model_validate(report["conclusion"], strict=True)
            validate_assessment_bindings(conclusion, (report.get("agent") or {}).get("evidence", []),
                                         require_assessments=bool(body.assessment_replacements or conclusion.assessments or
                                                                  body.corrected_classification == "governance_explained"))
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422, "Revised explanation assessments do not match the recorded evidence bindings") from None
    path, manifest, digest = make_bundle(document, report, revision)
    version = candidate.append_version(case_id, body.parent_version_id, digest, author=body.author, evidence_uri=f"/api/investigations/{case_id}/bundle?revision={revision}")
    document["report"] = report
    document["versions"].append(version)
    document["registry"] = candidate.get_case(case_id)
    document["bundle_files"][str(version["version_id"])] = path
    document.update(manifest=manifest, manifest_hash=digest)
    return document

@app.exception_handler(BundleValidationError)
async def bundle_error(request: Request, exc: BundleValidationError):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(RegistryError)
async def registry_error(request: Request, exc: RegistryError):
    return JSONResponse({"detail": exc.code}, status_code=409)


@app.exception_handler(sqlite3.Error)
async def storage_error(request: Request, exc: sqlite3.Error):
    return JSONResponse({"detail": "Local storage is unavailable; reload the committed case before retrying"}, status_code=503)


app.include_router(create_bot_router(required))

DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="workbench")
