"""Mainnet product records reproduce the four verified public transactions."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mainnet_story_builder", ROOT / "scripts/build_bot_story_data.py")
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


@pytest.fixture
def mainnet_sources(tmp_path):
    if not (ROOT / builder.MAINNET_WORKFLOW_PATH).exists():
        pytest.skip("The complete mainnet receipt archive is required")
    snapshot = builder.build_snapshot(network="mainnet")
    for item in snapshot["source_files"]:
        target = tmp_path / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / item["path"], target)
    return tmp_path


def rewrite_record(root, key, change):
    workflow_path = root / builder.MAINNET_WORKFLOW_PATH
    workflow = builder.read_json(workflow_path)
    entry = workflow["deployment"] if key == "deploy" else next(item for item in workflow["workflow"] if item["step"] == key)
    path = root / entry["receipt_path"]
    receipt = builder.read_json(path)
    change(receipt)
    path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    entry["receipt_sha256"] = builder.sha256_file(path)
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False), encoding="utf-8")


def test_mainnet_snapshot_uses_four_distinct_verified_transactions(mainnet_sources):
    snapshot = builder.build_snapshot(mainnet_sources, network="mainnet")
    assert snapshot["chain_id"] == snapshot["network"]["chain_id"] == 677
    assert snapshot["deployment"]["archived_status"] == "verified"
    assert snapshot["contract_url"] == "https://scan.botchain.ai/address/" + snapshot["contract_address"]
    hashes = [snapshot["deployment"]["transaction_hash"], *(step["transaction_hash"] for step in snapshot["steps"])]
    assert len(set(hashes)) == 4
    for step in snapshot["steps"]:
        assert step["prepared"]["chain_id"] == 677
        assert step["prepared"]["contract_address"] == snapshot["contract_address"]
        assert step["tx_url"] == "https://scan.botchain.ai/tx/" + step["transaction_hash"]
        assert step["evidence_quality"]["citation_status"] == "needs_review"
    original, review, corrected = snapshot["steps"]
    assert review["version_id"] == original["version_id"] == corrected["parent_version_id"]
    assert review["content_hash"] == original["content_hash"] != corrected["content_hash"]
    assert original["prepared"]["evidence_uri"] == "https://hnnuyuxuan.github.io/cluetide-app/bot/bot-demo/uni-v1.zip"
    assert corrected["prepared"]["evidence_uri"] == "https://hnnuyuxuan.github.io/cluetide-app/bot/bot-demo/uni-v2.zip"
    assert review["prepared"]["evidence_uri"] == corrected["prepared"]["evidence_uri"]
    assert builder.encode_snapshot(snapshot) == (ROOT / builder.MAINNET_OUTPUT_PATH).read_bytes()


@pytest.mark.parametrize(("key", "change", "message"), [
    ("deploy", lambda r: r["prepared_public_request"].update(chain_id=968), "Deployment public intent mismatch"),
    ("deploy", lambda r: r["verification"].update(confirmations=2), "Mainnet confirmations incomplete"),
    ("deploy", lambda r: r["verification"].update(status="pending"), "Mainnet verification required"),
    ("deploy", lambda r: r["verification"]["artifact"].update(runtime_sha256="0" * 64), "Deployment runtime commitment mismatch"),
    ("v1", lambda r: r["prepared_public_request"].update(chain_id=968), "prepared case/network mismatch"),
    ("v1", lambda r: r["public_rpc"]["transaction"].update(chainId="0x3c8"), "Mainnet transaction chain mismatch"),
    ("v1", lambda r: r["public_rpc"].update(rpc_url="https://rpc.bohr.life"), "Mainnet archived RPC identity mismatch"),
    ("v1", lambda r: r["prepared_public_request"].update(evidence_uri="https://example.com/other.zip"), "mainnet evidence URI mismatch"),
    ("review", lambda r: r["verification"].update(tx_link="https://scan.bohr.life/tx/" + r["verification"]["transaction_hash"]), "Mainnet explorer mismatch"),
    ("review", lambda r: r["prepared_public_request"].update(evidence_uri="https://example.com/review.zip"), "Mainnet review URI mismatch"),
    ("v2", lambda r: r["verification"].update(actual_fee_wei="1"), "verified fee mismatch"),
])
def test_rehashed_mainnet_receipts_preserve_network_and_commitment_bindings(mainnet_sources, key, change, message):
    rewrite_record(mainnet_sources, key, change)
    with pytest.raises(ValueError, match=message):
        builder.build_snapshot(mainnet_sources, network="mainnet")


def test_mainnet_requires_complete_workflow(mainnet_sources):
    path = mainnet_sources / builder.MAINNET_WORKFLOW_PATH
    workflow = builder.read_json(path)
    workflow["workflow"] = workflow["workflow"][:2]
    path.write_text(json.dumps(workflow), encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected workflow order"):
        builder.build_snapshot(mainnet_sources, network="mainnet")


def test_mainnet_snapshot_exposes_only_public_record_fields(mainnet_sources):
    snapshot = builder.build_snapshot(mainnet_sources, network="mainnet")
    text = builder.encode_snapshot(snapshot).decode("utf-8")
    assert len(builder.encode_snapshot(snapshot)) <= builder.MAX_BYTES
    assert "business_fee_testnet_bot" not in snapshot
    for field in ("account_balance_wei", "account_nonce", "health", "model_budget", "screenshot_local_path", "private_key", "authorization"):
        assert f'"{field}"' not in text
    assert "local-data/" not in text and "local-only/" not in text
