"""Bounded public-case comparisons using the project's existing model ledger.

The default preflight makes read-only account and price requests, not inference.
Paid runs require --run and the existing local session authorization. Public
results contain case evidence and safe protocol metadata, never credentials.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from cluetide.budget import HARD_CAP_RMB, MICRO_RMB, BudgetLedger, fetch_official_price_quote, read_gateway_balance
from cluetide.settings import LOCAL_DATA, ROOT, gateway_key, preview_only


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary_path = Path(stream.name)
            stream.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


async def preflight() -> tuple[dict, BudgetLedger | None, dict]:
    if preview_only():
        from cluetide.live import budget_snapshot
        return {"observed_at": datetime.now(timezone.utc).isoformat(), "status": "offline_preview",
                "budget": budget_snapshot(), "inference_requests": 0, "network_reads": 0,
                "account_balance_verified": False, "quotes": {}}, None, {}
    ledger = BudgetLedger(LOCAL_DATA / "model-budget.sqlite3")
    result = {"observed_at": datetime.now(timezone.utc).isoformat(),
              "budget": ledger.snapshot(), "inference_requests": 0,
              "account_balance_verified": False, "quotes": {}}
    quotes = {}
    balance_baseline = ledger.balance_reservation_baseline()
    try:
        balance = await read_gateway_balance(gateway_key())
        available = balance["balance_micro_rmb"]
        if available > 0:
            ledger = BudgetLedger(LOCAL_DATA / "model-budget.sqlite3",
                                 cap_rmb=HARD_CAP_RMB, spendable_micro_rmb=available,
                                 spendable_at_reserved_micro_rmb=balance_baseline["reserved_micro_rmb"],
                                 spendable_pending_micro_rmb=balance_baseline["pending_micro_rmb"])
        result.update(account_balance_verified=True, balance_positive=available > 0,
                      balance_covers_remaining_cap=available >= int(
                          Decimal(ledger.snapshot()["remaining_rmb"]) * MICRO_RMB))
    except Exception:
        result["balance_status"] = "read_unavailable"
    for name in ("deepseek-v3.2", "minimax-m2.7"):
        try:
            quote = await fetch_official_price_quote(name)
            quotes[name] = quote
            result["quotes"][name] = {
                "verified": True, "provider": quote.provider_slug,
                "input_rmb_per_million": str(quote.input_rmb_per_million),
                "output_rmb_per_million": str(quote.output_rmb_per_million),
                "source_url": quote.source_url, "source_sha256": quote.source_sha256,
                "verified_at": quote.verified_at.isoformat(),
            }
        except Exception:
            result["quotes"][name] = {"verified": False}
    result["budget"] = ledger.snapshot()
    return result, ledger, quotes


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--case", default="uniswap93")
    parser.add_argument("--variant", choices=["complete", "missing_context"], default="complete")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path, default=LOCAL_DATA / "gcc-preflight.json")
    args = parser.parse_args()
    result, ledger, quotes = await preflight()
    write_json(args.output, result)
    if result.get("status") == "offline_preview":
        print(json.dumps({"path": str(args.output), "status": "offline_preview", "inference_requests": 0, "network_reads": 0}))
        return
    if not args.run:
        print(json.dumps({"path": str(args.output), "result": result}, ensure_ascii=False))
        return
    # The runner implementation follows the verified public-case catalog.
    from cluetide.evaluation import run_comparison
    if not result.get("account_balance_verified") or not result.get("balance_positive"):
        print(json.dumps({"path": str(args.output), "status": "balance_unavailable"}))
        return
    if "deepseek-v3.2" not in quotes:
        print(json.dumps({"path": str(args.output), "status": "price_unavailable"}))
        return
    def checkpoint(value):
        value["preflight"] = result
        value["checkpoint"] = "completed_strategy_recorded"
        write_json(args.output, value)
    comparison = await run_comparison(args.case, variant=args.variant, repeat=args.repeat,
                                      ledger=ledger, quote=quotes["deepseek-v3.2"], checkpoint=checkpoint)
    comparison["preflight"] = result
    write_json(args.output, comparison)
    print(json.dumps({"path": str(args.output), "status": comparison["status"],
                      "budget": ledger.snapshot(), "runs": len(comparison.get("runs", []))}))


if __name__ == "__main__":
    asyncio.run(main())
