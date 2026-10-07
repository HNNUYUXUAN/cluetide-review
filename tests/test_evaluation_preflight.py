"""CLI paid admission requires both the account read and its local balance gate."""
import importlib.util
import sys
from pathlib import Path

import pytest

from cluetide.budget import BudgetLedger
from cluetide import evaluation


def load_script():
    script = Path(__file__).resolve().parents[1] / "scripts" / "gcc_evaluate.py"
    spec = importlib.util.spec_from_file_location("gcc_preflight_gate_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checkpoint_replace_failure_preserves_completed_record(monkeypatch, tmp_path):
    module = load_script()
    target = tmp_path / "result.json"
    prior = b'{"completed":1}\n'
    target.write_bytes(prior)

    def replacement_unavailable(*args):
        raise OSError("Synthetic checkpoint replacement unavailable")

    monkeypatch.setattr(module.os, "replace", replacement_unavailable)
    with pytest.raises(OSError):
        module.write_json(target, {"completed": 2})
    assert target.read_bytes() == prior
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_cli_stops_when_spendable_ledger_cannot_be_constructed(monkeypatch, tmp_path, capsys):
    module = load_script()
    model_runs = []

    def ledger(path, **kwargs):
        if "spendable_micro_rmb" in kwargs:
            raise OSError("Synthetic local balance gate unavailable")
        return BudgetLedger(path, **kwargs)

    async def account_read(*args):
        return {"balance_micro_rmb": 1_000_000}

    async def unavailable_quote(*args):
        raise RuntimeError("Synthetic quote unavailable")

    async def model_run(*args, **kwargs):
        model_runs.append(True)
        raise AssertionError("Paid inference requires the constructed balance gate")

    monkeypatch.setattr(module, "LOCAL_DATA", tmp_path)
    monkeypatch.setattr(module, "BudgetLedger", ledger)
    monkeypatch.setattr(module, "gateway_key", lambda: "synthetic-test")
    monkeypatch.setattr(module, "read_gateway_balance", account_read)
    monkeypatch.setattr(module, "fetch_official_price_quote", unavailable_quote)
    monkeypatch.setattr(evaluation, "run_comparison", model_run)
    monkeypatch.setattr(sys, "argv", [module.__file__, "--run", "--output", str(tmp_path / "result.json")])
    await module.main()
    assert not model_runs
    assert '"status": "balance_unavailable"' in capsys.readouterr().out
