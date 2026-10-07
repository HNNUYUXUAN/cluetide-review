"""Public evidence schemas. Raw EVM integers never pass through floats."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_UINT256 = (1 << 256) - 1
MAX_SAFE_INTEGER = (1 << 53) - 1
# Engineering demonstration choice: 10 million units for an 18-decimal token.
# It is not trained, calibrated, or an estimate of maliciousness.
DEFAULT_ALERT_THRESHOLD_RAW = str(10_000_000 * 10**18)
MAX_WINDOW_BLOCKS = 2_000
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
HASH_PATTERN = re.compile(r"^0x[0-9a-fA-F]{64}$")


def normalize_address(value: str) -> str:
    if not isinstance(value, str) or not ADDRESS_PATTERN.fullmatch(value):
        raise ValueError("Expected a 20-byte hexadecimal Ethereum address")
    return value.lower()


def normalize_hash(value: str) -> str:
    if not isinstance(value, str) or not HASH_PATTERN.fullmatch(value):
        raise ValueError("Expected a 32-byte hexadecimal hash")
    return value.lower()


def uint256_decimal(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]*", value):
        raise ValueError("uint256 must be a canonical decimal string")
    if int(value) > MAX_UINT256:
        raise ValueError("Value exceeds uint256")
    return value


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InvestigationRequest(PublicModel):
    chain_id: Literal[1] = 1
    address: str
    token_address: str
    from_block: int = Field(ge=0, le=MAX_SAFE_INTEGER, strict=True)
    to_block: int = Field(ge=0, le=MAX_SAFE_INTEGER, strict=True)
    alert_threshold_raw: str = DEFAULT_ALERT_THRESHOLD_RAW
    log_chunk_size: int = Field(default=500, ge=1, le=500, strict=True)

    _address = field_validator("address", "token_address")(normalize_address)
    _threshold = field_validator("alert_threshold_raw")(uint256_decimal)

    @field_validator("chain_id", mode="before")
    @classmethod
    def strict_chain_id(cls, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("chain_id must be an integer")
        return value

    @model_validator(mode="after")
    def bounded_window(self) -> "InvestigationRequest":
        if self.from_block > self.to_block:
            raise ValueError("from_block must not exceed to_block")
        if self.to_block - self.from_block + 1 > MAX_WINDOW_BLOCKS:
            raise ValueError(f"A window may contain at most {MAX_WINDOW_BLOCKS} blocks")
        return self


class BlockAnchor(PublicModel):
    number: int = Field(ge=0, le=MAX_SAFE_INTEGER)
    block_hash: str
    tag: str = "finalized"
    _hash = field_validator("block_hash")(normalize_hash)


class QueryCoverage(PublicModel):
    direction: Literal["incoming", "outgoing"]
    from_block: int
    to_block: int
    status: Literal["complete", "error"]
    observation_id: str
    returned_logs: int = 0
    accepted_logs: int = 0
    rejected_logs: int = 0
    error: str | None = None


class Coverage(PublicModel):
    status: Literal["complete", "empty", "partial", "error"]
    requested_from_block: int
    requested_to_block: int
    planned_queries: int = 0
    completed_queries: int = 0
    queries: list[QueryCoverage] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    statement: str = "Coverage describes returned RPC data within the requested window."


class TransferRecord(PublicModel):
    evidence_id: str
    chain_id: Literal[1] = 1
    token_address: str
    block_number: int = Field(ge=0, le=MAX_SAFE_INTEGER)
    block_hash: str
    transaction_hash: str
    log_index: int = Field(ge=0, le=MAX_SAFE_INTEGER)
    transaction_index: int | None = Field(default=None, ge=0, le=MAX_SAFE_INTEGER)
    from_address: str
    to_address: str
    value_raw: str
    directions: list[Literal["in", "out"]]
    observation_ids: list[str] = Field(default_factory=list)

    _addresses = field_validator("token_address", "from_address", "to_address")(normalize_address)
    _hashes = field_validator("block_hash", "transaction_hash")(normalize_hash)
    _value = field_validator("value_raw")(uint256_decimal)


class TokenMetadata(PublicModel):
    status: Literal["complete", "partial", "unavailable"] = "unavailable"
    decimals: int | None = Field(default=None, ge=0, le=255)
    symbol: str | None = None
    name: str | None = None
    block_anchor: BlockAnchor | None = None
    errors: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)


class Alert(PublicModel):
    alert_id: str
    rule: Literal["large_transfer_raw"] = "large_transfer_raw"
    threshold_raw: str
    observed_value_raw: str
    evidence_ids: list[str]
    explanation: str
    threshold_basis: str = "Configured engineering demonstration threshold; not trained or calibrated."
    interpretation: str = "A rule-triggered investigation lead, not a finding of attack or totalSupply change."
    _values = field_validator("threshold_raw", "observed_value_raw")(uint256_decimal)


class EvidenceSet(PublicModel):
    schema_version: Literal["cluetide-evidence/v1"] = "cluetide-evidence/v1"
    request: InvestigationRequest
    collected_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finalized_anchor: BlockAnchor | None = None
    window_end_anchor: BlockAnchor | None = None
    coverage: Coverage
    transfers: list[TransferRecord] = Field(default_factory=list)
    metadata: TokenMetadata = Field(default_factory=TokenMetadata)
    alerts: list[Alert] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    integrity_statement: str = "Evidence hashes commit to bytes; they do not establish factual truth."
