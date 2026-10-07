"""Assemble the captured Euler public case without network or model calls."""
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
TX = "0xc310a0affe2169d1f6feec1c63dbc7f7c62a887fa48795d327d4d2da2d6b111d"
TOKEN = "0x6b175474e89094c44da98b954eedeac495271d0f"
SUBJECT = "0x27182842e098f60e3d576794a5bffb0777e025d3"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
NOW = datetime.now(UTC).isoformat().replace("+00:00", "Z")


def dump(name, value):
    (ROOT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def result(name):
    response = json.loads((RAW / (name + ".response.json")).read_text(encoding="utf-8"))
    assert response.get("id") == 1 and "error" not in response and response.get("result") is not None
    return response["result"]


def decode_text(word):
    raw = bytes.fromhex(word[2:]); offset = int.from_bytes(raw[:32]); length = int.from_bytes(raw[offset:offset + 32])
    return raw[offset + 32:offset + 32 + length].decode("utf-8")


def source(source_id, url, title, publisher, locator, excerpt, summary, read_scope, **extra):
    filename = "raw/" + source_id + ".excerpt.txt"
    content = (excerpt + "\n").encode("utf-8")
    (ROOT / filename).write_bytes(content)
    return {"source_id": source_id, "url": url, "title": title, "publisher": publisher,
            "category": "incident_postmortem", "locator": locator, "excerpt": excerpt, "summary": summary,
            "observed_on_date_utc": NOW[:10], "recorded_at_utc": NOW,
            "retrieval_method": "Web tool public source read; short attributed excerpt retained",
            "acquisition_status": "short_excerpt_retained", "actual_read_scope": read_scope,
            "excerpt_file": filename, "excerpt_sha256": hashlib.sha256(content).hexdigest(),
            "scope": "Source pointer and short excerpt; entire article or code file is not bundled.", **extra}


receipt, transaction = result("receipt"), result("transaction")
headers = {key: result(key) for key in ("start-block", "case-block", "end-block", "finalized")}
outgoing, incoming = result("logs-out"), result("logs-in")
assert result("chain-id") == "0x1"
assert receipt["transactionHash"] == transaction["hash"] == TX and receipt["status"] == "0x1"
assert int(receipt["blockNumber"], 16) == 16817996
assert receipt["blockHash"] == transaction["blockHash"] == headers["case-block"]["hash"]
assert TX == headers["case-block"]["transactions"][int(receipt["transactionIndex"], 16)]
assert headers["case-block"]["parentHash"] == headers["start-block"]["hash"]
assert headers["end-block"]["parentHash"] == headers["case-block"]["hash"]
assert int(headers["finalized"]["number"], 16) > 16817997
assert len(outgoing) == 1 and len(incoming) == 2
normalized = []
for direction, logs in (("out", outgoing), ("in", incoming)):
    for log in logs:
        assert log["address"] == TOKEN and log["topics"][0] == TRANSFER
        assert log["transactionHash"] == TX and log["blockHash"] == receipt["blockHash"] and log["removed"] is False
        assert log in receipt["logs"]
        sender, receiver = "0x" + log["topics"][1][-40:], "0x" + log["topics"][2][-40:]
        assert (sender if direction == "out" else receiver) == SUBJECT
        normalized.append({"chain_id": 1, "token_address": TOKEN, "from_address": sender, "to_address": receiver,
            "value_raw": str(int(log["data"], 16)), "block_number": int(log["blockNumber"], 16),
            "block_hash": log["blockHash"], "transaction_hash": TX, "log_index": int(log["logIndex"], 16),
            "transaction_index": int(log["transactionIndex"], 16), "directions": [direction]})
normalized.sort(key=lambda item: item["log_index"])
assert outgoing[0]["data"] == "0x000000000000000000000000000000000000000000202e5991bf52e5118fbbc6"
assert int(result("metadata-decimals"), 16) == 18
assert decode_text(result("metadata-symbol")) == "DAI" and decode_text(result("metadata-name")) == "Dai Stablecoin"
captures = [json.loads((ROOT / filename).read_text(encoding="utf-8")) for filename in ("capture-initial.json", "capture-window.json", "capture-state.json")]
provenance = {entry["name"]: entry for capture in captures for entry in capture["observations"]}
sources = [
    source("euler-labs-recovery-retrospective", "https://www.euler.finance/blog/war-peace-behind-the-scenes-of-eulers-240m-exploit-recovery",
        "War & Peace: Behind the Scenes of Euler's $240M Exploit Recovery", "Euler Labs",
        "March 13th; WTF is the donateToReserves function?; The exploit in real-time (first exploit transaction hyperlink)",
        "the function lacked a health check", "Euler Labs links this first exploit transaction and describes the missing health check, self-liquidation and subsequent multi-asset incident. The selected case captures one DAI transaction, not the full incident or recovery.",
        "March 13th, missing-health-check explanation and first-exploit chronology; later MEV explanation consulted for actor distinction.",
        published_date="2024-01-10", linked_transaction_hash=TX, event_time_utc="2023-03-13T08:50:59Z"),
    source("omniscia-euler-postmortem", "https://medium.com/@omniscia.io/euler-finance-incident-post-mortem-1ce077c28454",
        "Euler Finance Incident Post-Mortem", "Omniscia",
        "Vulnerability Analysis; Core Problem; Attack Scenario; Sources items 6-9",
        "Acquire a 30m DAI flash-loan from AAVE V2", "The author describes the DAI flash-loan, collateral donation and self-liquidation sequence, and directly identifies this transaction and three involved contracts.",
        "Vulnerability Analysis through Sources, including the transaction/address links and the date inconsistency in Attack Scenario.",
        published_date="2023-03-13", linked_transaction_hash=TX,
        source_quality_note="Attack Scenario's date text says 2023-02-01 06:29:18 UTC, conflicting with its incident date and Euler's chronology. The captured Ethereum block timestamp is used for this case's chain time.",
        actor_addresses={"primary_contract": "0xebc29199c817dc47ba12e3f86102564d640cbf99", "violator": "0x583c21631c48d442b5c0e605d624f54a0b366c72", "liquidator": "0xa0b3ee897f233f385e5d61086c32685257d4f12b"}),
    source("euler-v1-donation-code", "https://github.com/euler-legacy-xyz/euler-contracts/blob/fa9398728165676a5666939d8c34a7578d8e1919/contracts/modules/EToken.sol#L356-L386",
        "Euler V1 EToken.sol at fa939872", "Euler legacy GitHub organization",
        "contracts/modules/EToken.sol lines 356-386, donateToReserves; file SPDX header",
        "function donateToReserves(uint subAccountId, uint amount) external nonReentrant {",
        "The consulted function changes the account balance and reserves and emits events; its body contains no checkLiquidity call. This is fixed-version published code, not independently matched deployed bytecode or an executed exploit reproduction.",
        "SPDX header and complete donateToReserves function at source lines 356-386.",
        source_version="fa9398728165676a5666939d8c34a7578d8e1919", license_spdx="GPL-2.0-or-later"),
    source("euler-v1-addresses", "https://docs-v1.euler.finance/euler-protocol/addresses",
        "Euler V1 Addresses", "Euler Finance documentation", "Mainnet contract table, Euler row",
        "Euler 0x27182842E098f60e3D576794A5bFFb0777E025d3",
        "The indexed official V1 mainnet address table identifies the subject address as Euler. Its live URL redirected to the current V2 documentation when opened, so the retained observation has indexed-excerpt scope.",
        "Search provider's indexed official V1 mainnet address table extract only; live target redirects to current docs.",
        acquisition_status="indexed_official_excerpt_retained", retrieval_method="Web search indexed official source extract; live open redirected to https://docs.euler.finance/", live_retrieval_status="redirected_to_v2_docs"),
]
dump("sources.json", sources)
dump("normalized-transfers.json", normalized)
coverage = {"status": "complete_query_response", "from_block": 16817995, "to_block": 16817997,
    "subject_outgoing_count": 1, "subject_incoming_count": 2, "missing_ranges": [],
    "scope": "Two successful directional DAI Transfer filters for the Euler subject in an inclusive three-block window.",
    "finality_basis": "Provider-reported finalized anchor, plus adjacent local window header links; no independent consensus proof."}
snapshot = {"schema_version": "cluetide.public-rpc-snapshot/v1", "mode": "public_cache", "recorded_at_utc": NOW,
    "request": {"chain_id": 1, "subject_address": SUBJECT, "token_address": TOKEN, "start_block": 16817995,
                "end_block": 16817997, "transaction_hash": TX}, "eth_chainId": result("chain-id"),
    "finalized": headers["finalized"], "start_block": headers["start-block"], "end_block": headers["end-block"],
    "case_block": headers["case-block"], "logs_out": outgoing, "logs_in": incoming,
    "receipt": receipt, "transaction": transaction,
    "token_state": {"decimals": result("metadata-decimals"), "symbol": result("metadata-symbol"), "name": result("metadata-name"),
        "metadata_block_number": 16817997, "metadata_block_hash": headers["end-block"]["hash"],
        "metadata_block_selection": "EIP-1898", "metadata_require_canonical": True,
        "totalSupply_before": result("state-total-supply-before"), "totalSupply_before_block_number": 16817995,
        "totalSupply_before_block_hash": headers["start-block"]["hash"],
        "totalSupply_after": result("state-total-supply-after"), "totalSupply_after_block_number": 16817996,
        "totalSupply_after_block_hash": headers["case-block"]["hash"]},
    "coverage": coverage, "provenance": provenance,
    "integrity_statement": "Snapshot hashes preserve bytes, not factual truth; public RPC observations depend on the provider."}
dump("rpc.json", snapshot)
dump("case.json", {"case_id": "euler-20230313", "request": snapshot["request"], "transaction_status": "success",
    "transaction_block": 16817996, "transaction_block_hash": receipt["blockHash"],
    "block_timestamp_utc": datetime.fromtimestamp(int(headers["case-block"]["timestamp"],16),UTC).isoformat().replace("+00:00","Z"),
    "coverage": coverage, "transfers": normalized,
    "investigation_boundary": "A single historical DAI transaction related to Euler V1; observed transfers do not by themselves reconstruct all internal calls, debt health, total incident loss or recovery."})
dump("verification.json", {"schema_version": "cluetide.fixture-verification.v1", "verified_at_utc": NOW,
    "checks": ["chain_id_1", "successful_receipt", "transaction_receipt_block_hash_match", "transaction_in_block", "adjacent_window_header_links", "provider_finalized_height_exceeds_window", "directional_logs_match_receipt", "subject_one_outgoing_two_incoming", "exact_uint256_strings", "metadata_pinned_end_block_hash"],
    "source_linked_transaction_hash": TX, "outgoing_raw": normalized[-1]["value_raw"],
    "incoming_raw": "30000000000000000000000000", "net_transfer_flow_raw": "-8904507348306697267428294",
    "read_attempts": sum(capture["read_attempts"] for capture in captures) + 4,
    "successful_reads": 14, "initial_local_transport_failures": 4, "remaining_blockers": [],
    "total_supply_before_raw": str(int(result("state-total-supply-before"),16)),
    "total_supply_after_raw": str(int(result("state-total-supply-after"),16)),
    "limitations": ["One provider's successful responses; no independent consensus or inclusion proof.", "No trace RPC or exploit execution was performed; internal debt health is source interpretation.", "DAI net flow is a transfer-derived subject flow, not token balance, USD profit or aggregate incident loss.", "The V1 address page was obtained as an indexed official extract; its live URL now redirects."]})
files = []
for path in sorted(ROOT.rglob("*")):
    if path.is_file() and path.name != "fixture-manifest.json" and path.suffix not in {".py", ".ps1", ".pyc"} and "__pycache__" not in path.parts:
        payload = path.read_bytes()
        files.append({"path": path.relative_to(ROOT).as_posix(), "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
dump("fixture-manifest.json", {"schema_version": "cluetide.fixture-manifest.v1", "recorded_at_utc": NOW, "files": files,
    "scope": "Retained public evidence bytes; collection/assembly scripts and this manifest excluded.",
    "integrity_statement": "Hashes detect changes to retained bytes, not source authenticity or factual truth."})
print(json.dumps({"status": "verified", "case_id": "euler-20230313", "evidence_files": len(files), "transfer_count": len(normalized), "model_requests": 0, "transactions_signed_or_broadcast": 0}))
