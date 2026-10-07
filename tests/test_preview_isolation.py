"""Offline previews preserve historical accounting without opening spending ledgers."""
import json
import pytest
from cluetide import live


def test_preview_has_no_paid_session_or_new_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("CLUETIDE_PREVIEW_ONLY", "1")
    monkeypatch.setattr(live, "LOCAL_DATA", tmp_path)
    monkeypatch.setattr(live, "gateway_key", lambda: "synthetic")
    (tmp_path / "paid-session.json").write_text('{"enabled":true,"expires_at_utc":"2999-01-01T00:00:00+00:00"}')
    (tmp_path / "budget-history.json").write_text(json.dumps({"requests_reserved": 60, "reserved_rmb": "9.586344", "cap_rmb": "10", "private_field": "discard"}))
    assert not live.paid_session_available()
    result = live.budget_snapshot()
    assert result["execution_enabled"] is False
    assert result["historical_budget"]["requests_reserved"] == 60
    assert "private_field" not in result["historical_budget"]
    assert not (tmp_path / "model-budget.sqlite3").exists()


def test_preview_without_history_does_not_grant_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("CLUETIDE_PREVIEW_ONLY", "1")
    monkeypatch.setattr(live, "LOCAL_DATA", tmp_path)
    assert live.budget_snapshot()["historical_budget"] is None
    assert not (tmp_path / "model-budget.sqlite3").exists()


@pytest.mark.parametrize("history", [[], None, "history", 1, True])
def test_preview_ignores_malformed_history_without_opening_budget(monkeypatch, tmp_path, history):
    monkeypatch.setenv("CLUETIDE_PREVIEW_ONLY", "1")
    monkeypatch.setattr(live, "LOCAL_DATA", tmp_path)
    (tmp_path / "budget-history.json").write_text(json.dumps(history), encoding="utf-8")
    assert live.budget_snapshot()["historical_budget"] is None
    assert not (tmp_path / "model-budget.sqlite3").exists()


@pytest.mark.asyncio
async def test_preview_paid_execution_stops_before_network_and_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("CLUETIDE_PREVIEW_ONLY", "1")
    monkeypatch.setattr(live, "LOCAL_DATA", tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("Preview must not initialize paid resources")
    monkeypatch.setattr(live, "BudgetLedger", forbidden)
    monkeypatch.setattr(live, "gateway_key", forbidden)
    result = await live.run_paid(object(), object(), stop_event=None, deadline_seconds=1)
    assert result.stop_reason == "paid_session_unavailable"
    assert result.model_requests == 0
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("script", ["gcc_evaluate", "probe_gateway"])
async def test_preview_cli_stops_before_budget_and_network(monkeypatch, tmp_path, script):
    import importlib.util
    from pathlib import Path
    from cluetide import budget
    monkeypatch.setenv("CLUETIDE_PREVIEW_ONLY", "1")
    monkeypatch.setattr(live, "LOCAL_DATA", tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("Preview CLI must not create budget or network resources")
    monkeypatch.setattr(budget, "BudgetLedger", forbidden)
    monkeypatch.setattr(budget, "read_gateway_balance", forbidden)
    monkeypatch.setattr(budget, "fetch_official_price_quote", forbidden)
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{script}.py"
    spec = importlib.util.spec_from_file_location(f"preview_{script}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if script == "gcc_evaluate":
        result, ledger, quotes = await module.preflight()
        assert result["status"] == "offline_preview"
        assert ledger is None and quotes == {}
    else:
        result = await module.live_acceptance()
        assert result["reason"] == "paid_session_unavailable"
        assert result["paid_model_requests"] == 0
        assert (await module.public_prices())["network_reads"] == 0
    assert not list(tmp_path.iterdir())
