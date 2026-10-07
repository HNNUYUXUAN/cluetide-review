"""Deterministic representative transaction selection from collected evidence."""

from __future__ import annotations

from typing import Any, NamedTuple

from .schemas import EvidenceSet, normalize_hash, uint256_decimal


SCOPE_SCHEMA_VERSION = "cluetide-investigation-scope/v1"
SCOPE_STATEMENT = (
    "Agent 复核聚焦选定的代表交易；窗口内其余交易只记录确定性采集证据。"
    "调查完成状态依据后续交易或回执工具的成功读取记录更新。"
)


class _TransferCandidate(NamedTuple):
    evidence_id: str
    transaction_hash: str
    amount_raw: int


def _records(evidence: dict[str, Any], field: str) -> list[dict[str, Any]]:
    records = evidence.get(field, [])
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise ValueError(f"{field} must be a list of evidence records")
    return records


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a nonempty string")
    return value


def select_investigation_transaction(
    evidence: EvidenceSet | dict[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    """Choose the largest alerted Transfer, then the largest observed Transfer.

    Equal amounts use evidence_id lexical order. Alert references define the
    candidate pool, and canonical uint256 Transfer values determine ranking.
    The scope lists every observed transaction and starts with all transactions
    awaiting investigation. Selected evidence and alerts cover the selected
    transaction's complete set of collected Transfer records.
    """
    if isinstance(evidence, EvidenceSet):
        payload = evidence.model_dump(mode="json")
    elif isinstance(evidence, dict):
        payload = evidence
    else:
        raise TypeError("evidence must be an EvidenceSet or a dictionary")

    transfers: list[_TransferCandidate] = []
    transfer_ids: dict[str, tuple[str, int]] = {}
    for record in _records(payload, "transfers"):
        evidence_id = _identifier(record.get("evidence_id"), "Transfer evidence_id")
        transaction_hash = normalize_hash(record.get("transaction_hash"))
        amount = int(uint256_decimal(record.get("value_raw")))
        identity = (transaction_hash, amount)
        if evidence_id in transfer_ids and transfer_ids[evidence_id] != identity:
            raise ValueError("Transfer evidence_id must identify one transaction and raw amount")
        transfer_ids[evidence_id] = identity
        transfers.append(_TransferCandidate(evidence_id, transaction_hash, amount))

    alerts: list[tuple[str, set[str]]] = []
    for record in _records(payload, "alerts"):
        alert_id = _identifier(record.get("alert_id"), "Alert alert_id")
        references = record.get("evidence_ids")
        if not isinstance(references, list):
            raise ValueError("Alert evidence_ids must be a list")
        evidence_ids = {_identifier(value, "Alert evidence_id") for value in references}
        alerts.append((alert_id, evidence_ids))

    alerted_ids = {evidence_id for _, references in alerts for evidence_id in references}
    alerted_transfers = [record for record in transfers if record.evidence_id in alerted_ids]
    candidates = alerted_transfers or transfers
    selected = min(candidates, key=lambda record: (-record.amount_raw, record.evidence_id)) if candidates else None
    selected_hash = selected.transaction_hash if selected else None
    observed_hashes = sorted({record.transaction_hash for record in transfers})
    alerted_hashes = sorted({record.transaction_hash for record in alerted_transfers})
    selected_ids = {record.evidence_id for record in transfers if record.transaction_hash == selected_hash}

    if alerted_transfers:
        basis = "largest_alerted_transfer"
        reason = (
            "选取告警引用的 Transfer 中原始金额最大的代表事件及其交易；"
            "金额相同时按 evidence_id 字典序确定代表事件。"
            "告警阈值是原始单位的工程规则，告警用于提供调查线索。"
        )
    elif transfers:
        basis = "largest_observed_transfer"
        reason = (
            "窗口内的 Transfer 未被现有告警引用；选取原始金额最大的代表事件及其交易，"
            "金额相同时按 evidence_id 字典序确定代表事件。"
        )
    else:
        basis = "no_transfer"
        reason = "采集窗口没有 Transfer 记录，因此没有代表交易可供 Agent 复核。"

    scope = {
        "schema_version": SCOPE_SCHEMA_VERSION,
        "selected_transaction_hash": selected_hash,
        "investigated_transaction_hash": None,
        "uninvestigated_transaction_hashes": list(observed_hashes),
        "observed_transaction_hashes": observed_hashes,
        "alerted_transaction_hashes": alerted_hashes,
        "selected_alert_ids": sorted({alert_id for alert_id, references in alerts if references & selected_ids}),
        "selected_evidence_ids": sorted(selected_ids),
        "selection_basis": basis,
        "selection_reason": reason,
        "statement": SCOPE_STATEMENT,
    }
    return selected_hash, scope
