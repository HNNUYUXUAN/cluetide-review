"""Public-case comparisons with equal read budgets and one shared paid ledger.

The conditional baseline collects evidence from an explicit policy and produces
a conservative report without an LLM. The one-shot baseline receives that same
collected evidence. The adaptive agent starts at the screened observations and
chooses its own follow-up reads. Neither baseline is a fact-verification oracle.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .agent import (AgentOutcome, AgentRequest, RuntimeLimits, ToolEvidence,
                    compare_supply_observations, create_gateway_model, run_investigation)
from .budget import BudgetLedger, PriceQuote
from .report_quality import deterministic_transfer_facts, report_quality
from .schemas import EvidenceSet


class MissingContextBackend:
    """Public stress variant: historical supply reads fail and context is absent.

    Original chain receipt and transaction remain unchanged. These injected read
    failures describe a test condition, not the provider's actual availability.
    """
    def __init__(self, backend):
        self.backend = backend

    async def get_receipt(self, tx_hash):
        return await self.backend.get_receipt(tx_hash)

    async def get_transaction(self, tx_hash):
        return await self.backend.get_transaction(tx_hash)

    async def get_token_state(self, token_address, block_number):
        raise RuntimeError("Explicit evaluation stress condition: supply read unavailable")

    async def get_governance_source(self, source_id):
        raise RuntimeError("Explicit evaluation stress condition: context unavailable")


def initial_evidence(evidence: EvidenceSet) -> list[dict]:
    items = [{"evidence_id": record.evidence_id, "kind": "transfer", "status": "ok",
              "payload": record.model_dump(mode="json")} for record in evidence.transfers]
    items.extend([
        {"evidence_id": "derived:transfer-amounts", "kind": "transfer_amounts", "status": "ok",
         "payload": deterministic_transfer_facts(evidence)},
        {"evidence_id": "collection:coverage", "kind": "collection_coverage", "status": "ok",
         "payload": evidence.coverage.model_dump(mode="json")},
    ])
    return items


async def conditional_followup(backend, request: AgentRequest, *, max_tools: int = 8) -> dict:
    """Apply a strong, documented conditional read policy within the agent budget.

    Receipt and transaction cross-check identity and execution. A large transfer
    or dead recipient triggers historical supply comparison. Allowed context is
    read for the observed transaction, keeping all available sources up to the
    remaining budget. Errors count as attempts and remain in the result.
    """
    if not 1 <= max_tools <= 8:
        raise ValueError("Conditional policy uses at most eight read attempts")
    evidence = [ToolEvidence.model_validate(item) for item in request.initial_observations if "evidence_id" in item]
    trace = []
    attempts = 0

    async def read(name: str, arguments: dict) -> ToolEvidence | None:
        nonlocal attempts
        if attempts >= max_tools:
            return None
        attempts += 1
        try:
            value = getattr(backend, name)(**arguments)
            if inspect.isawaitable(value):
                value = await asyncio.wait_for(value, timeout=20)
            item = ToolEvidence.model_validate(value)
        except Exception:
            item = ToolEvidence(evidence_id=f"conditional:error:{attempts}", kind=name,
                                payload={"error": "read_unavailable", "arguments": arguments}, status="error")
        evidence.append(item)
        trace.append({"tool": name, "arguments": arguments, "status": item.status,
                      "evidence_id": item.evidence_id})
        return item

    started = time.monotonic()
    receipt = await read("get_receipt", {"tx_hash": request.tx_hash})
    await read("get_transaction", {"tx_hash": request.tx_hash})
    payload = receipt.payload if receipt and receipt.status == "ok" and isinstance(receipt.payload, dict) else {}
    block = payload.get("blockNumber")
    try:
        event_block = int(block, 16) if isinstance(block, str) else block if type(block) is int else None
    except (ValueError, TypeError):
        event_block = None
    transfers = [item for item in evidence if item.kind == "transfer"]
    if (event_block is not None and request.from_block <= event_block <= request.to_block
            and (transfers or "dead" in json.dumps(payload).lower())):
        comparison_blocks = list(dict.fromkeys([
            max(request.from_block, event_block - 1), min(request.to_block, event_block)]))
        for number in comparison_blocks:
            await read("get_token_state", {"token_address": request.token_address, "block_number": number})
    # Context is allowed only from the case's approved identifiers. Reading all
    # that fit makes the baseline stronger than selecting the first convenient
    # source and omitting potentially conflicting accounts.
    for source_id in request.governance_sources:
        await read("get_governance_source", {"source_id": source_id})
    successful = [item for item in evidence if item.status == "ok"]
    states = [item for item in successful if item.kind == "token_state"]
    source_items = [item for item in successful if item.kind in {"governance_source", "public_context_source"}]
    receipt_ids = [item.evidence_id for item in successful if item.kind in {"receipt", "transaction"}]
    assessments = [{"explanation_id": "event_explanation", "explanation": "交易与允许的背景来源可以支持有限事件解释。",
                    "status": "unknown", "support_evidence_ids": receipt_ids,
                    "counter_evidence_ids": [], "unknowns": ["来源与实际执行的语义对应仍需逐条人工核读。"],
                    "checks": ["交叉读取回执和交易；读取预算内全部允许来源。"]}]
    state_by_id = {item.evidence_id: item for item in states}
    comparison = (compare_supply_observations([item.evidence_id for item in states], state_by_id, request)
                  if len(state_by_id) == len(states) else None)
    if comparison is not None:
        decreased = comparison[1] < comparison[0]
        state_ids = [item.evidence_id for item in states]
        assessments.append({"explanation_id": "supply_decrease", "explanation": "所比较区块末的 totalSupply getter 减少。",
                            "status": "supported" if decreased else "refuted",
                            "support_evidence_ids": state_ids if decreased else [],
                            "counter_evidence_ids": [] if decreased else state_ids,
                            "unknowns": ["端点净变化的原因与区间内完整状态变化仍需另外调查。"],
                            "checks": ["比较两个有界历史区块的 getter 原始整数。"]})
    else:
        assessments.append({"explanation_id": "supply_decrease", "explanation": "所选转账伴随 totalSupply 减少。",
                            "status": "unknown", "support_evidence_ids": [], "counter_evidence_ids": [],
                            "unknowns": ["没有取得同一代币、窗口内两个可比较的历史 getter。"],
                            "checks": ["验证代币身份、两个不同区块端点与完整 uint256 原始整数；失败读取保留。"]})
    claims = [{"text": "采集记录保留了所选交易与有限窗口的观察。",
               "evidence_ids": receipt_ids or ["collection:coverage"], "interpretation": False}]
    facts_item = next((item for item in successful if item.kind == "transfer_amounts"), None)
    if facts_item and isinstance(facts_item.payload, list):
        for fact in facts_item.payload[:8]:
            amount = fact.get("formatted_amount")
            unit = fact.get("symbol") or "token units"
            text = (f"观察金额 {amount} {unit}；原始 ERC-20 整数 {fact['raw_amount']}，decimals={fact['decimals']}。"
                    if amount is not None else f"观察原始 ERC-20 整数 {fact['raw_amount']}；decimals 未取得。")
            claims.append({"text": text, "evidence_ids": [facts_item.evidence_id], "interpretation": False})
    if source_items:
        claims.append({"text": "允许的背景来源已取得；来源记载与链上执行需分别核读。",
                       "evidence_ids": [item.evidence_id for item in source_items][:8], "interpretation": True})
    linked_incident = [item for item in source_items if item.kind == "public_context_source"
                       and isinstance(item.payload, dict)
                       and item.payload.get("linked_transaction_hash") == request.tx_hash]
    if linked_incident and receipt_ids and payload.get("status") == "0x1":
        assessments[0] = {"explanation_id": "reported_protocol_exploit",
                          "explanation": "官方事件复盘所指交易与本次成功回执对应。",
                          "status": "supported", "support_evidence_ids": receipt_ids + [linked_incident[0].evidence_id],
                          "counter_evidence_ids": [],
                          "unknowns": ["该对应只覆盖所选代币和交易，未重现调用内的完整漏洞过程。"],
                          "checks": ["比较复盘记录的交易哈希与成功链上回执；核对有限资金流。"]}
        claims.append({"text": "事件复盘明确链接所选交易；成功回执支持这项身份对应。",
                       "evidence_ids": receipt_ids + [linked_incident[0].evidence_id], "interpretation": True})
    conflicts = [item for item in source_items if isinstance(item.payload, dict) and item.payload.get("source_quality_note")]
    if conflicts:
        assessments.append({"explanation_id": "source_consistency", "explanation": "背景材料的时间叙述一致。",
                            "status": "unknown", "support_evidence_ids": [], "counter_evidence_ids": [],
                            "unknowns": [item.payload["source_quality_note"][:600] for item in conflicts[:8]],
                            "checks": ["保留来源质量记录；链上时间应回查原始区块 timestamp。"]})
    classification = "needs_review" if source_items else "unresolved"
    # A case-specific, auditable rule connects known GovernorBravo execute(93),
    # the successful receipt and proposal's exact treasury transfer. This is a
    # useful deterministic competitor, with its narrow applicability explicit.
    transaction = next((item.payload for item in successful if item.kind == "transaction"), {})
    proposal = next((item for item in source_items if isinstance(item.payload, dict)
                     and item.payload.get("source_id") == "uniswap-proposal-93"), None)
    dead_transfers = [item for item in transfers if isinstance(item.payload, dict)
                      and item.payload.get("to_address") == "0x000000000000000000000000000000000000dead"
                      and item.payload.get("from_address") == "0x1a9c8182c09f50c8318d769245bea52c32be35bc"
                      and item.payload.get("value_raw") == "100000000000000000000000000"]
    if (proposal and proposal.payload.get("excerpt") == "UNI.transfer(0xdead, 100_000_000 ether);"
            and isinstance(transaction, dict)
            and transaction.get("to", "").lower() == "0x408ed6354d4973f66138c91495f2f2fcbd8724c3"
            and transaction.get("input", "").lower() == "0xfe0d94c1" + f"{93:064x}"
            and payload.get("status") == "0x1" and dead_transfers):
        governance_ids = receipt_ids + [proposal.evidence_id, dead_transfers[0].evidence_id]
        assessments[0] = {"explanation_id": "governance_execution",
                          "explanation": "所选大额 UNI 转账与提案93的 GovernorBravo 执行对应。",
                          "status": "supported", "support_evidence_ids": governance_ids,
                          "counter_evidence_ids": [], "unknowns": ["该对应不能排除其他恶意活动或证明所有提案动作。"],
                          "checks": ["固定 GovernorBravo 地址、execute(uint256) 选择器与参数93；成功回执；国库到dead精确金额；提案调用摘录。"]}
        claims.append({"text": "固定规则核对发现成功 execute(93) 交易及提案记载的国库到dead转账对应。",
                       "evidence_ids": governance_ids, "interpretation": True})
        classification = "governance_explained"
    report = {"summary": "确定性条件策略完成预算内补查，保持事件原因与安全范围的有限判断。",
              "classification": classification, "claims": claims,
              "assessments": assessments,
              "limitations": ["条件脚本没有使用语言模型解释源码或任意调用的语义。",
                              "工具错误和未取得背景保留为未知。节点观测及文件哈希不等于事实认证。"]}
    return {"strategy": "conditional", "status": "completed", "report": report,
            "evidence": [item.model_dump(mode="json") for item in evidence], "trace": trace,
            "tool_attempts": attempts, "model_requests": 0, "latency_seconds": round(time.monotonic() - started, 3),
            "successful_new_evidence_ids": [item.evidence_id for item in successful if item.evidence_id not in
                                            {initial.get("evidence_id") for initial in request.initial_observations}]}


def measure_outcome(outcome: AgentOutcome, initial_ids: set[str], elapsed: float, evidence: EvidenceSet) -> dict:
    report = outcome.report.model_dump(mode="json") if outcome.report else None
    known = {item.evidence_id: item for item in outcome.evidence}
    references = [identifier for claim in (report or {}).get("claims", []) for identifier in claim["evidence_ids"]]
    assessment_refs = [identifier for item in (report or {}).get("assessments", [])
                       for key in ("support_evidence_ids", "counter_evidence_ids") for identifier in item[key]]
    return {**outcome.model_dump(mode="json"), "latency_seconds": round(elapsed, 3),
            "successful_new_evidence_ids": sorted(identifier for identifier, item in known.items()
                                                  if item.status == "ok" and identifier not in initial_ids),
            "citation_structure": {"status": "checked" if report else "not_reported",
                                   "references": len(references) + len(assessment_refs),
                                   "successful_ids_only": all(identifier in known and known[identifier].status == "ok"
                                                              for identifier in references + assessment_refs)},
            "quality_screen": report_quality(report, evidence) if report else None,
            "semantic_support_review": "manual_review_required",
            "expected_amount_facts": deterministic_transfer_facts(evidence)}


async def run_comparison(case_id: str, *, variant: str, repeat: int,
                         ledger: BudgetLedger, quote: PriceQuote, checkpoint=None) -> dict:
    import httpx
    from .adapters import CachedRpc, InvestigationTools
    from .case_catalog import load_catalog_case
    from .collector import collect_transfers
    from .investigation_scope import select_investigation_transaction
    from .live import paid_session_available
    from .schemas import InvestigationRequest
    from .settings import gateway_key

    if variant not in {"complete", "missing_context"} or not 1 <= repeat <= 3:
        raise ValueError("Evaluation variant or repetition is outside the bounded scope")
    result = {"schema_version": "cluetide-gcc-comparison/v1", "case_id": case_id, "variant": variant,
              "recorded_at": datetime.now(timezone.utc).isoformat(), "status": "partial", "runs": [],
              "data_mode": "verified_public_case_cache", "model": "deepseek-v3.2",
              "reasoning_mode": "disabled", "max_context_bytes": 64000, "max_output_tokens": 4096,
              "read_budget_per_strategy": 8, "adaptive_model_limit": 4, "one_shot_model_limit": 1,
              "budget_before": ledger.snapshot(), "signed_or_broadcast_transactions": 0,
              "variant_note": "Original public case" if variant == "complete" else
              "Explicit synthetic availability stress: approved context omitted and historical state reads fail; chain receipt unchanged."}
    from pathlib import Path
    import subprocess
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2]).decode().strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    result["runtime_source"] = {"baseline_commit": revision,
        "files": [{"path": "src/cluetide/" + name, "sha256": hashlib.sha256(
            Path(__file__).with_name(name).read_bytes()).hexdigest()}
            for name in ("agent.py", "adapters.py", "budget.py", "evaluation.py", "report_quality.py", "schemas.py")]}
    result["timing_scope"] = ("Shared initial cached Transfer collection excluded. Adaptive timing includes its dynamic reads. "
                              "Conditional evidence is prepared once and reused by every one-shot repetition. "
                              "One-shot end-to-end values attribute that preparation to a standalone run; do not sum them as actual repeated work. "
                              "Cached read timing does not estimate live network investigation latency.")
    if not paid_session_available():
        result["stop_reason"] = "paid_session_unavailable"
        return result
    preset, snapshot, sources = load_catalog_case(case_id)
    request = InvestigationRequest(**{key: preset[key] for key in
        ("address", "token_address", "from_block", "to_block", "alert_threshold_raw")})
    evidence = await collect_transfers(request, CachedRpc(snapshot))
    tx_hash, scope = select_investigation_transaction(evidence)
    result.update(collection=evidence.model_dump(mode="json"), investigation_scope=scope)
    if not tx_hash or not evidence.finalized_anchor:
        result["stop_reason"] = "no_finalized_transaction"
        return result
    if variant == "missing_context":
        sources = []
    anchors = {record.transaction_hash: {"block_number": record.block_number, "block_hash": record.block_hash}
               for record in evidence.transfers}

    def backend():
        value = InvestigationTools(CachedRpc(snapshot), request, sources, expected_transaction_blocks=anchors)
        return MissingContextBackend(value) if variant == "missing_context" else value

    initial = initial_evidence(evidence)
    agent_request = AgentRequest(tx_hash=tx_hash, token_address=request.token_address,
        from_block=request.from_block, to_block=request.to_block, finalized_block=evidence.finalized_anchor.number,
        initial_observations=initial, governance_sources={item["source_id"]: item["url"] for item in sources})
    conditional = await conditional_followup(backend(), agent_request)
    result["conditional_baseline"] = conditional
    result["actual_investigation_read_attempts"] = conditional["tool_attempts"]
    initial_ids = {item["evidence_id"] for item in initial}
    quote.validate()
    for repetition in range(1, repeat + 1):
        for strategy in ("adaptive", "one_shot"):
            if not paid_session_available():
                result["stop_reason"] = "paid_session_unavailable"
                break
            # Both model strategies use the same compact non-thinking output
            # configuration. Prior thinking-mode boundary failures are retained
            # as separate immutable experiment records.
            # A bounded final report gets 4096 tokens. The 64 KB public envelope
            # limits conservative reservations as well as provider context.
            limits = RuntimeLimits(max_model_requests=4 if strategy == "adaptive" else 1,
                                   max_context_bytes=64000, max_output_tokens=4096, deadline_seconds=180)
            if Decimal(ledger.snapshot()["remaining_rmb"]) <= 0:
                result["stop_reason"] = "remaining_budget_exhausted"
                break
            used_initial = initial if strategy == "adaptive" else conditional["evidence"]
            scope_request = agent_request.model_copy(update={"initial_observations": copy.deepcopy(used_initial)})
            protocol = []
            async def observe_response(response):
                if response.request.url.path.endswith("/chat/completions"):
                    await response.aread()
                    safe = {"status": response.status_code}
                    if response.status_code == 200:
                        value = response.json()
                        message = value.get("choices", [{}])[0].get("message", {})
                        safe.update(usage=value.get("usage"), tool_calls=message.get("tool_calls", []),
                                    finish_reason=value.get("choices", [{}])[0].get("finish_reason"),
                                    reasoning_content_received=bool(message.get("reasoning_content")))
                    protocol.append(safe)
            started = time.monotonic()
            before = ledger.snapshot()
            async with httpx.AsyncClient(timeout=60, follow_redirects=False,
                                         event_hooks={"response": [observe_response]}) as client:
                model = create_gateway_model(gateway_key(), model_name="deepseek-v3.2", http_client=client, thinking=False)
                outcome = await run_investigation(model, backend(), scope_request, limits=limits,
                    ledger=ledger, price_quotes={quote.model: quote}, paid_enabled=True, strategy=strategy)
            record = measure_outcome(outcome, {item["evidence_id"] for item in used_initial},
                                     time.monotonic() - started, evidence)
            record.update(strategy=strategy, repetition=repetition, protocol=protocol,
                          budget_before=before, budget_after=ledger.snapshot(),
                          end_to_end_read_attempts=outcome.tool_attempts + (
                              0 if strategy == "adaptive" else conditional["tool_attempts"]),
                          end_to_end_accounting_scope="standalone_attributed_preparation",
                          precollection_reused=strategy == "one_shot",
                          precollection_latency_seconds=0 if strategy == "adaptive" else conditional["latency_seconds"],
                          end_to_end_strategy_seconds=round(time.monotonic() - started + (
                              0 if strategy == "adaptive" else conditional["latency_seconds"]), 3))
            result["runs"].append(record)
            result["actual_investigation_read_attempts"] = conditional["tool_attempts"] + sum(
                item["tool_attempts"] for item in result["runs"])
            result["budget_after"] = ledger.snapshot()
            if checkpoint is not None:
                checkpoint(copy.deepcopy(result))
        if result.get("stop_reason"):
            break
    result["budget_after"] = ledger.snapshot()
    expected = repeat * 2
    result["execution_coverage"] = "planned_runs_recorded" if len(result["runs"]) == expected else "partial"
    result["status"] = "completed" if len(result["runs"]) == expected and all(
        item["status"] == "completed" for item in result["runs"]) else "partial"
    result["report_semantic_review"] = "pending_manual_public_evidence_review"
    return result
