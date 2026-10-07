"""The comparison baseline uses the same scoped supply predicate as reports."""
import pytest

from cluetide.agent import AgentRequest, Conclusion, validate_assessment_bindings
from cluetide.evaluation import conditional_followup

TX = "0x" + "a" * 64
TOKEN = "0x" + "b" * 40
OTHER_TOKEN = "0x" + "c" * 40


class SupplyBackend:
    def __init__(self, before, after, *, token=TOKEN, event_block=11, malformed=False):
        self.before, self.after = before, after
        self.token, self.event_block = token, event_block
        self.malformed = malformed
        self.state_blocks = []

    async def get_receipt(self, tx_hash):
        return {"evidence_id": "receipt:" + tx_hash, "kind": "receipt", "status": "ok",
                "payload": {"blockNumber": hex(self.event_block), "status": "0x1"}}

    async def get_transaction(self, tx_hash):
        return {"evidence_id": "transaction:" + tx_hash, "kind": "transaction", "status": "ok", "payload": {}}

    async def get_token_state(self, token_address, block_number):
        self.state_blocks.append(block_number)
        raw = self.before if block_number == 10 else self.after
        return {"evidence_id": f"state:{self.token}:{block_number}", "kind": "token_state", "status": "ok",
                "payload": {"block_number": block_number, **({} if self.malformed else {"total_supply_raw": raw})}}


def request():
    return AgentRequest(tx_hash=TX, token_address=TOKEN, from_block=10, to_block=12, finalized_block=20,
                        initial_observations=[{"evidence_id": "transfer:1", "kind": "transfer",
                                               "payload": {"value_raw": "1000"}}])


@pytest.mark.asyncio
@pytest.mark.parametrize(("before", "after", "expected"), [
    (str(10**70), str(10**70 - 1), "supported"),
    (str(10**70), str(10**70 + 1), "refuted"),
    (str(10**70), str(10**70), "refuted"),
])
async def test_baseline_uses_exact_net_direction_and_shared_report_rule(before, after, expected):
    result = await conditional_followup(SupplyBackend(before, after), request())
    assessment = result["report"]["assessments"][1]
    assert assessment["status"] == expected
    direction = "support_evidence_ids" if expected == "supported" else "counter_evidence_ids"
    assert len(assessment[direction]) == 2
    validate_assessment_bindings(Conclusion.model_validate(result["report"]), result["evidence"], request=request())
    assert result["model_requests"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    {"token": OTHER_TOKEN}, {"malformed": True},
])
async def test_equal_unusable_getters_cannot_refute_supply_decrease(options):
    result = await conditional_followup(SupplyBackend("100", "100", **options), request())
    assessment = result["report"]["assessments"][1]
    assert assessment["status"] == "unknown"
    assert assessment["counter_evidence_ids"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["-1", "01", "0x10", str(2**256)])
async def test_equal_invalid_raw_values_are_not_a_supply_comparison(raw):
    result = await conditional_followup(SupplyBackend(raw, raw), request())
    assert result["report"]["assessments"][1]["status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("event_block", [1, 30])
async def test_outside_receipt_does_not_send_historical_reads_outside_scope(event_block):
    backend = SupplyBackend("100", "100", event_block=event_block)
    result = await conditional_followup(backend, request())
    assert backend.state_blocks == []
    assert result["report"]["assessments"][1]["status"] == "unknown"


@pytest.mark.asyncio
async def test_single_endpoint_keeps_supply_unknown():
    backend = SupplyBackend("100", "100", event_block=10)
    result = await conditional_followup(backend, request())
    assert backend.state_blocks == [10]
    assert result["report"]["assessments"][1]["status"] == "unknown"
