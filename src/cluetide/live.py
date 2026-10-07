"""Session-limited paid execution under one shared conservative budget."""
import asyncio
import json
import time
from collections.abc import Callable
from datetime import datetime, timezone

from .agent import AgentOutcome, RuntimeLimits, create_gateway_model, run_investigation
from .budget import HARD_CAP_RMB, BudgetLedger, fetch_official_price_quote, read_gateway_balance
from .settings import LOCAL_DATA, gateway_key, preview_only


def paid_session_available() -> bool:
    if preview_only():
        return False
    path = LOCAL_DATA / "paid-session.json"
    try:
        decision = json.loads(path.read_text(encoding="utf-8"))
        expiry = datetime.fromisoformat(decision["expires_at_utc"])
        return decision.get("enabled") is True and bool(gateway_key()) and expiry.tzinfo is not None and datetime.now(timezone.utc) < expiry
    except (OSError, ValueError, KeyError, TypeError):
        return False


def budget_snapshot() -> dict:
    if preview_only():
        history = None
        try:
            saved = json.loads((LOCAL_DATA / "budget-history.json").read_text("utf-8"))
            if isinstance(saved, dict):
                history = {key: saved[key] for key in ("requests_reserved", "reserved_rmb", "cap_rmb", "remaining_rmb", "reported_usage_rmb") if type(saved.get(key)) in (int, float, str)}
        except (OSError, ValueError, TypeError):
            pass
        return {"mode": "offline_preview", "execution_enabled": False, "historical_budget": history,
                "scope": "Historical reservations are a reference; this preview grants no spending allowance."}
    return BudgetLedger(LOCAL_DATA / "model-budget.sqlite3").snapshot()


async def run_paid(backend, request, *, stop_event, deadline_seconds: float,
                   outcome_callback: Callable[[AgentOutcome], None] | None = None) -> AgentOutcome:
    import httpx
    # Validate before any account or pricing read. The deadline starts before
    # preflight, rather than giving the model a fresh interval afterward.
    RuntimeLimits(deadline_seconds=deadline_seconds)
    started = time.monotonic()
    captured_outcome = None
    def capture_outcome(value: AgentOutcome) -> None:
        nonlocal captured_outcome
        captured_outcome = value.model_copy(deep=True)
        if outcome_callback is not None:
            try:
                outcome_callback(captured_outcome.model_copy(deep=True))
            except Exception:
                # Observation cannot hide external cancellation or expose
                # arbitrary observer exception diagnostics.
                pass
    def partial_outcome(reason: str) -> AgentOutcome:
        if captured_outcome is not None:
            return captured_outcome.model_copy(update={"status": "partial", "stop_reason": reason})
        return AgentOutcome(status="partial", stop_reason=reason)
    try:
        async with asyncio.timeout(deadline_seconds):
            if stop_event is not None and stop_event.is_set():
                return AgentOutcome(status="stopped", stop_reason="user_stop")
            if not paid_session_available():
                return AgentOutcome(status="partial", stop_reason="paid_session_unavailable")
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                ledger = BudgetLedger(LOCAL_DATA / "model-budget.sqlite3", cap_rmb=HARD_CAP_RMB)
                balance_baseline = ledger.balance_reservation_baseline()
                balance = await read_gateway_balance(gateway_key(), http_client=client)
                quotes = {}
                for name in ("deepseek-v3.2", "minimax-m2.7"):
                    if stop_event is not None and stop_event.is_set():
                        return AgentOutcome(status="stopped", stop_reason="user_stop")
                    quotes[name] = await fetch_official_price_quote(name, http_client=client)
                if balance["balance_micro_rmb"] <= 0:
                    return AgentOutcome(status="budget_exhausted", stop_reason="provider_balance_exhausted")
                if stop_event is not None and stop_event.is_set():
                    return AgentOutcome(status="stopped", stop_reason="user_stop")
                if not paid_session_available():
                    return AgentOutcome(status="partial", stop_reason="paid_session_unavailable")
                ledger = BudgetLedger(LOCAL_DATA / "model-budget.sqlite3", cap_rmb=HARD_CAP_RMB,
                    spendable_micro_rmb=balance["balance_micro_rmb"],
                    spendable_at_reserved_micro_rmb=balance_baseline["reserved_micro_rmb"],
                    spendable_pending_micro_rmb=balance_baseline["pending_micro_rmb"])
                primary = create_gateway_model(gateway_key(), model_name="deepseek-v3.2", http_client=client, thinking=False)
                fallback = create_gateway_model(gateway_key(), model_name="minimax-m2.7", http_client=client)
                remaining = deadline_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    return AgentOutcome(status="partial", stop_reason="deadline_exceeded")
                return await run_investigation(primary, backend, request, limits=RuntimeLimits(deadline_seconds=remaining, max_output_tokens=4096), stop_event=stop_event, ledger=ledger, price_quotes=quotes, fallback_model=fallback, paid_enabled=True, outcome_callback=capture_outcome)
    except TimeoutError:
        return partial_outcome("deadline_exceeded")
    except Exception:
        return partial_outcome("provider_preflight_or_transport_unavailable")
