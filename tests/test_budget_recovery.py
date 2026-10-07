"""Actual SQLite recovery and contention across separate Windows processes."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
import subprocess
import sys

from cluetide.budget import BudgetLedger


WORKER = r'''
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from cluetide.budget import BudgetExhausted, BudgetLedger, PriceQuote

ledger = BudgetLedger(sys.argv[1], cap_rmb=Decimal(sys.argv[2]),
                      spendable_micro_rmb=int(sys.argv[4]) if len(sys.argv) > 4 else None)
quote = PriceQuote("deepseek-v3.2", Decimal("1.6"), Decimal("2.4"),
                   "https://tokendance.space/portal/api/models/deepseek-v3.2/endpoints/stats",
                   datetime.now(timezone.utc), verified=True, source_sha256="a" * 64)
try:
    ledger.reserve(quote, input_tokens=1000, output_tokens=1024)
    print("reserved", flush=True)
except BudgetExhausted:
    print("rejected", flush=True)
if sys.argv[3] == "abrupt":
    os._exit(23)
'''


def run_worker(path: Path, *, mode="normal", cap="0.02", spendable_micro_rmb=None):
    # Execute only this test's Python child process, with no shell or network.
    arguments = [sys.executable, "-c", WORKER, str(path), cap, mode]
    if spendable_micro_rmb is not None:
        arguments.append(str(spendable_micro_rmb))
    return subprocess.run(arguments,
                          capture_output=True, text=True, timeout=30, check=False)


def test_abrupt_process_exit_preserves_committed_reservation(tmp_path):
    path = tmp_path / "budget.sqlite3"
    worker = run_worker(path, mode="abrupt")
    assert worker.returncode == 23 and worker.stdout.strip() == "reserved"
    reopened = BudgetLedger(path)
    state = reopened.snapshot()
    assert state["cap_rmb"] == "0.02"
    assert state["requests_reserved"] == 1
    assert Decimal(state["reserved_rmb"]) > 0
    with reopened._connect() as db:
        assert db.execute("SELECT state FROM reservations").fetchone()[0] == "reserved"


def test_concurrent_processes_cannot_overbook_shared_budget(tmp_path):
    path = tmp_path / "budget.sqlite3"
    BudgetLedger(path, cap_rmb=Decimal("0.02"))
    with ThreadPoolExecutor(max_workers=4) as launchers:
        results = list(launchers.map(lambda _: run_worker(path), range(8)))
    assert all(result.returncode == 0 for result in results)
    assert sum(result.stdout.strip() == "reserved" for result in results) == 2
    assert sum(result.stdout.strip() == "rejected" for result in results) == 6
    state = BudgetLedger(path).snapshot()
    assert state["requests_reserved"] == 2
    assert Decimal(state["reserved_rmb"]) <= Decimal(state["cap_rmb"]) == Decimal("0.02")


def test_concurrent_balance_observations_include_existing_unsettled_requests(tmp_path):
    path = tmp_path / "budget.sqlite3"
    BudgetLedger(path)
    # Each worker observes the same still-uncharged account balance. Two request
    # upper bounds fit; pending requests remain part of later observations.
    spendable = 2 * 8116
    with ThreadPoolExecutor(max_workers=4) as launchers:
        results = list(launchers.map(lambda _: run_worker(
            path, cap="10", spendable_micro_rmb=spendable), range(8)))
    assert all(result.returncode == 0 for result in results)
    assert sum(result.stdout.strip() == "reserved" for result in results) == 2
    assert sum(result.stdout.strip() == "rejected" for result in results) == 6
    state = BudgetLedger(path).snapshot()
    assert state["requests_reserved"] == 2
    assert Decimal(state["reserved_rmb"]) * 1_000_000 <= spendable
