"""Bounded quality screens grounded in stored reports and exact Transfer values."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from cluetide.report_quality import report_quality
from cluetide.schemas import MAX_UINT256


def claim_report(text, references=None):
    return {"summary": "有限窗口观察。", "claims": [{
        "text": text, "evidence_ids": references or ["transfer:sample"]}]}


def transfer_evidence(raw="100000000000000000000000000"):
    return {"transfers": [{"evidence_id": "transfer:sample", "value_raw": raw}]}


def codes(report, evidence=None):
    return {item["code"] for item in report_quality(report, evidence)["flags"]}


@pytest.mark.parametrize("text", [
    "转账金额为1亿代币（原始值1000000000000000000000000000）。",
    "Transfer raw amount is 1000000000000000000000000000.",
    "转移金额为1000000000000000000000000000原始单位。",
    "Transfer raw value: 1,000,000,000,000,000,000,000,000,000.",
    "Transfer: 1000000000000000000000000000 base units.",
])
def test_explicit_raw_literals_are_compared_to_the_cited_transfer(text):
    report = claim_report(text, ["transfer:sample", "derived:transfer-amounts"])
    original = deepcopy(report)
    result = report_quality({"conclusion": report}, transfer_evidence())
    assert result["flags"] == [{
        "code": "transfer_raw_amount_mismatch",
        "message": "An explicit raw amount differs from the uniquely cited Transfer value; compare the original integer.",
        "locations": ["conclusion.claims[0].text"],
    }]
    assert result["schema_version"] == "cluetide-report-quality/v1"
    assert result["report_modified"] is False
    assert report == original


@pytest.mark.parametrize("raw", ["0", "1", str(MAX_UINT256)])
def test_raw_comparison_preserves_full_uint256_precision(raw):
    assert codes(claim_report(f"Transfer raw amount = {raw}"), transfer_evidence(raw)) == set()
    assert codes(claim_report(f"Transfer raw amount = {int(raw) + 1}"), transfer_evidence(raw)) == {
        "transfer_raw_amount_mismatch"}


@pytest.mark.parametrize("text", [
    "转账原始值100000000000000000000000000。",
    "转账原始值为100,000,000,000,000,000,000,000,000。",
    "转账原始值为000100000000000000000000000000。",
    "Transfer raw value is 1e26.",
    "Transfer raw value is 100000000000000000000000000.0.",
    "Transfer raw value is 0x52b7d2dcc80cd2e4000000.",
    "转账数量约1亿代币。",
    "如果转账原始值为123，则需要补查。",
    "无法证明转账原始值为123。",
    "There is no evidence that the Transfer raw amount is 123.",
    "If the Transfer raw amount is 123, investigate.",
    "Transfer raw value is not 123.",
])
def test_correct_raw_and_nonliteral_or_qualified_amounts_remain_outside_mismatch(text):
    assert codes(claim_report(text), transfer_evidence()) == set()


@pytest.mark.parametrize("text", [
    "转账与 Approval 事件在同一交易，Approval 原始值123。",
    "转账时 totalSupply 原始值123。",
    "两笔转账合计原始值123。",
    "Transfer logs and balance raw value 123.",
    "Transfer raw value and native tx.value raw amount 123.",
])
def test_raw_comparison_keeps_other_fields_and_aggregates_for_semantic_review(text):
    assert codes(claim_report(text), transfer_evidence()) == set()


def test_raw_comparison_requires_one_unambiguous_direct_transfer_reference():
    report = claim_report("转账原始值123。")
    assert codes(report) == set()
    assert codes(claim_report("转账原始值123。", ["derived:transfer-amounts"]), transfer_evidence()) == set()
    assert codes(claim_report("转账原始值123。", ["transfer:sample", "receipt:sample"]), transfer_evidence()) == set()
    evidence = transfer_evidence()
    evidence["transfers"].append({"evidence_id": "transfer:other", "value_raw": "42"})
    assert codes(claim_report("转账原始值123。", ["transfer:sample", "transfer:other"]), evidence) == set()
    evidence["transfers"].append({"evidence_id": "transfer:sample", "value_raw": "42"})
    assert codes(report, evidence) == set()


def test_raw_check_uses_evidence_not_self_declared_display_facts_or_old_agent_text():
    report = {"conclusion": claim_report("转账原始值123。"),
              "transfer_facts": [{"evidence_id": "transfer:sample", "raw_amount": "123"}],
              "agent": {"report": claim_report("转账原始值42。")}}
    assert codes(report, transfer_evidence("42")) == {"transfer_raw_amount_mismatch"}
    report["conclusion"]["claims"][0]["text"] = "转账原始值42。"
    report["agent"]["report"]["claims"][0]["text"] = "转账原始值123。"
    assert codes(report, transfer_evidence("42")) == set()


@pytest.mark.parametrize("text", [
    "交易状态成功，表明治理提案被正确执行。",
    "交易执行成功，因此这是正常合约调用。",
    "Transaction succeeded, establishing safe contract execution.",
    "Receipt status=0x1 means the proposal executed correctly.",
])
def test_execution_success_is_separate_from_behavioral_correctness(text):
    assert codes({"summary": text}) == {"execution_success_generalization"}


@pytest.mark.parametrize("text", [
    "交易状态成功，仍需核对提案动作和授权。",
    "交易执行成功，但无法证明治理提案被正确执行。",
    "交易状态成功不能证明这是正常合约调用。",
    "交易成功不代表治理提案被正确执行。",
    "Successful transaction execution does not establish safe contract execution.",
    "Transaction succeeded, but we cannot claim the proposal executed correctly.",
    "不能确认交易成功，仍需调查是否为正常合约调用。",
    "需要核对正常合约调用是否存在；当前只有成功回执。",
    "交易成功，需要确认是否为正常合约调用。",
    "交易成功，但治理提案是否正确执行尚未核实。",
    "Transaction succeeded, but the proposal was not executed correctly.",
])
def test_execution_scope_qualifiers_remain_valid(text):
    assert codes({"summary": text}) == set()


@pytest.mark.parametrize("status,flagged", [("supported", True), ("unknown", False), ("refuted", False)])
def test_candidate_execution_inference_uses_observed_checks_and_judgment(status, flagged):
    report = {"summary": "有限观察。", "assessments": [{
        "status": status, "explanation": "正常合约调用执行代币转移操作",
        "checks": ["交易执行成功（status=0x1）"],
        "unknowns": ["合约具体功能未知。"],
    }]}
    result = report_quality(report)
    assert (result["status"] == "needs_review") is flagged
    if flagged:
        assert result["flags"][0]["locations"] == ["assessments[0].explanation"]


@pytest.mark.parametrize("text", [
    "两个读数相等，但无法证明该代币合约未实现销毁机制。",
    "两个读数相等，不意味着合约没有销毁机制。",
    "两个读数相等，表明无法证明合约未实现销毁机制。",
    "Identical getters do not prove the contract has no burn mechanism.",
    "We cannot claim the token lacks burn functionality.",
    "If the contract has no burn function, further checks are needed.",
    "两个读数相等，合约是否没有销毁机制仍待核查。",
    "合约没有足够证据证明销毁机制是否存在。",
])
def test_qualified_contract_implementation_statements_are_not_overclaims(text):
    assert codes({"summary": text}) == set()


def test_qualifier_on_earlier_clause_does_not_erase_later_implementation_claim():
    assert codes({"summary": "无法核对全部调用，但是两个读数相等，证明合约没有销毁机制。"}) == {
        "contract_burn_generalization"}


def test_archived_accepted_model_outputs_reproduce_deterministic_findings():
    # Public report and evidence fields exercise deterministic quality checks.
    path = Path(__file__).resolve().parents[1] / "tests/fixtures/quality-regression.json"
    examples = json.loads(path.read_text("utf-8"))
    quantity_run = examples["quantity"]
    transfer_items = [item["payload"] for item in quantity_run["evidence"] if item["kind"] == "transfer"]
    quality = report_quality(quantity_run["report"], {"transfers": transfer_items})
    finding = next(item for item in quality["flags"] if item["code"] == "transfer_raw_amount_mismatch")
    assert finding["locations"] == ["claims[0].text"]
    quality = report_quality(examples["execution"]["report"])
    finding = next(item for item in quality["flags"] if item["code"] == "execution_success_generalization")
    assert finding["locations"] == ["assessments[1].explanation"]
