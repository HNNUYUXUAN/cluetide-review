"""Build the BOT story snapshot from archived public reports and receipts."""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

from cluetide.bundles import canonical_json_bytes, import_bundle, manifest_sha256

ROOT = Path(__file__).resolve().parents[1]
CASE_ID = "gcc-uniswap93-adaptive-20261007"
WORKFLOW_PATH = "data/demo/bot-testnet-workflow-20261008.json"
REVIEW_INDEX_PATH = "data/demo/gcc-saved-model-review-20261007.json"
OUTPUT_PATH = "frontend/src/bot-story-data.json"
MAX_BYTES = 30_000
PREPARED_FIELDS = frozenset({"chain_id", "action", "account", "contract_address", "case_id", "local_version_id", "onchain_version_id", "local_review_id"})
STEP_COPY = {
    "v1": ("原始模型报告", "首次登记 · 链上版本 1", "保存当时的模型判断与证据内容承诺，让之后的复核有明确对象。"),
    "review": ("指定版本复核", "请求更正 · 绑定版本 1", "复核意见绑定原始报告的内容哈希，明确指出需要收敛的结论范围。"),
    "v2": ("更正后的报告", "追加登记 · 版本 2 → 版本 1", "把可观察事实与解释、未知分开，追加更正版本并保留完整父版本关系。"),
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_quality(report: dict) -> dict:
    citation = report["citation_validation"]
    quality = report["quality_validation"]
    return {
        "review_status": report["review_status"],
        "quality_status": quality["status"],
        "quality_scope": quality["scope"],
        "citation_status": citation["status"],
        "citation_issues": [f'{item["code"]} · {item["location"]}: {item["message"]}' for item in citation["issues"]],
        "citation_scope": citation["scope"],
        "factual_verification": citation["factual_verification"],
        "source_authenticity_verified": citation["source_authenticity_verified"],
        "assessment_statuses": {item["explanation_id"]: item["status"] for item in report["conclusion"]["assessments"]},
    }


def build_snapshot(root: Path = ROOT) -> dict:
    workflow = read_json(root / WORKFLOW_PATH)
    index = read_json(root / REVIEW_INDEX_PATH)
    require(workflow["case_id"] == CASE_ID and workflow["chain_id"] == 968, "Unexpected case or network")
    archives = next(item for item in index["cases"] if item["case_id"] == CASE_ID)["archives"]
    reports, manifest_hashes, archive_by_key = {}, {}, {}
    for key, revision in (("v1", 1), ("v2", 2)):
        archive = next(item for item in archives if item["revision"] == revision)
        path = root / archive["path"]
        require(sha256_file(path) == archive["sha256"], f"{key}: archive SHA-256 mismatch")
        bundle = import_bundle(path)
        digest = manifest_sha256(bundle.manifest)
        require(digest == archive["manifest_hash"], f"{key}: manifest mismatch")
        require(bundle.report["case_id"] == CASE_ID and bundle.report["revision"] == revision, f"{key}: report identity mismatch")
        require(archive["version_id"] == workflow["local_to_onchain"][key]["local_version_id"], f"{key}: local version mismatch")
        reports[key], manifest_hashes[key], archive_by_key[key] = bundle.report, digest, archive
    require(reports["v1"]["agent"] == reports["v2"]["agent"], "Original model output changed")
    require(reports["v2"]["parent_manifest_hash"] == manifest_hashes["v1"], "Parent manifest mismatch")
    require(archive_by_key["v2"]["parent_version_id"] == archive_by_key["v1"]["version_id"], "Local parent mismatch")
    review = next(item for item in reports["v2"]["review_records"] if item["review_id"] == workflow["local_to_onchain"]["review"]["local_review_id"])
    review_payload = {field: review[field] for field in ("case_id", "version_id", "content_hash", "comment", "reviewer")}
    require(hashlib.sha256(canonical_json_bytes(review_payload)).hexdigest() == review["review_hash"], "Review commitment mismatch")
    require(review["case_id"] == CASE_ID and review["content_hash"] == manifest_hashes["v1"], "Review target mismatch")
    require(review["version_id"] == archive_by_key["v1"]["version_id"], "Review version mismatch")

    steps = []
    for record in workflow["workflow"]:
        key = record["step"]
        require(key in STEP_COPY, "Unexpected workflow step")
        receipt_path = root / record["receipt_path"]
        require(sha256_file(receipt_path) == record["receipt_sha256"], f"{key}: receipt SHA-256 mismatch")
        receipt = read_json(receipt_path)
        prepared = receipt["prepared_public_request"]
        require(set(prepared) <= PREPARED_FIELDS, f"{key}: unexpected public request field")
        require(prepared["action"] == record["action"] == {"v1": "create_case", "review": "add_review", "v2": "append_version"}[key], f"{key}: action mismatch")
        require(prepared["chain_id"] == workflow["chain_id"] and prepared["case_id"] == CASE_ID, f"{key}: prepared case/network mismatch")
        require(prepared["account"] == workflow["wallet"] and prepared["contract_address"] == workflow["contract_address"], f"{key}: prepared wallet/contract mismatch")
        verification = receipt["verification"]
        require(verification["status"] == record["status"] == "verified" and receipt["receipt"]["status"] == 1, f"{key}: receipt not verified")
        require(verification["transaction_hash"] == receipt["receipt"]["transaction_hash"] == record["transaction_hash"], f"{key}: transaction mismatch")
        require(receipt["tx_url"] == record["tx_url"] == verification["tx_link"], f"{key}: transaction URL mismatch")
        require(receipt["receipt"]["from"] == workflow["wallet"] and receipt["receipt"]["to"] == workflow["contract_address"], f"{key}: receipt wallet/contract mismatch")
        require(receipt["receipt"]["fee_wei"] == record["actual_fee_wei"], f"{key}: receipt fee mismatch")
        local = workflow["local_to_onchain"][key]
        require(prepared["local_version_id"] == local["local_version_id"], f"{key}: prepared local version mismatch")
        report_key = "v1" if key == "review" else key
        report = reports[report_key]
        version = verification["getter_state"]["version"]
        require(version["content_hash"] == verification["commitments"]["content_hash"] == manifest_hashes[report_key], f"{key}: content commitment mismatch")
        require(version["version_id"] == local["version_id"] == record["onchain_ids"]["version_id"], f"{key}: onchain version mismatch")
        if key == "review":
            onchain_review = verification["getter_state"]["review"]
            require(onchain_review["review_hash"] == review["review_hash"], "Onchain review commitment mismatch")
            require(onchain_review["review_id"] == local["review_id"] and onchain_review["version_id"] == local["version_id"], "Onchain review target mismatch")
            require(onchain_review["decision"] == review["decision"], "Onchain review decision mismatch")
            require(prepared["local_review_id"] == review["review_id"], "Prepared review mismatch")
            require(prepared["onchain_version_id"] == onchain_review["version_id"], "Prepared review target mismatch")
        else:
            require(version["parent_version_id"] == local["parent_version_id"], f"{key}: onchain parent mismatch")
            if key == "v2":
                require(prepared["onchain_version_id"] == version["parent_version_id"], "Prepared parent mismatch")
        title, subtitle, explanation = STEP_COPY[key]
        step = {
            "key": key,
            "title": title,
            "subtitle": subtitle,
            "summary": review["comment"] if key == "review" else report["conclusion"]["summary"],
            "source_label": "本地复核意见原文" if key == "review" else ("原始模型报告原文 · v1" if key == "v1" else "人工复核更正报告原文 · v2"),
            "local_version_id": local["local_version_id"],
            "version_id": local["version_id"],
            "content_hash": version["content_hash"],
            "transaction_hash": record["transaction_hash"],
            "tx_url": record["tx_url"],
            "archived_status": verification["status"],
            "prepared": dict(prepared),
            "bundle_url": f'/api/investigations/{CASE_ID}/bundle?revision={report["revision"]}',
            "explanation": explanation,
            "report_claims": [] if key == "review" else [item["text"] for item in report["conclusion"]["claims"][:3]],
            "limitations": report["conclusion"]["limitations"],
            "evidence_quality": evidence_quality(report),
            "receipt_observed_at_beijing": receipt["observed_at_beijing"],
        }
        if key == "review":
            step.update(review_id=local["review_id"], local_review_id=review["review_id"], review_hash=review["review_hash"], decision=review["decision"])
        else:
            step["parent_version_id"] = local["parent_version_id"]
        steps.append(step)
    require([step["key"] for step in steps] == ["v1", "review", "v2"], "Unexpected workflow order")
    require(sum(int(item["actual_fee_wei"]) for item in workflow["workflow"]) == int(workflow["business_actual_fee_wei"]), "Business fee mismatch")
    require(Decimal(workflow["business_actual_fee_testnet_bot"]) * 10**18 == int(workflow["business_actual_fee_wei"]), "Business fee unit mismatch")
    snapshot = {
        "schema_version": "cluetide-bot-story/v1",
        "observed_at_beijing": workflow["observed_at_beijing"],
        "case_id": CASE_ID,
        "title": "一亿 UNI 转账，从模型判断到可追溯更正",
        "chain_id": workflow["chain_id"],
        "contract_address": workflow["contract_address"],
        "wallet": workflow["wallet"],
        "contract_url": workflow["contract_url"],
        "business_fee_testnet_bot": workflow["business_actual_fee_testnet_bot"],
        "identity_note": "本案例作者与链上复核者使用同一钱包；本地复核标签为 local-semantic-reviewer。该流程展示版本复核，未建立独立身份认证。",
        "evidence_note": "归档状态记录 BOT 测试网 968 当时的回执与内容承诺核验。哈希证明对应文件字节；报告引用结构仍有两项待复核，事实与解释需按证据范围理解。完整历史证据包通过本地应用 API 按 revision 下载。",
        "source_files": [
            {"path": WORKFLOW_PATH, "sha256": sha256_file(root / WORKFLOW_PATH)},
            {"path": REVIEW_INDEX_PATH, "sha256": sha256_file(root / REVIEW_INDEX_PATH)},
            *[{"path": item["path"], "sha256": item["sha256"]} for item in archives],
            *[{"path": item["receipt_path"], "sha256": item["receipt_sha256"]} for item in workflow["workflow"]],
        ],
        "steps": steps,
    }
    require(len(encode_snapshot(snapshot)) <= MAX_BYTES, "Snapshot exceeds 30 KB")
    return snapshot


def encode_snapshot(snapshot: dict) -> bytes:
    return (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def check_live(snapshot: dict, base_url: str) -> None:
    """Compare local API historical records and bundle bytes with the archive."""
    with urlopen(base_url.rstrip("/") + f"/api/investigations/{CASE_ID}", timeout=15) as response:
        document = json.load(response)
    require(document["id"] == CASE_ID, "Live case mismatch")
    for step in snapshot["steps"]:
        version = next(item for item in document["versions"] if item["version_id"] == step["local_version_id"])
        require(version["content_hash"] == step["content_hash"] and version["evidence_uri"] == step["bundle_url"], "Live version mismatch")
    review_step = snapshot["steps"][1]
    review = next(item for item in document["reviews"] if item["review_id"] == review_step["local_review_id"])
    require(review["review_hash"] == review_step["review_hash"] and review["comment"] == review_step["summary"], "Live review mismatch")
    for step in (snapshot["steps"][0], snapshot["steps"][2]):
        with urlopen(base_url.rstrip("/") + step["bundle_url"], timeout=15) as response:
            archive = response.read()
        source_path = f'data/demo/bundles/gcc-uniswap93-adaptive-{step["key"]}.zip'
        expected = next(item["sha256"] for item in snapshot["source_files"] if item["path"] == source_path)
        require(hashlib.sha256(archive).hexdigest() == expected, "Live archive bytes mismatch")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compare generated data with the saved snapshot")
    parser.add_argument("--check-live", metavar="BASE_URL", help="Read-only comparison with the local application API")
    args = parser.parse_args()
    snapshot = build_snapshot()
    payload = encode_snapshot(snapshot)
    target = ROOT / OUTPUT_PATH
    if args.check:
        require(target.read_bytes() == payload, "Saved story snapshot differs from archived sources")
    else:
        target.write_bytes(payload)
    if args.check_live:
        check_live(snapshot, args.check_live)
    print(json.dumps({"path": OUTPUT_PATH, "bytes": len(payload), "steps": len(snapshot["steps"]), "archive_validation": "passed", "live_api_comparison": "passed" if args.check_live else "not_requested"}))


if __name__ == "__main__":
    main()
