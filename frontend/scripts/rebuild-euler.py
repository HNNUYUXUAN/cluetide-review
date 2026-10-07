"""Build bounded, synthetic replay reports from the attributed public Euler snapshot.

Usage: python frontend/scripts/rebuild-euler.py .
Public chain observations are preserved; report and review language is authored here.
"""
import hashlib
import json
from pathlib import Path
import sys
import zipfile


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(root):
    source = root / "data/cases/euler-20230313"
    output = Path(__file__).resolve().parents[1] / "public/gcc-demo"
    rpc = json.loads((source / "rpc.json").read_text(encoding="utf-8"))
    transfers = json.loads((source / "normalized-transfers.json").read_text(encoding="utf-8"))
    sources = json.loads((source / "sources.json").read_text(encoding="utf-8"))
    # Preserve attribution and reading scope; distribute project summaries and source links.
    for item in sources:
        for key in ("excerpt", "excerpt_file", "excerpt_sha256"):
            item.pop(key, None)
        item["content_type"] = "project_factual_summary"
        item["scope"] = "Project-authored factual summary and attributed source pointer; original webpage and source code are not bundled."
    for item in transfers:
        item["evidence_id"] = f"transfer:1:{item['block_hash']}:{item['transaction_hash']}:{item['log_index']}"
    request = rpc["request"]
    tx = request["transaction_hash"]
    evidence = {
        "schema_version": "cluetide-evidence/v1",
        "request": {"chain_id": 1, "address": request["subject_address"], "token_address": request["token_address"], "from_block": request["start_block"], "to_block": request["end_block"]},
        "transfers": transfers, "sources": sources,
        "metadata": {"decimals": 18, "symbol": "DAI", "name": "Dai Stablecoin", "block_number": rpc["token_state"]["metadata_block_number"], "block_hash": rpc["token_state"]["metadata_block_hash"]},
        "coverage": {"status": "complete", "completed_queries": 2, "planned_queries": 2, "requested_from_block": request["start_block"], "requested_to_block": request["end_block"], "scope": rpc["coverage"]["scope"]},
        "collected_at": rpc["recorded_at_utc"],
        "integrity_statement": "Evidence hashes commit to bytes; they do not establish factual truth.",
    }
    agent_evidence = [
        {"evidence_id": f"receipt:{tx}", "kind": "receipt", "payload": rpc["receipt"]},
        {"evidence_id": f"transaction:{tx}", "kind": "transaction", "payload": rpc["transaction"]},
        *[{"evidence_id": f"source:{item['source_id']}", "kind": "public_context_source", "payload": item} for item in sources],
    ]
    for side in ("before", "after"):
        state = rpc["token_state"]
        block = state[f"totalSupply_{side}_block_number"]
        agent_evidence.append({"evidence_id": f"state:{request['token_address']}:{block}", "kind": "token_state", "payload": {"block_number": block, "block_hash": state[f"totalSupply_{side}_block_hash"], "block_selection": "EIP-1898", "require_canonical": True, "total_supply_raw": str(int(state[f"totalSupply_{side}"], 16))}})
    # Only bounded observations are included; provider endpoints and unrelated finalized-block transactions are omitted.
    raw = {"source_snapshot_sha256": digest((source / "rpc.json").read_bytes()), "request": request, "logs_in": rpc["logs_in"], "logs_out": rpc["logs_out"], "receipt": rpc["receipt"], "transaction": rpc["transaction"], "token_state": rpc["token_state"], "window_headers": [{key: rpc[name][key] for key in ("number", "hash", "parentHash", "timestamp")} for name in ("start_block", "case_block", "end_block")]}
    parent_hash = None
    for version in (1, 2):
        claim = "本窗口记录 Euler 主体的 DAI 流入与流出。"
        if version == 2:
            claim += "入减出只表示所选窗口的净 Transfer 流，不能直接用作事件损失。"
        report = {
            "schema_version": "cluetide-report/v1", "case_id": "euler-20230313-synthetic", "revision": version, "parent_manifest_hash": parent_hash,
            "synthetic_fixture": {"mode": "synthetic", "generator": "frontend/scripts/rebuild-euler.py", "model_requests": 0, "transactions_signed_or_broadcast": 0, "network_reads": 0, "scope": "Public historical observations with synthetic report and review language.", "public_sources": [{"path": f"data/cases/euler-20230313/{name}", "sha256": digest((source / name).read_bytes())} for name in ("rpc.json", "sources.json", "normalized-transfers.json")]},
            "agent": {"synthetic": True, "evidence": agent_evidence, "model_requests": 0},
            "conclusion": {
                "summary": claim,
                "claims": [{"text": claim, "evidence_ids": [item["evidence_id"] for item in transfers]}],
                "assessments": [
                    {"status": "supported", "explanation": "所选交易在保存回执中执行成功。", "support_evidence_ids": [f"receipt:{tx}"], "counter_evidence_ids": []},
                    {"status": "unknown", "explanation": "仅凭三块窗口不能重建完整内部债务状态与全部事件影响。", "support_evidence_ids": [], "counter_evidence_ids": []},
                ],
                "limitations": ["合成解释与复核示例；数据为保存的公开历史观察。", "净 Transfer 流不等同利润、美元损失或全部事件规模。", "SHA-256 校验文件字节，不能证明事实真实性。"],
            },
            "review_records": [] if version == 1 else [{"reviewer": "合成示例复核者", "parent_manifest_hash": parent_hash, "comment": "明确净流量的计算口径，限定主体、代币与窗口。", "synthetic": True}],
        }
        markdown = f"# Euler DAI 公开回放 v{version}\n\n合成报告与复核示例；公开观察取得于 2026-10-07 UTC。\n\n{claim}\n\n窗口：16817995–16817997。\n\n" + "\n".join(f"- {item['publisher']}: {item['url']}" for item in sources) + "\n\nSHA-256 校验文件字节，不能证明事实真实性。\n"
        files = {"evidence.json": canonical(evidence), "report.json": canonical(report), "raw.json": canonical(raw), "report.md": markdown.encode("utf-8")}
        manifest = {"format": "cluetide-public-evidence/v1", "canonicalization": "RFC8785", "integrity_statement": "SHA-256 verifies artifact integrity, not factual truth.", "files": {name: {"sha256": digest(data), "size": len(data)} for name, data in files.items()}}
        files["manifest.json"] = canonical(manifest)
        with zipfile.ZipFile(output / f"euler-20230313-synthetic-v{version}.zip", "w", compression=zipfile.ZIP_STORED) as archive:
            for name in sorted(files):
                info = zipfile.ZipInfo(name, date_time=(2026, 10, 8, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                archive.writestr(info, files[name])
        parent_hash = digest(files["manifest.json"])
    index = {}
    for case_id in ("uniswap93", "euler-20230313"):
        for version in (1, 2):
            path = output / f"{case_id}-synthetic-v{version}.zip"
            with zipfile.ZipFile(path) as archive:
                index[f"{case_id}-v{version}"] = {"archive": digest(path.read_bytes()), "manifest": digest(archive.read("manifest.json"))}
    (output / "fixtures.json").write_bytes(canonical(index))
    print("公开合成案卷：4 个；SHA-256 清单：已生成。")


if __name__ == "__main__":
    build(Path(sys.argv[1]))
