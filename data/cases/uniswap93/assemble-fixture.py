"""Assemble a public, offline case snapshot from the retained RPC responses."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
TX = "0x091f0083242a777d55821c1189e568d6d033d9da501b75087dc736fa143d2c1e"
TOKEN = "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"
TIMELOCK = "0x1a9c8182c09f50c8318d769245bea52c32be35bc"
DEAD = "0x000000000000000000000000000000000000dead"
GOVERNOR = "0x408ed6354d4973f66138c91495f2f2fcbd8724c3"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def result(name: str):
    document = json.loads((RAW / f"{name}.response.json").read_text(encoding="utf-8"))
    assert "error" not in document, name
    assert document.get("result") is not None, name
    return document["result"]


def write_json(name: str, document):
    (ROOT / name).write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def abi_string(value: str) -> str:
    raw = bytes.fromhex(value.removeprefix("0x"))
    offset = int.from_bytes(raw[:32], "big")
    assert offset == 32 and len(raw) >= 64
    size = int.from_bytes(raw[offset:offset + 32], "big")
    assert 0 <= size <= 128 and offset + 32 + size <= len(raw)
    return raw[offset + 32:offset + 32 + size].decode("utf-8")


def main():
    recorded = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    receipt = result("tenderly-receipt")
    receipt_other = result("drpc-receipt")
    transaction = result("transaction")
    block = result("case-block")
    finalized = result("finalized")
    logs = result("tenderly-logs-all")
    outgoing = [entry for entry in logs if "0x" + entry["topics"][1][-40:] == TIMELOCK]
    incoming = [entry for entry in logs if "0x" + entry["topics"][2][-40:] == TIMELOCK]
    before = result("tenderly-total-supply-before")
    after = result("tenderly-total-supply-after")
    decimals = result("tenderly-decimals")
    state = result("tenderly-governor-state")
    end_block = result("end-block")
    metadata_decimals = result("metadata-end-decimals")
    metadata_symbol = result("metadata-end-symbol")
    metadata_name = result("metadata-end-name")
    assert int(metadata_decimals, 16) == 18
    assert abi_string(metadata_symbol) == "UNI"
    assert abi_string(metadata_name) == "Uniswap"
    metadata_queries = []
    for query_name in ("metadata-end-decimals", "metadata-end-symbol", "metadata-end-name"):
        query = json.loads((RAW / f"{query_name}.request.json").read_text(encoding="utf-8"))
        assert query["method"] == "eth_call"
        assert query["params"][0]["to"] == TOKEN
        assert query["params"][1] == {"blockHash": end_block["hash"], "requireCanonical": True}
        metadata_queries.append({"name": query_name, "request": query, "result": result(query_name)})

    assert result("chain-id") == "0x1"
    assert receipt["status"] == "0x1"
    assert receipt["logs"] == receipt_other["logs"]
    assert transaction["hash"] == receipt["transactionHash"] == TX
    assert transaction["blockHash"] == receipt["blockHash"] == block["hash"]
    assert int(block["number"], 16) == 24106378
    assert TX in block["transactions"]
    assert transaction["to"] == GOVERNOR
    assert transaction["input"] == "0xfe0d94c1" + f"{93:064x}"
    assert int(finalized["number"], 16) > 24106388
    assert len(logs) == 5 and len(outgoing) == 1 and incoming == []
    assert all(24106368 <= int(entry["blockNumber"], 16) <= 24106388 for entry in logs)
    assert all(entry["address"] == TOKEN and entry["topics"][0] == TRANSFER for entry in logs)
    assert len({(entry["blockHash"], entry["transactionHash"], entry["logIndex"]) for entry in logs}) == len(logs)
    transfer = outgoing[0]
    assert transfer in receipt["logs"]
    assert int(transfer["logIndex"], 16) == 11
    assert "0x" + transfer["topics"][2][-40:] == DEAD
    assert int(transfer["data"], 16) == 100_000_000 * 10**18
    assert int(decimals, 16) == 18
    assert int(before, 16) == int(after, 16) == 1_000_000_000 * 10**18
    assert int(state, 16) == 7

    captures = [json.loads((ROOT / name).read_text(encoding="utf-8")) for name in ("capture.json", "capture-fallback.json", "capture-state.json", "capture-metadata.json")]
    observations = [observation for capture in captures for observation in capture["observations"]]
    provenance = {observation["name"]: observation for observation in observations}
    request = {
        "chain_id": 1,
        "subject_address": TIMELOCK,
        "token_address": TOKEN,
        "start_block": 24106368,
        "end_block": 24106388,
        "transaction_hash": TX,
    }
    coverage = {
        "status": "complete_query_response",
        "scope": "UNI Transfer events for the inclusive bounded window; subject subsets locally filtered from the complete returned token log list",
        "from_block": 24106368,
        "to_block": 24106388,
        "total_blocks": 21,
        "all_token_transfer_count": len(logs),
        "subject_outgoing_count": len(outgoing),
        "subject_incoming_count": len(incoming),
        "log_source": "tenderly-logs-all",
        "missing_ranges": [],
        "finality_basis": "Captured provider-reported finalized block, whose height exceeds the requested window; no independently verified header ancestry or consensus proof is included.",
    }
    rpc = {
        "schema_version": "cluetide.rpc-cache.v1",
        "mode": "public_cache_snapshot",
        "recorded_at_utc": recorded,
        "request": request,
        "eth_chainId": result("chain-id"),
        "finalized": finalized,
        "start_block": result("start-block"),
        "end_block": end_block,
        "case_block": block,
        "logs_all": logs,
        "logs_out": outgoing,
        "logs_in": incoming,
        "receipt": receipt,
        "transaction": transaction,
        "token_state": {
            "decimals": metadata_decimals,
            "symbol": metadata_symbol,
            "name": metadata_name,
            "metadata_block_number": int(end_block["number"], 16),
            "metadata_block_hash": end_block["hash"],
            "metadata_block_selection": "EIP-1898",
            "metadata_require_canonical": True,
            "decimals_case_block": decimals,
            "decimals_case_block_number": 24106378,
            "totalSupply_before": before,
            "totalSupply_before_block_number": 24106377,
            "totalSupply_after": after,
            "totalSupply_after_block_number": 24106378,
        },
        "token_state_queries": metadata_queries,
        "token_metadata": {
            "name": abi_string(metadata_name),
            "symbol": abi_string(metadata_symbol),
            "decimals": int(metadata_decimals, 16),
            "block_number": int(end_block["number"], 16),
            "block_hash": end_block["hash"],
            "block_selection": "EIP-1898",
            "require_canonical": True,
        },
        "state_summary": {
            "decimals": 18,
            "before_block": 24106377,
            "after_block": 24106378,
            "total_supply_before_raw": str(int(before, 16)),
            "total_supply_after_raw": str(int(after, 16)),
            "governor_state": "Executed",
            "governor_state_raw": state,
        },
        "coverage": coverage,
        "provenance": provenance,
        "integrity_statement": "SHA-256 commits to saved bytes; it does not prove factual truth. Public RPC providers are trusted data sources in this snapshot.",
    }
    write_json("rpc.json", rpc)
    normalized = {
        "chain_id": 1,
        "token_address": TOKEN,
        "from_address": TIMELOCK,
        "to_address": DEAD,
        "amount_raw": str(int(transfer["data"], 16)),
        "amount_display": "100000000",
        "decimals": 18,
        "transaction_hash": TX,
        "block_number": int(block["number"], 16),
        "block_hash": block["hash"],
        "log_index": int(transfer["logIndex"], 16),
        "transaction_index": int(transfer["transactionIndex"], 16),
        "receipt_status": "success",
        "block_timestamp_utc": datetime.fromtimestamp(int(block["timestamp"], 16), UTC).isoformat().replace("+00:00", "Z"),
        "outer_transaction_sender": transaction["from"],
        "outer_transaction_target": transaction["to"],
        "governor_proposal_id": "93",
    }
    write_json("normalized-transfer.json", normalized)
    write_json("case.json", {"case_id": "uniswap93", "title": "UNIfication: treasury UNI transfer to dead address", "request": request, "verified_transfer": normalized, "coverage": coverage})

    sources = [
        {
            "source_id": "uniswap-proposal-93",
            "url": "https://vote.uniswapfoundation.org/proposals/93",
            "title": "UNIfication — Uniswap proposal 93",
            "publisher": "Uniswap governance / Agora",
            "locator": "Proposal Spec: first UNI.transfer action; proposal status",
            "excerpt": "UNI.transfer(0xdead, 100_000_000 ether);",
            "summary": "Proposal 93 specifies eight governance calls including a transfer of 100 million UNI to the dead address, protocol-fee configuration, UNI vesting approval, and agreement attestations. Its page presents the proposal as executed.",
            "interpretation": "The specified transfer and successful GovernorBravo execute(93) transaction support a governance-execution explanation for the alert.",
            "observation_note": "The current Agora display showed Executed 11:58 pm Dec 28, 2025 when viewed. The captured block timestamp is Dec 27, 2025 20:33:11 UTC; the display is not used as the chain-time authority.",
        },
        {
            "source_id": "uniswap-unification-blog",
            "url": "https://blog.uniswap.org/unification",
            "title": "UNIfication",
            "publisher": "Uniswap Labs",
            "published_date": "2025-11-10",
            "locator": "Retroactive Burn",
            "excerpt": "We propose a retroactive burn of 100 million UNI from the treasury.",
            "summary": "The joint Labs/Foundation proposal describes the one-time treasury transfer as an estimate of fees that could have accumulated since token launch, alongside enabling protocol fees and UNI burn mechanisms.",
            "interpretation": "The blog supplies the governance rationale; it does not by itself establish the actual receipt or the ERC-20 totalSupply getter change.",
        },
        {
            "source_id": "uniswap-governance-contracts",
            "url": "https://developers.uniswap.org/docs/ecosystem/governance/technical-reference",
            "title": "Uniswap governance Technical Reference",
            "publisher": "Uniswap Labs developer documentation",
            "locator": "Active Governance Contracts",
            "excerpt": "UNI Token: 0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984\nTimelock: 0x1a9C8182C09F50C8318d769245beA52c32BE35BC\nGovernorBravo: 0x408ED6354d4973f66138C91495F2f2FCbd8724C3",
            "summary": "The official Ethereum mainnet contract table identifies the UNI event emitter, the treasury Timelock sender, and the GovernorBravo execution target found in the captured transaction.",
        },
        {
            "source_id": "uniswap-uni-source",
            "url": "https://github.com/Uniswap/governance/blob/master/contracts/Uni.sol",
            "title": "Uniswap governance — contracts/Uni.sol",
            "publisher": "Uniswap official GitHub organization",
            "locator": "transfer and _transferTokens functions",
            "excerpt": "emit Transfer(src, dst, amount);",
            "summary": "The published UNI transfer path debits and credits balances, emits Transfer, and updates delegate votes. It does not assign totalSupply on that path. The captured historical totalSupply getter reads independently establish unchanged values across the two block states.",
            "interpretation": "A dead-address transfer and a decrease in the totalSupply getter are distinct claims.",
        },
    ]
    for source in sources:
        source["observed_on_date_utc"] = "2026-10-06"
        source["recorded_at_utc"] = recorded
        source["retrieval_method"] = "web tool open; short manually selected excerpt retained"
        source["scope"] = "Excerpt snapshot and source pointer; entire page is not included"
        excerpt_path = ROOT / "raw" / f"{source['source_id']}.excerpt.txt"
        excerpt_path.write_text(source["excerpt"], encoding="utf-8")
        source["excerpt_file"] = excerpt_path.relative_to(ROOT).as_posix()
        source["excerpt_sha256"] = hashlib.sha256(excerpt_path.read_bytes()).hexdigest()
    write_json("sources.json", sources)
    verification = {
        "schema_version": "cluetide.fixture-verification.v1",
        "verified_at_utc": recorded,
        "checks": ["chain_id_1", "successful_receipt", "two_provider_receipt_logs_match", "transaction_in_case_block", "receipt_transaction_block_hash_match", "governor_execute_93_input", "bounded_unique_transfer_logs", "subject_outgoing_one_incoming_zero", "amount_uint256_exact", "decimals_18", "total_supply_getters_unchanged", "governor_state_executed", "captured_finalized_anchor_exceeds_window", "three_metadata_queries_pinned_end_block_hash", "metadata_name_symbol_decimals_exact"],
        "receipt_difference": "Tenderly includes blobGasUsed=0x0; dRPC omits that optional field. All receipt logs are identical.",
        "amount_raw": normalized["amount_raw"],
        "total_supply_before_raw": str(int(before, 16)),
        "total_supply_after_raw": str(int(after, 16)),
        "remaining_blockers": [],
        "limitations": ["Provider-reported data, without a cryptographic inclusion or consensus proof.", "Token-state reads are end-of-block reads at 24106377 and 24106378, rather than intra-transaction reads.", "Single-provider successful bounded getLogs response establishes query coverage, not independent evidence that the provider never omitted a log."],
    }
    write_json("verification.json", verification)
    files = []
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and path.name != "fixture-manifest.json" and path.suffix not in {".py", ".ps1"}:
            files.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    write_json("fixture-manifest.json", {"schema_version": "cluetide.fixture-manifest.v1", "recorded_at_utc": recorded, "files": files, "hash_algorithm": "sha256", "integrity_statement": "File hashes detect byte changes; they do not prove the factual truth of source claims."})
    print(f"Verified public snapshot: {len(logs)} UNI Transfer logs; one subject outgoing; zero subject incoming; {len(files)} hashed files.")


if __name__ == "__main__":
    main()
