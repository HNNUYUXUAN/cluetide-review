import pytest

from cluetide.report_quality import report_quality


@pytest.mark.parametrize("status,flagged", [("supported", True), ("refuted", False), ("unknown", False)])
def test_wording_checks_follow_the_candidate_judgment(status, flagged):
    report = {"summary": "有限窗口待复核", "claims": [],
              "assessments": [{"status": status, "explanation": "该事件不是攻击。"}]}
    quality = report_quality(report)
    assert (quality["status"] == "needs_review") is flagged
    if flagged:
        assert quality["flags"][0]["locations"] == ["assessments[0].explanation"]


def test_supply_getter_observation_is_distinct_from_contract_wide_implementation_inference():
    bounded = report_quality({"summary": "两个区块末 totalSupply getter 相等。", "claims": []})
    generalized = report_quality({"summary": "两个读数相等，表明该代币合约未实现销毁机制。", "claims": []})
    assert bounded["status"] == "no_flags_detected"
    assert generalized["flags"][0]["code"] == "contract_burn_generalization"
