from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cluetide.budget import HARD_CAP_RMB, BudgetExhausted, BudgetLedger, PriceNotVerified, PriceQuote, price_quote_from_public_payload, read_gateway_balance


def quote(**updates):
    values = dict(model="deepseek-v3.2", input_rmb_per_million=Decimal("1.6"),
                  output_rmb_per_million=Decimal("2.4"),
                  source_url="https://tokendance.space/portal/api/models/deepseek-v3.2/endpoints/stats",
                  verified_at=datetime.now(timezone.utc), verified=True, source_sha256="a" * 64)
    values.update(updates)
    return PriceQuote(**values)


def test_failed_and_crashed_reservations_survive_reopening(tmp_path):
    path = tmp_path / "budget.sqlite3"
    ledger = BudgetLedger(path)
    first = ledger.reserve(quote(), input_tokens=1000, output_tokens=1024)
    ledger.finish(first, quote=quote(), failed=True)
    ledger.reserve(quote(), input_tokens=1000, output_tokens=1024)
    reopened = BudgetLedger(path)
    assert reopened.snapshot()["requests_reserved"] == 2
    assert Decimal(reopened.snapshot()["remaining_rmb"]) < HARD_CAP_RMB
    assert reopened.snapshot()["account_balance_verified"] is False


def test_budget_is_atomic_across_concurrent_reservations(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", cap_rmb=Decimal("0.02"))
    def reserve(_):
        try:
            ledger.reserve(quote(), input_tokens=1000, output_tokens=1024)
            return True
        except BudgetExhausted:
            return False
    with ThreadPoolExecutor(max_workers=6) as executor:
        successes = sum(executor.map(reserve, range(12)))
    assert successes == 2
    assert Decimal(ledger.snapshot()["reserved_rmb"]) <= Decimal("0.02")


@pytest.mark.parametrize("cap", ["-1", "0", "10.01", "NaN", "Infinity"])
def test_cap_is_positive_finite_and_within_authorization(tmp_path, cap):
    with pytest.raises(ValueError):
        BudgetLedger(tmp_path / "budget.sqlite3", cap_rmb=Decimal(cap))


def test_reopening_preserves_authorization_and_retained_reservations(tmp_path):
    path = tmp_path / "budget.sqlite3"
    original = BudgetLedger(path, cap_rmb=Decimal("5"))
    failed = original.reserve(quote(), input_tokens=1000, output_tokens=1024)
    original.finish(failed, quote=quote(), failed=True)
    original.reserve(quote(), input_tokens=1000, output_tokens=1024)
    before = original.snapshot()
    constrained = BudgetLedger(path, cap_rmb=Decimal("0.01"))
    assert constrained.snapshot()["cap_rmb"] == "0.01"
    with pytest.raises(BudgetExhausted):
        constrained.reserve(quote(), input_tokens=1000, output_tokens=1024)
    reopened = BudgetLedger(path, cap_rmb=HARD_CAP_RMB)
    assert reopened.snapshot() == before
    with reopened._connect() as connection:
        assert connection.execute("SELECT cap FROM budget_config").fetchone()[0] == 5_000_000
        assert {row[0] for row in connection.execute("SELECT state FROM reservations")} == {
            "failed_reserved", "reserved"
        }


def test_spendable_balance_covers_completed_history_and_additional_reservations(tmp_path):
    path = tmp_path / "budget.sqlite3"
    original = BudgetLedger(path)
    amount = quote().upper_bound_micro_rmb(1000, 1024)
    completed = original.reserve(quote(), input_tokens=1000, output_tokens=1024)
    original.finish(completed, quote=quote(), input_tokens=100, output_tokens=100)
    bounded = BudgetLedger(path, spendable_micro_rmb=amount)
    bounded.reserve(quote(), input_tokens=1000, output_tokens=1024)
    with pytest.raises(BudgetExhausted):
        bounded.reserve(quote(), input_tokens=1000, output_tokens=1024)
    assert bounded.snapshot()["requests_reserved"] == 2
    assert BudgetLedger(path).snapshot()["cap_rmb"] == str(HARD_CAP_RMB)


@pytest.mark.parametrize("failed", [False, True])
def test_unsettled_reservations_consume_the_next_balance_observation(tmp_path, failed):
    path = tmp_path / "budget.sqlite3"
    original = BudgetLedger(path)
    amount = quote().upper_bound_micro_rmb(1000, 1024)
    unsettled = original.reserve(quote(), input_tokens=1000, output_tokens=1024)
    if failed:
        original.finish(unsettled, quote=quote(), failed=True)
    baseline = original.balance_reservation_baseline()
    assert baseline == {"reserved_micro_rmb": amount, "pending_micro_rmb": amount}
    bounded = BudgetLedger(path, spendable_micro_rmb=amount,
        spendable_at_reserved_micro_rmb=baseline["reserved_micro_rmb"],
        spendable_pending_micro_rmb=baseline["pending_micro_rmb"])
    with pytest.raises(BudgetExhausted):
        bounded.reserve(quote(), input_tokens=1000, output_tokens=1024)
    assert bounded.snapshot()["requests_reserved"] == 1


def test_unsettled_baseline_remains_reserved_after_completion_during_balance_read(tmp_path):
    path = tmp_path / "budget.sqlite3"
    original = BudgetLedger(path)
    amount = quote().upper_bound_micro_rmb(1000, 1024)
    unsettled = original.reserve(quote(), input_tokens=1000, output_tokens=1024)
    baseline = original.balance_reservation_baseline()
    original.finish(unsettled, quote=quote(), input_tokens=100, output_tokens=100)
    bounded = BudgetLedger(path, spendable_micro_rmb=amount,
        spendable_at_reserved_micro_rmb=baseline["reserved_micro_rmb"],
        spendable_pending_micro_rmb=baseline["pending_micro_rmb"])
    with pytest.raises(BudgetExhausted):
        bounded.reserve(quote(), input_tokens=1000, output_tokens=1024)
    assert bounded.snapshot()["requests_reserved"] == 1


def test_balance_gate_accounts_for_parallel_reservations_since_preflight(tmp_path):
    path = tmp_path / "budget.sqlite3"
    original = BudgetLedger(path)
    amount = quote().upper_bound_micro_rmb(1000, 1024)
    completed = original.reserve(quote(), input_tokens=1000, output_tokens=1024)
    original.finish(completed, quote=quote(), input_tokens=100, output_tokens=100)
    baseline = original.balance_reservation_baseline()
    original.reserve(quote(), input_tokens=1000, output_tokens=1024)
    bounded = BudgetLedger(path, spendable_micro_rmb=amount,
                           spendable_at_reserved_micro_rmb=baseline["reserved_micro_rmb"],
                           spendable_pending_micro_rmb=baseline["pending_micro_rmb"])
    with pytest.raises(BudgetExhausted):
        bounded.reserve(quote(), input_tokens=1000, output_tokens=1024)
    assert bounded.snapshot()["requests_reserved"] == 2


def test_balance_gate_accounts_for_increased_historical_usage_settlement(tmp_path):
    path = tmp_path / "budget.sqlite3"
    original = BudgetLedger(path)
    prior = original.reserve(quote(), input_tokens=1000, output_tokens=1024)
    original.finish(prior, quote=quote(), input_tokens=100, output_tokens=100)
    amount = quote().upper_bound_micro_rmb(1000, 1024)
    bounded = BudgetLedger(path, spendable_micro_rmb=amount)
    original.finish(prior, quote=quote(), input_tokens=10000, output_tokens=10000)
    with pytest.raises(BudgetExhausted):
        bounded.reserve(quote(), input_tokens=1000, output_tokens=1024)
    assert bounded.snapshot()["requests_reserved"] == 1
    assert bounded.snapshot()["reserved_rmb"] == "0.04"


def test_concurrent_settlements_preserve_the_largest_retained_usage(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    prior = ledger.reserve(quote(), input_tokens=1000, output_tokens=1024)
    def settle(tokens):
        ledger.finish(prior, quote=quote(), input_tokens=tokens, output_tokens=tokens)
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(settle, [10000, 100, 1000, 10] * 3))
    assert ledger.snapshot()["requests_reserved"] == 1
    assert ledger.snapshot()["reserved_rmb"] == "0.04"


@pytest.mark.parametrize("available", [-1, 1.5, True])
def test_spendable_balance_is_a_nonnegative_integer(tmp_path, available):
    with pytest.raises(ValueError):
        BudgetLedger(tmp_path / "budget.sqlite3", spendable_micro_rmb=available)


@pytest.mark.parametrize("updates", [
    {"verified": False},
    {"source_url": "https://untrusted.example/price"},
    {"source_url": "https://tokendance.space@untrusted.example/price"},
    {"verified_at": datetime.now(timezone.utc) - timedelta(days=2)},
    {"model": "unapproved-model"},
    {"input_rmb_per_million": Decimal("NaN")},
    {"output_rmb_per_million": Decimal("0")},
])
def test_unverified_stale_or_invalid_quote_blocks_paid_reservation(tmp_path, updates):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    with pytest.raises(PriceNotVerified):
        ledger.reserve(quote(**updates), input_tokens=1000, output_tokens=1024)
    assert ledger.snapshot()["requests_reserved"] == 0


def test_success_retains_conservative_upper_bound(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    current = quote()
    reservation = ledger.reserve(current, input_tokens=10000, output_tokens=1024)
    before = ledger.snapshot()["reserved_rmb"]
    ledger.finish(reservation, quote=current, input_tokens=10, output_tokens=10)
    after = ledger.snapshot()
    assert after["reserved_rmb"] == before
    assert Decimal(after["reported_usage_rmb"]) < Decimal(after["reserved_rmb"])


def test_actual_provider_quote_uses_highest_context_tier():
    payload = {"endpoints": [{"slug": "baidu", "supported_protocols": ["openai:chat-completions"],
        "pricing": {"items": [
            {"id": "openai:chat-completions:input_tokens", "plans": [
                {"unit": "millionTokens", "rate": "1.6"}, {"unit": "millionTokens", "rate": "3.2"}]},
            {"id": "openai:chat-completions:completion_tokens", "plans": [
                {"unit": "millionTokens", "rate": "2.4"}, {"unit": "millionTokens", "rate": "4.8"}]}]}}]}
    current = price_quote_from_public_payload(payload, model="deepseek-v3.2", provider_slug="baidu",
        source_url="https://tokendance.space/portal/api/models/deepseek-v3.2/endpoints/stats")
    assert current.input_rmb_per_million == Decimal("3.2")
    assert current.output_rmb_per_million == Decimal("4.8")
    assert len(current.source_sha256) == 64


def test_balance_reader_uses_remaining_balance_not_credits():
    import asyncio
    import httpx
    async def task():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
            json={"balance": {"credits": 100000000, "credits_used": 99000000, "balance": 1000000}}))) as client:
            return await read_gateway_balance("synthetic-test-key", http_client=client)
    result = asyncio.run(task())
    assert result["balance_micro_rmb"] == 1000000
    assert "credits" not in result and "credits_used" not in result
