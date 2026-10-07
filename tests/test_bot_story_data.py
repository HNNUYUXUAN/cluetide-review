"""The public BOT story keeps historical report and transaction identities."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_bot_story_data", ROOT / "scripts/build_bot_story_data.py")
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


@pytest.fixture
def public_source_copy(tmp_path):
    snapshot = builder.build_snapshot()
    for item in snapshot["source_files"]:
        destination = tmp_path / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / item["path"], destination)
    return tmp_path


def rewrite_receipt(root: Path, key: str, change) -> None:
    """Rehash a modified source to exercise semantic association checks."""
    workflow_path = root / builder.WORKFLOW_PATH
    workflow = builder.read_json(workflow_path)
    record = next(item for item in workflow["workflow"] if item["step"] == key)
    path = root / record["receipt_path"]
    receipt = builder.read_json(path)
    change(receipt)
    path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    record["receipt_sha256"] = builder.sha256_file(path)
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False), encoding="utf-8")


def test_snapshot_reproduces_from_public_archives_and_preserves_original_wording():
    snapshot = builder.build_snapshot()
    assert builder.encode_snapshot(snapshot) == (ROOT / builder.OUTPUT_PATH).read_bytes()
    assert len(builder.encode_snapshot(snapshot)) <= 30_000
    original, review, corrected = snapshot["steps"]
    for step in (original, corrected):
        archive = ROOT / f'data/demo/bundles/gcc-uniswap93-adaptive-{step["key"]}.zip'
        conclusion = builder.import_bundle(archive).report["conclusion"]
        assert step["summary"] == conclusion["summary"]
        assert step["report_claims"] == [claim["text"] for claim in conclusion["claims"][:3]]
        assert step["limitations"] == conclusion["limitations"]
    assert original["summary"] != corrected["summary"]
    assert "原始模型报告" in original["source_label"]
    assert "治理提案被正确执行" in original["report_claims"][2]
    assert "执行授权和其他治理动作需另行核验" in corrected["report_claims"][2]
    assert review["summary"] == "将成功回执、治理解释与授权范围分开，保留未核验事项。"


def test_three_receipts_bind_exact_versions_and_review_target():
    snapshot = builder.build_snapshot()
    original, review, corrected = snapshot["steps"]
    assert [(step["local_version_id"], step["version_id"]) for step in snapshot["steps"]] == [(32, "1"), (32, "1"), (33, "2")]
    assert corrected["parent_version_id"] == original["version_id"]
    assert review["content_hash"] == original["content_hash"]
    assert corrected["content_hash"] != original["content_hash"]
    assert review["review_id"] == "1" and review["local_review_id"] == 7
    assert len({step["transaction_hash"] for step in snapshot["steps"]}) == 3
    for step in snapshot["steps"]:
        receipt = builder.read_json(ROOT / f'data/demo/bot-testnet-{step["key"]}-receipt-20261008.json')
        assert step["prepared"] == receipt["prepared_public_request"]
        assert step["transaction_hash"] == receipt["receipt"]["transaction_hash"]
        assert step["content_hash"] == receipt["verification"]["commitments"]["content_hash"]
    assert original["bundle_url"].endswith("?revision=1")
    assert review["bundle_url"] == original["bundle_url"]
    assert corrected["bundle_url"].endswith("?revision=2")


def test_quality_limits_remain_separate_from_transaction_verification():
    snapshot = builder.build_snapshot()
    for step in snapshot["steps"]:
        assert step["archived_status"] == "verified"
        quality = step["evidence_quality"]
        assert quality["citation_status"] == "needs_review"
        assert len(quality["citation_issues"]) == 2
        assert all("missing_agent_rpc_observation" in item for item in quality["citation_issues"])
        assert quality["factual_verification"] is False
        assert quality["source_authenticity_verified"] is False
        assert quality["assessment_statuses"]["supply_decrease"] == "unknown"
    assert "同一钱包" in snapshot["identity_note"]


def test_public_snapshot_uses_bounded_allowlisted_fields():
    snapshot = builder.build_snapshot()
    assert set(snapshot) == {
        "schema_version", "observed_at_beijing", "case_id", "title", "chain_id",
        "contract_address", "wallet", "contract_url", "business_fee_testnet_bot",
        "identity_note", "evidence_note", "source_files", "steps",
    }
    for step in snapshot["steps"]:
        assert set(step["prepared"]) <= builder.PREPARED_FIELDS
    payload = builder.encode_snapshot(snapshot).decode("utf-8")
    for field in ("account_balance_wei", "account_nonce", "health", "model_budget", "screenshot_local_path", "rpc_url", "private_key", "authorization"):
        assert f'"{field}"' not in payload
    assert "local-data/" not in payload and "local-state" not in payload


def test_modified_archive_bytes_are_rejected(public_source_copy):
    path = public_source_copy / "data/demo/bundles/gcc-uniswap93-adaptive-v1.zip"
    path.write_bytes(path.read_bytes() + b"altered")
    with pytest.raises(ValueError, match="archive SHA-256 mismatch"):
        builder.build_snapshot(public_source_copy)


def test_modified_receipt_bytes_are_rejected(public_source_copy):
    path = public_source_copy / "data/demo/bot-testnet-v1-receipt-20261008.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="receipt SHA-256 mismatch"):
        builder.build_snapshot(public_source_copy)


@pytest.mark.parametrize(("key", "field", "value", "error"), [
    ("review", "onchain_version_id", "2", "Prepared review target mismatch"),
    ("v2", "onchain_version_id", "2", "Prepared parent mismatch"),
    ("v1", "local_version_id", 33, "prepared local version mismatch"),
    ("v1", "action", "append_version", "action mismatch"),
])
def test_rehashed_receipt_cannot_change_its_business_target(public_source_copy, key, field, value, error):
    rewrite_receipt(public_source_copy, key, lambda receipt: receipt["prepared_public_request"].update({field: value}))
    with pytest.raises(ValueError, match=error):
        builder.build_snapshot(public_source_copy)


def test_additional_request_fields_require_explicit_public_mapping(public_source_copy):
    rewrite_receipt(public_source_copy, "review", lambda receipt: receipt["prepared_public_request"].update({"session_note": "private runtime state"}))
    with pytest.raises(ValueError, match="unexpected public request field"):
        builder.build_snapshot(public_source_copy)
