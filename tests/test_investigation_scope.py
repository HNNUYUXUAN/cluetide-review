from copy import deepcopy

import pytest

from cluetide.investigation_scope import select_investigation_transaction
from cluetide.schemas import Coverage, EvidenceSet, InvestigationRequest, MAX_UINT256


ADDRESS = "0x" + "a" * 40
TOKEN = "0x" + "b" * 40
OTHER = "0x" + "c" * 40
BLOCK_HASH = "0x" + "1" * 64
SMALL_TX = "0x" + "2" * 64
LARGE_TX = "0x" + "3" * 64
OTHER_TX = "0x" + "4" * 64


@pytest.fixture
def transfer_record():
    def make(evidence_id, amount, transaction_hash, *, log_index=0):
        return {
            "evidence_id": evidence_id,
            "chain_id": 1,
            "token_address": TOKEN,
            "block_number": 100,
            "block_hash": BLOCK_HASH,
            "transaction_hash": transaction_hash,
            "log_index": log_index,
            "from_address": ADDRESS,
            "to_address": OTHER,
            "value_raw": str(amount),
            "directions": ["out"],
        }
    return make


@pytest.fixture
def alert_record():
    def make(alert_id, *evidence_ids, threshold_raw="100", observed_value_raw="100"):
        return {
            "alert_id": alert_id,
            "threshold_raw": threshold_raw,
            "observed_value_raw": observed_value_raw,
            "evidence_ids": list(evidence_ids),
            "explanation": "原始金额达到配置的工程阈值。",
        }
    return make


def test_largest_alerted_transfer_is_selected_after_an_earlier_small_transfer(transfer_record, alert_record):
    payload = {
        "transfers": [transfer_record("transfer:small", 1, SMALL_TX),
                      transfer_record("transfer:large", 10**25, LARGE_TX)],
        "alerts": [alert_record("alert:large", "transfer:large")],
    }

    selected, scope = select_investigation_transaction(payload)

    assert selected == LARGE_TX
    assert scope["schema_version"] == "cluetide-investigation-scope/v1"
    assert scope["selected_transaction_hash"] == LARGE_TX
    assert scope["selection_basis"] == "largest_alerted_transfer"
    assert scope["selected_evidence_ids"] == ["transfer:large"]
    assert scope["selected_alert_ids"] == ["alert:large"]
    assert scope["observed_transaction_hashes"] == [SMALL_TX, LARGE_TX]
    assert scope["alerted_transaction_hashes"] == [LARGE_TX]
    assert scope["investigated_transaction_hash"] is None
    assert scope["uninvestigated_transaction_hashes"] == [SMALL_TX, LARGE_TX]
    assert "工程规则" in scope["selection_reason"]
    assert "代表交易" in scope["statement"]
    assert "确定性采集证据" in scope["statement"]


def test_alert_references_define_candidates_and_transfer_amounts_define_rank(transfer_record, alert_record):
    payload = {
        "transfers": [transfer_record("transfer:huge", MAX_UINT256, OTHER_TX),
                      transfer_record("transfer:medium", 101, LARGE_TX),
                      transfer_record("transfer:small", 100, SMALL_TX)],
        "alerts": [alert_record("alert:small", "transfer:small", observed_value_raw=str(MAX_UINT256)),
                   alert_record("alert:medium", "transfer:medium", observed_value_raw="101")],
    }

    selected, scope = select_investigation_transaction(payload)

    assert selected == LARGE_TX
    assert scope["alerted_transaction_hashes"] == [SMALL_TX, LARGE_TX]
    assert scope["selected_alert_ids"] == ["alert:medium"]


@pytest.mark.parametrize("alerted", [False, True])
def test_equal_amounts_use_evidence_id_order_independent_of_input_order(transfer_record, alert_record, alerted):
    records = [transfer_record("transfer:z", MAX_UINT256, SMALL_TX),
               transfer_record("transfer:a", MAX_UINT256, LARGE_TX)]
    alerts = [alert_record("alert:both", "transfer:z", "transfer:a")] if alerted else []

    result = select_investigation_transaction({"transfers": records, "alerts": alerts})
    reversed_result = select_investigation_transaction({"transfers": list(reversed(records)), "alerts": alerts})

    assert result == reversed_result
    assert result[0] == LARGE_TX
    assert result[1]["selected_evidence_ids"] == ["transfer:a"]


def test_multiple_events_in_a_transaction_share_one_scope_and_all_related_alerts(transfer_record, alert_record):
    payload = {
        "transfers": [transfer_record("transfer:z", 50, LARGE_TX, log_index=1),
                      transfer_record("transfer:a", 200, LARGE_TX),
                      transfer_record("transfer:other", 100, OTHER_TX)],
        "alerts": [alert_record("alert:z", "transfer:z"),
                   alert_record("alert:a", "transfer:a", "transfer:z", "transfer:a"),
                   alert_record("alert:a", "transfer:a"),
                   alert_record("alert:other", "transfer:other")],
    }

    selected, scope = select_investigation_transaction(payload)

    assert selected == LARGE_TX
    assert scope["observed_transaction_hashes"] == [LARGE_TX, OTHER_TX]
    assert scope["alerted_transaction_hashes"] == [LARGE_TX, OTHER_TX]
    assert scope["selected_evidence_ids"] == ["transfer:a", "transfer:z"]
    assert scope["selected_alert_ids"] == ["alert:a", "alert:z"]
    assert scope["uninvestigated_transaction_hashes"] == [LARGE_TX, OTHER_TX]


def test_largest_observed_event_uses_exact_uint256_amounts(transfer_record):
    payload = {"transfers": [transfer_record("transfer:a", MAX_UINT256 - 1, SMALL_TX),
                             transfer_record("transfer:z", MAX_UINT256, LARGE_TX)]}

    selected, scope = select_investigation_transaction(payload)

    assert selected == LARGE_TX
    assert scope["selection_basis"] == "largest_observed_transfer"
    assert scope["selected_alert_ids"] == []
    assert scope["alerted_transaction_hashes"] == []


def test_representative_ranking_uses_individual_transfer_amounts(transfer_record, alert_record):
    payload = {
        "transfers": [transfer_record("transfer:first", 150, SMALL_TX),
                      transfer_record("transfer:second", 150, SMALL_TX, log_index=1),
                      transfer_record("transfer:largest", 200, LARGE_TX)],
        "alerts": [alert_record("alert:all", "transfer:first", "transfer:second", "transfer:largest")],
    }

    selected, scope = select_investigation_transaction(payload)

    assert selected == LARGE_TX
    assert scope["selected_evidence_ids"] == ["transfer:largest"]


def test_alerts_with_external_references_preserve_observed_fallback(transfer_record, alert_record):
    payload = {
        "transfers": [transfer_record("transfer:present", 0, SMALL_TX)],
        "alerts": [alert_record("alert:external", "transfer:external")],
    }

    selected, scope = select_investigation_transaction(payload)

    assert selected == SMALL_TX
    assert scope["selection_basis"] == "largest_observed_transfer"
    assert scope["selected_alert_ids"] == []


@pytest.mark.parametrize("payload", [{}, {"transfers": [], "alerts": []}, {
    "transfers": [], "alerts": [{"alert_id": "alert:external", "evidence_ids": ["transfer:external"]}],
}])
def test_empty_transfer_window_has_a_complete_empty_scope(payload):
    selected, scope = select_investigation_transaction(payload)

    assert selected is scope["selected_transaction_hash"] is None
    assert scope["investigated_transaction_hash"] is None
    assert scope["selection_basis"] == "no_transfer"
    for field in ("observed_transaction_hashes", "alerted_transaction_hashes",
                  "uninvestigated_transaction_hashes", "selected_evidence_ids", "selected_alert_ids"):
        assert scope[field] == []


def test_model_and_dictionary_inputs_produce_the_same_scope(transfer_record, alert_record):
    evidence = EvidenceSet(
        request=InvestigationRequest(address=ADDRESS, token_address=TOKEN, from_block=100, to_block=100),
        coverage=Coverage(status="complete", requested_from_block=100, requested_to_block=100),
        transfers=[transfer_record("transfer:selected", 100, SMALL_TX)],
        alerts=[alert_record("alert:selected", "transfer:selected")],
    )

    assert select_investigation_transaction(evidence) == select_investigation_transaction(evidence.model_dump(mode="json"))


def test_selection_preserves_input_and_separates_returned_lists(transfer_record, alert_record):
    payload = {
        "transfers": [transfer_record("transfer:selected", 100, SMALL_TX)],
        "alerts": [alert_record("alert:selected", "transfer:selected")],
    }
    original = deepcopy(payload)

    _, scope = select_investigation_transaction(payload)
    scope["uninvestigated_transaction_hashes"].clear()

    assert payload == original
    assert scope["observed_transaction_hashes"] == [SMALL_TX]


@pytest.mark.parametrize("value", [0, 1.0, True, None, "", "-1", "+1", "01", "1.0", "1e2", " 1", "١", str(MAX_UINT256 + 1)])
def test_dictionary_inputs_require_canonical_uint256_strings(transfer_record, value):
    record = transfer_record("transfer:selected", 100, SMALL_TX)
    record["value_raw"] = value

    with pytest.raises(ValueError):
        select_investigation_transaction({"transfers": [record]})


def test_conflicting_evidence_ids_have_one_consistent_identity(transfer_record):
    payload = {"transfers": [transfer_record("transfer:one", 100, SMALL_TX),
                             transfer_record("transfer:one", 200, LARGE_TX)]}

    with pytest.raises(ValueError, match="one transaction and raw amount"):
        select_investigation_transaction(payload)


def test_transaction_hashes_use_canonical_unique_order(transfer_record):
    lower = "0x" + "a" * 64
    payload = {"transfers": [transfer_record("transfer:z", 1, lower.upper().replace("0X", "0x")),
                             transfer_record("transfer:a", 2, lower),
                             transfer_record("transfer:other", 3, SMALL_TX)]}

    selected, scope = select_investigation_transaction(payload)

    assert selected == SMALL_TX
    assert scope["observed_transaction_hashes"] == sorted([lower, SMALL_TX])
