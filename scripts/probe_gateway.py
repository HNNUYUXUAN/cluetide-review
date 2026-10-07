r"""Public-price probe and explicitly enabled bounded live acceptance.

Run: .venv\Scripts\python.exe scripts\probe_gateway.py --prices
The default path loads no credential and makes no paid request. --live uses the
existing configured key after a read-only balance check; never logs credentials.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import hashlib
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from cluetide.budget import fetch_official_price_quote


async def public_prices() -> dict:
    from cluetide.settings import preview_only
    if preview_only():
        return {"status": "offline_preview", "paid_requests": 0, "network_reads": 0}
    results = {}
    for model in ("deepseek-v3.2", "minimax-m2.7"):
        try:
            quote = await fetch_official_price_quote(model)
            results[model] = {
                "status": "verified_public_price", "provider": quote.provider_slug,
                "input_rmb_per_million": str(quote.input_rmb_per_million),
                "output_rmb_per_million": str(quote.output_rmb_per_million),
                "verified_at": quote.verified_at.isoformat(), "source_url": quote.source_url,
                "source_sha256": quote.source_sha256,
                "paid_requests": 0, "account_balance_verified": False,
            }
        except Exception:
            # Do not print arbitrary request exception content.
            results[model] = {"status": "price_verification_failed", "paid_requests": 0}
    return results


async def live_acceptance(*, allow_fallback: bool = True) -> dict:
    import httpx
    from cluetide.adapters import CachedRpc, InvestigationTools, load_case
    from cluetide.agent import AgentRequest, RuntimeLimits, create_gateway_model, run_investigation
    from cluetide.budget import BudgetLedger, read_gateway_balance
    from cluetide.schemas import InvestigationRequest
    from cluetide.settings import LOCAL_DATA, ROOT, gateway_key
    from cluetide.live import paid_session_available

    result = {"schema_version": "cluetide-gateway-acceptance/v1",
              "observed_at": datetime.now(timezone.utc).isoformat(),
              "status": "blocked", "paid_model_requests": 0,
              "tool_data_mode": "real_public_chain_and_governance_cache",
              "network_transactions_signed_or_broadcast": 0}
    if not paid_session_available():
        result["reason"] = "paid_session_unavailable"
        return result
    api_key = gateway_key()
    if not api_key:
        result["reason"] = "configured_gateway_key_missing"
        return result
    ledger = BudgetLedger(LOCAL_DATA / "model-budget.sqlite3")
    result["budget_before"] = ledger.snapshot()
    try:
        balance = await read_gateway_balance(api_key)
        balance_micro = balance["balance_micro_rmb"]
        result["account_balance_verified"] = True
        result["balance_source_url"] = "https://tokendance.space/docs/open-api.md"
        result["balance_field_used"] = "balance.balance"
        result["balance_sufficient_for_session_cap"] = balance_micro >= 5_000_000
        if balance_micro <= 0:
            result["reason"] = "insufficient_balance"
            return result
        # A smaller provider balance also lowers the durable local cap.
        if balance_micro < 5_000_000:
            ledger = BudgetLedger(LOCAL_DATA / "model-budget.sqlite3",
                                  cap_rmb=Decimal(balance_micro) / Decimal(1_000_000))
        quotes = {name: await fetch_official_price_quote(name)
                  for name in ("deepseek-v3.2", "minimax-m2.7")}
        result["official_route_quotes"] = {name: {
            "provider": quote.provider_slug, "input_rmb_per_million": str(quote.input_rmb_per_million),
            "output_rmb_per_million": str(quote.output_rmb_per_million), "source_url": quote.source_url,
            "source_sha256": quote.source_sha256, "verified_at": quote.verified_at.isoformat()
        } for name, quote in quotes.items()}
        route_names = ("deepseek-v3.2", "minimax-m2.7") if allow_fallback else ("deepseek-v3.2",)
        worst_request = max(quotes[name].upper_bound_micro_rmb(input_tokens=2 * 64000 + 4096,
                                                              output_tokens=2048) for name in route_names)
        worst_task_micro = worst_request * 6
        remaining_micro = int(Decimal(ledger.snapshot()["remaining_rmb"]) * 1_000_000)
        result["worst_task_reservation_rmb"] = str(Decimal(worst_task_micro) / 1_000_000)
        result["worst_task_fits_remaining_budget"] = worst_task_micro <= remaining_micro
        if worst_task_micro > remaining_micro:
            result["reason"] = "worst_task_reservation_exceeds_remaining_budget"
            return result
    except Exception:
        result["reason"] = "balance_or_official_price_read_failed"
        return result

    snapshot, sources = load_case(ROOT / "data" / "cases" / "uniswap93")
    if not snapshot or not sources:
        result["reason"] = "public_evidence_cache_missing"
        return result
    initial_request = InvestigationRequest(chain_id=1, address="0x1a9c8182c09f50c8318d769245bea52c32be35bc",
        token_address="0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", from_block=24106368, to_block=24106388)
    backend = InvestigationTools(CachedRpc(snapshot), initial_request, sources)
    scope = AgentRequest(tx_hash="0x091f0083242a777d55821c1189e568d6d033d9da501b75087dc736fa143d2c1e",
        token_address=initial_request.token_address, from_block=24106368, to_block=24106388,
        finalized_block=int(snapshot["finalized"]["number"], 16),
        initial_observations=[{"alert": "Large outgoing ERC-20 transfer requires event explanation."}],
        governance_sources={source["source_id"]: source["url"] for source in sources})
    outgoing, incoming = [], []
    reasoning_digests = set()
    async def inspect_request(http_request):
        if http_request.url.path.endswith("/chat/completions"):
            body = json.loads(http_request.content)
            assistant_messages = [item for item in body.get("messages", []) if item.get("role") == "assistant"]
            carried = [item.get("reasoning_content") for item in assistant_messages if item.get("reasoning_content")]
            outgoing.append({"model": body.get("model"), "stream": body.get("stream"),
                "provider_locked": body.get("provider") == {"only": ["agentuniverse"], "allow_fallbacks": False},
                "tools_available": [tool.get("function", {}).get("name") for tool in body.get("tools", [])],
                "reasoning_content_sent": bool(carried),
                "reasoning_content_matches_previous_response": bool(carried) and all(
                    hashlib.sha256(item.encode("utf-8")).hexdigest() in reasoning_digests for item in carried)})
    async def inspect_response(http_response):
        if http_response.request.url.path.endswith("/chat/completions"):
            await http_response.aread()
            safe = {"http_status": http_response.status_code}
            if http_response.status_code == 200:
                payload = http_response.json()
                choice = payload.get("choices", [{}])[0]
                message = choice.get("message", {})
                reasoning = message.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    reasoning_digests.add(hashlib.sha256(reasoning.encode("utf-8")).hexdigest())
                safe.update(reasoning_content_received=bool(reasoning),
                            tools=[call.get("function", {}).get("name") for call in message.get("tool_calls", [])],
                            finish_reason=choice.get("finish_reason"))
            incoming.append(safe)
    async with httpx.AsyncClient(timeout=60, follow_redirects=False,
        event_hooks={"request": [inspect_request], "response": [inspect_response]}) as inference_client:
        primary = create_gateway_model(api_key, http_client=inference_client, thinking=True)
        fallback = create_gateway_model(api_key, model_name="minimax-m2.7", http_client=inference_client) if allow_fallback else None
        outcome = await run_investigation(primary, backend, scope, limits=RuntimeLimits(max_output_tokens=2048),
            ledger=ledger, price_quotes=quotes, fallback_model=fallback, paid_enabled=True)
    rounds = [entry for entry in incoming if any(name != "final_report" for name in entry.get("tools", []))]
    choices = [entry for entry in outcome.trace if entry.get("event") == "tool_selected"]
    result.update(status=outcome.status, stop_reason=outcome.stop_reason,
        paid_model_requests=outcome.model_requests, tool_attempts=outcome.tool_attempts,
        model_names=outcome.model_names, tool_call_rounds=len(rounds), tool_choices=choices,
        request_protocol_checks=outgoing, response_protocol_checks=incoming,
        reasoning_content_roundtrip_verified=any(entry["reasoning_content_matches_previous_response"] for entry in outgoing),
        governance_selected_after_chain_observation=any(entry["tool"] == "get_governance_source" for entry in choices)
            and bool(choices) and choices[0]["tool"] in {"get_receipt", "get_transaction"},
        acceptance_pass=outcome.status == "completed" and len(rounds) >= 2,
        report=outcome.report.model_dump(mode="json") if outcome.report else None,
        evidence_ids=[item.evidence_id for item in outcome.evidence], budget_after=ledger.snapshot())
    result["tool_trace"] = outcome.trace
    result["public_tool_evidence"] = [item.model_dump(mode="json") for item in outcome.evidence]
    result["public_rpc_observations"] = backend.observations
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", action="store_true", help="Fetch free public official route prices")
    parser.add_argument("--live", action="store_true", help="Run explicitly authorized real-model bounded acceptance")
    parser.add_argument("--output", help="Write safe acceptance metadata to a local JSON path")
    parser.add_argument("--no-fallback", action="store_true", help="Use only the primary DeepSeek route in live acceptance")
    args = parser.parse_args()
    if not args.prices and not args.live:
        parser.print_help()
        return
    result = asyncio.run(live_acceptance(allow_fallback=not args.no_fallback) if args.live else public_prices())
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
