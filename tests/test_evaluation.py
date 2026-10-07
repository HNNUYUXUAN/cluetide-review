import pytest

from cluetide.agent import AgentRequest
from cluetide.evaluation import conditional_followup

TX = "0x" + "a" * 64
TOKEN = "0x" + "b" * 40


class PublicBackend:
    def __init__(self, *, fail_state=False):
        self.fail_state = fail_state
        self.calls = []

    async def get_receipt(self, tx_hash):
        self.calls.append("receipt")
        return {"evidence_id": "receipt:" + tx_hash, "kind": "receipt", "status": "ok",
                "payload": {"blockNumber": "0xb", "status": "0x1"}}

    async def get_transaction(self, tx_hash):
        self.calls.append("transaction")
        return {"evidence_id": "transaction:" + tx_hash, "kind": "transaction", "status": "ok", "payload": {}}

    async def get_token_state(self, token_address, block_number):
        self.calls.append("state")
        if self.fail_state:
            raise RuntimeError("public test failure")
        return {"evidence_id": f"state:{token_address}:{block_number}", "kind": "token_state", "status": "ok",
                "payload": {"block_number": block_number, "total_supply_raw": "100000000000000000000000000000000000001"}}

    async def get_governance_source(self, source_id):
        self.calls.append("context")
        return {"evidence_id": "source:" + source_id, "kind": "public_context_source", "status": "ok",
                "payload": {"source_id": source_id, "category": "incident_postmortem"}}


def scope():
    return AgentRequest(tx_hash=TX, token_address=TOKEN, from_block=10, to_block=12, finalized_block=20,
        initial_observations=[
            {"evidence_id": "transfer:1", "kind": "transfer", "payload": {"value_raw": "1000"}},
            {"evidence_id": "collection:coverage", "kind": "collection_coverage", "payload": {"status": "complete"}},
        ], governance_sources={str(i): "https://example.com/source" for i in range(5)})


@pytest.mark.asyncio
async def test_conditional_baseline_retains_failed_reads_and_spends_same_eight_attempts():
    backend = PublicBackend(fail_state=True)
    result = await conditional_followup(backend, scope())
    assert result["tool_attempts"] == 8
    assert backend.calls == ["receipt", "transaction", "state", "state", "context", "context", "context", "context"]
    assert sum(item["status"] == "error" for item in result["evidence"]) == 2
    supply = next(item for item in result["report"]["assessments"] if item["explanation_id"] == "supply_decrease")
    assert supply["status"] == "unknown" and supply["unknowns"]
    assert result["report"]["classification"] == "needs_review"


@pytest.mark.asyncio
async def test_conditional_baseline_compares_exact_large_supply_integers():
    result = await conditional_followup(PublicBackend(), scope())
    supply = next(item for item in result["report"]["assessments"] if item["explanation_id"] == "supply_decrease")
    assert supply["status"] == "refuted"
    assert len(supply["counter_evidence_ids"]) == 2
    assert result["model_requests"] == 0


@pytest.mark.asyncio
async def test_conditional_read_budget_cannot_expand():
    with pytest.raises(ValueError):
        await conditional_followup(PublicBackend(), scope(), max_tools=9)


@pytest.mark.asyncio
async def test_repeated_comparison_counts_shared_preparation_once_and_preserves_checkpoints(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from decimal import Decimal
    from cluetide import evaluation, live, settings
    from cluetide.agent import AgentOutcome
    from cluetide.budget import BudgetLedger, PriceQuote

    async def bounded_outcome(*args, **kwargs):
        return AgentOutcome(status="stopped", tool_attempts=1,
                            stop_reason="synthetic_read_rejection")

    monkeypatch.setattr(live, "paid_session_available", lambda: True)
    monkeypatch.setattr(settings, "gateway_key", lambda: "synthetic-test")
    monkeypatch.setattr(evaluation, "create_gateway_model", lambda *args, **kwargs: object())
    monkeypatch.setattr(evaluation, "run_investigation", bounded_outcome)
    quote = PriceQuote("deepseek-v3.2", Decimal("1.6"), Decimal("2.4"),
        "https://tokendance.space/portal/api/models/deepseek-v3.2/endpoints/stats",
        datetime.now(timezone.utc), verified=True, source_sha256="a" * 64)
    checkpoints = []
    result = await evaluation.run_comparison("uniswap93", variant="complete", repeat=2,
        ledger=BudgetLedger(tmp_path / "budget.sqlite3"), quote=quote,
        checkpoint=checkpoints.append)
    assert [len(item["runs"]) for item in checkpoints] == [1, 2, 3, 4]
    assert result["conditional_baseline"]["tool_attempts"] == 8
    assert result["actual_investigation_read_attempts"] == 12
    one_shots = [item for item in result["runs"] if item["strategy"] == "one_shot"]
    assert all(item["end_to_end_read_attempts"] == 9 and item["precollection_reused"] for item in one_shots)
    assert all(item["end_to_end_accounting_scope"] == "standalone_attributed_preparation" for item in one_shots)
