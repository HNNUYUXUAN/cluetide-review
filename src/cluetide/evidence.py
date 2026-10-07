"""Deterministic ERC-20 decoding and explainable investigation rules."""

from __future__ import annotations

import re
from typing import Any, Literal

from .rpc import parse_quantity
from .schemas import Alert, InvestigationRequest, TransferRecord, normalize_address, normalize_hash


TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
WORD = re.compile(r"^0x[0-9a-fA-F]{64}$")


def address_topic(address: str) -> str:
    return "0x" + "0" * 24 + normalize_address(address)[2:]


def _topic_address(topic: Any) -> str:
    normalize_hash(topic)
    if topic[2:26] != "0" * 24:
        raise ValueError("Indexed address has nonzero padding")
    return normalize_address("0x" + topic[-40:])


def decode_transfer_log(log: dict[str, Any], request: InvestigationRequest, *,
                        direction: Literal["incoming", "outgoing"], observation_id: str,
                        query_from: int | None = None, query_to: int | None = None) -> TransferRecord:
    if not isinstance(log, dict):
        raise ValueError("Log is not an object")
    if normalize_address(log.get("address")) != request.token_address:
        raise ValueError("Log contract is outside the requested token")
    topics = log.get("topics")
    if not isinstance(topics, list) or len(topics) != 3 or str(topics[0]).lower() != TRANSFER_TOPIC:
        raise ValueError("Log is not a standard ERC-20 Transfer")
    sender, receiver = _topic_address(topics[1]), _topic_address(topics[2])
    if direction == "outgoing" and sender != request.address:
        raise ValueError("Returned log does not match the outgoing filter")
    if direction == "incoming" and receiver != request.address:
        raise ValueError("Returned log does not match the incoming filter")
    data = log.get("data")
    if not isinstance(data, str) or not WORD.fullmatch(data):
        raise ValueError("Transfer value must be one uint256 ABI word")
    removed = log.get("removed")
    if removed is not None and removed is not False:
        raise ValueError("Removed or invalid-removed logs cannot establish finalized evidence")
    block_number = parse_quantity(log.get("blockNumber"))
    start = request.from_block if query_from is None else query_from
    end = request.to_block if query_to is None else query_to
    if not start <= block_number <= end:
        raise ValueError("Returned log is outside its requested block window")
    block_hash = normalize_hash(log.get("blockHash"))
    tx_hash = normalize_hash(log.get("transactionHash"))
    log_index = parse_quantity(log.get("logIndex"))
    tx_index = parse_quantity(log["transactionIndex"]) if log.get("transactionIndex") is not None else None
    directions: list[Literal["in", "out"]] = []
    if receiver == request.address:
        directions.append("in")
    if sender == request.address:
        directions.append("out")
    return TransferRecord(
        evidence_id=f"transfer:{request.chain_id}:{block_hash}:{tx_hash}:{log_index}",
        chain_id=request.chain_id, token_address=request.token_address,
        block_number=block_number, block_hash=block_hash, transaction_hash=tx_hash,
        log_index=log_index, transaction_index=tx_index, from_address=sender,
        to_address=receiver, value_raw=str(int(data, 16)), directions=directions,
        observation_ids=[observation_id],
    )


def transfer_identity(record: TransferRecord) -> tuple[int, str, str, int]:
    return record.chain_id, record.block_hash, record.transaction_hash, record.log_index


def transfer_payload(record: TransferRecord) -> tuple[Any, ...]:
    return (record.token_address, record.block_number, record.transaction_index,
            record.from_address, record.to_address, record.value_raw)


def build_alerts(transfers: list[TransferRecord], request: InvestigationRequest) -> list[Alert]:
    threshold = int(request.alert_threshold_raw)
    return [Alert(
        alert_id=f"large-transfer:{record.evidence_id}",
        threshold_raw=request.alert_threshold_raw, observed_value_raw=record.value_raw,
        evidence_ids=[record.evidence_id],
        explanation=(f"Transfer raw value {record.value_raw} meets configured threshold "
                     f"{request.alert_threshold_raw} for this ERC-20 contract. "
                     "Threshold units are raw token units; decimals are optional display metadata."),
    ) for record in transfers if int(record.value_raw) >= threshold]


def summarize_transfers(transfers: list[TransferRecord]) -> dict[str, str | int]:
    """Self-transfers contribute to both legs and have zero net transfer flow."""
    incoming = sum(int(record.value_raw) for record in transfers if "in" in record.directions)
    outgoing = sum(int(record.value_raw) for record in transfers if "out" in record.directions)
    return {"transfer_count": len(transfers), "incoming_raw": str(incoming),
            "outgoing_raw": str(outgoing), "net_transfer_flow_raw": str(incoming - outgoing)}
