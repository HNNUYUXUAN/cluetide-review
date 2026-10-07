from __future__ import annotations

import json

import pytest

from cluetide.agent import Conclusion
from cluetide.report_quality import assess_report_quality, deterministic_transfer_facts, report_quality
from cluetide.schemas import MAX_UINT256

TOKEN = "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"
OTHER_TOKEN = "0x" + "ab" * 20


def evidence(raw="100000000000000000000000000", decimals=18):
    return {"request": {"token_address": TOKEN},
            "metadata": {"decimals": decimals, "symbol": "UNI", "observation_ids": ["rpc:decimals", "rpc:symbol"]},
            "transfers": [{"evidence_id": "transfer:uni:93", "token_address": TOKEN, "value_raw": raw}],
            "raw": {"rpc_observations": [
                {"observation_id": "rpc:decimals", "method": "eth_call",
                 "params": [{"to": TOKEN, "data": "0x313ce567"}, "0x100"],
                 "result": "0x" + format(decimals if type(decimals) is int and decimals >= 0 else 0, "064x")},
                {"observation_id": "rpc:symbol", "method": "eth_call",
                 "params": [{"to": TOKEN, "data": "0x95d89b41"}, "0x100"], "result": "0x" + "00" * 32},
            ]}}


def conclusion(summary, claim="治理来源支持执行解释，仍需核对交易语义和证据范围。"):
    return {"summary": summary, "classification": "governance_explained",
            "claims": [{"text": claim, "evidence_ids": ["receipt:93"]}], "limitations": []}


def test_uni_amount_is_exact_code_output_and_input_is_unchanged():
    original = evidence()
    before = json.dumps(original, sort_keys=True)
    assert deterministic_transfer_facts(original) == [{
        "evidence_id": "transfer:uni:93", "token_address": TOKEN,
        "raw_amount": "100000000000000000000000000", "decimals": 18,
        "formatted_amount": "100000000", "symbol": "UNI", "unit": "ERC-20 token units",
        "formatting_status": "formatted", "decimals_observation_ids": ["rpc:decimals"],
    }]
    assert json.dumps(original, sort_keys=True) == before


@pytest.mark.parametrize("raw,decimals,expected", [
    ("0", 18, "0"), ("0", 255, "0"), ("1", 0, "1"), ("1", 18, "0." + "0" * 17 + "1"),
    ("10010", 4, "1.001"), (str(MAX_UINT256), 0, str(MAX_UINT256)),
    (str(MAX_UINT256), 18, str(MAX_UINT256)[:-18] + "." + str(MAX_UINT256)[-18:]),
    ("1", 255, "0." + "0" * 254 + "1"),
])
def test_formatting_uses_all_uint256_digits(raw, decimals, expected):
    assert deterministic_transfer_facts(evidence(raw, decimals))[0]["formatted_amount"] == expected


def test_missing_decimals_is_raw_only_and_other_token_does_not_inherit_uni_decimals():
    original = evidence(decimals=None)
    fact = deterministic_transfer_facts(original)[0]
    assert fact["formatted_amount"] is None and fact["decimals"] is None
    assert fact["unit"] == "ERC-20 base units" and fact["formatting_status"] == "raw_only"
    assert fact["decimals_observation_ids"] == []
    original = evidence()
    original["transfers"][0]["token_address"] = OTHER_TOKEN
    fact = deterministic_transfer_facts(original)[0]
    assert fact["formatted_amount"] is None and fact["decimals"] is None and fact["symbol"] is None


def test_decimals_references_require_matching_successful_raw_read():
    original = evidence()
    assert deterministic_transfer_facts(original)[0]["decimals_observation_ids"] == ["rpc:decimals"]
    original["raw"]["rpc_observations"][0]["error"] = "read failed"
    assert deterministic_transfer_facts(original)[0]["decimals_observation_ids"] == []
    original.pop("raw")
    assert deterministic_transfer_facts(original)[0]["decimals_observation_ids"] == []


def test_decimals_references_match_captured_historical_anchor():
    original = evidence()
    block_hash = "0x" + "12" * 32
    original["metadata"]["block_anchor"] = {"block_hash": block_hash, "number": 256}
    original["raw"]["rpc_observations"][0]["params"][1] = {"blockHash": block_hash, "requireCanonical": True}
    assert deterministic_transfer_facts(original)[0]["decimals_observation_ids"] == ["rpc:decimals"]
    original["raw"]["rpc_observations"][0]["params"][1]["blockHash"] = "0x" + "34" * 32
    assert deterministic_transfer_facts(original)[0]["decimals_observation_ids"] == []


@pytest.mark.parametrize("raw", [1, 1.5, "1e26", "01", str(MAX_UINT256 + 1)])
def test_invalid_raw_amounts_never_become_approximate_facts(raw):
    with pytest.raises(ValueError):
        deterministic_transfer_facts(evidence(raw=raw))


@pytest.mark.parametrize("decimals", [True, 1.5, "18", -1, 256])
def test_invalid_decimals_are_rejected(decimals):
    with pytest.raises(ValueError):
        deterministic_transfer_facts(evidence(decimals=decimals))


def test_original_acceptance_wording_has_both_flags_without_report_mutation():
    # Portable reproduction of the actual retained DS v1 text. No new model call.
    original = {"conclusion": conclusion(
        "事件解释为已批准的治理操作执行，而非攻击或异常活动。",
        "交易日志包含UNI转账事件，金额为100,000,000e18 wei（1亿UNI）。")}
    before = json.dumps(original, ensure_ascii=False, sort_keys=True)
    result = report_quality(original, evidence())
    assert result["status"] == "needs_review" and result["report_modified"] is False
    assert {flag["code"] for flag in result["flags"]} == {
        "erc20_wei_unit", "categorical_attack_exclusion"}
    assert result["flags"][0]["locations"] == ["conclusion.claims[0].text"]
    assert result["flags"][1]["locations"] == ["conclusion.summary"]
    assert json.dumps(original, ensure_ascii=False, sort_keys=True) == before


def test_reviewer_corrected_v2_is_checked_separately_without_certifying_truth():
    corrected = conclusion("观察与治理来源相吻合，支持已批准治理执行的解释；无法据此排除攻击。",
        "UNI Transfer的raw amount为100000000000000000000000000；已读取decimals=18，代码换算为100000000 UNI。")
    result = assess_report_quality(Conclusion.model_validate(corrected), evidence())
    assert result["status"] == "no_flags_detected" and result["flags"] == []
    assert "no factual verification" in result["scope"]


@pytest.mark.parametrize("text", [
    "无法排除攻击。", "不应断言排除攻击。", "不足以证明不是攻击。", "不能认定这不是攻击。",
    "尚未排除攻击。", "攻击无法排除。", "Governance consistency cannot rule out an attack.",
    "This does not rule out attacks.", "We should not claim this was not an attack.",
    "There is no evidence that this was not an attack.", "It is impossible to rule out an attack.",
    "Never rule out an attack from transfer logs.", "治理吻合不代表不存在攻击。",
])
def test_cautious_attack_language_is_not_categorical(text):
    assert report_quality(conclusion(text), evidence())["status"] == "no_flags_detected"


@pytest.mark.parametrize("text", [
    "这不是攻击。", "已排除攻击。", "攻击已被排除。", "确认无攻击。", "没有发生攻击。",
    "The event was not an attack.", "The vote rules out an attack.", "An attack has been ruled out.",
    "An attack is ruled out.", "Cannot verify details, but the vote rules out an attack.",
    "无法判断部分细节，但是治理投票已经排除攻击。",
])
def test_categorical_attack_exclusions_require_review(text):
    assert report_quality(conclusion(text))["flags"][0]["code"] == "categorical_attack_exclusion"


def test_native_eth_and_risk_limitations_are_not_scanned_as_erc20_claims():
    original = conclusion("交易tx.value=0 wei；UNI转账数量按代币decimals换算。")
    original["limitations"] = ["不应将ERC20 raw amount 100000000000000000000000000 wei写为已核验事实。",
                               "不应断言排除攻击。"]
    assert report_quality(original, evidence())["flags"] == []
    original["summary"] = "Native ETH transaction.value is 0 wei. UNI token transfers are separate."
    assert report_quality(original, evidence())["flags"] == []


def test_wei_defect_requires_local_token_context_and_supports_observed_symbol():
    assert report_quality(conclusion("交易的原生ETH金额为0 wei。"), evidence())["flags"] == []
    report = conclusion("Observed ABC token amount is 42 wei.")
    observed = evidence()
    observed["metadata"]["symbol"] = "ABC"
    assert report_quality(report, observed)["flags"][0]["code"] == "erc20_wei_unit"
    assert report_quality(conclusion("ERC20 raw units不能称wei。"), evidence())["flags"] == []


@pytest.mark.parametrize("text", [
    "Native ETH transaction.value was 0 wei. UNI token transfers are separate.",
    "Native ETH value (tx.value) is 0 wei while UNI transfers are separate.",
    "ETH gas cost was 42 wei. UNI token transfers are separate.",
    "ERC20 Transfer exists; native ETH value equals 0 wei.",
])
def test_mixed_native_and_token_claims_do_not_mislabel_native_amount(text):
    assert report_quality(conclusion(text), evidence())["flags"] == []


def test_native_gas_elsewhere_does_not_excuse_an_erc20_wei_amount():
    original = conclusion("ETH gas paid; UNI转移100000000000000000000000000 wei。")
    assert report_quality(original, evidence())["flags"][0]["code"] == "erc20_wei_unit"
