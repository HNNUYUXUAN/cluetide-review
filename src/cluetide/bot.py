"""BOT registry calldata preparation and bounded, getter-based verification.

Transactions are unsigned public plans for a user-controlled wallet. Receipt
verification checks canonical membership at read time and a stated confirmation
count; it does not assert finality or certify the committed interpretation.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

import httpx
from eth_abi import decode, encode
from eth_utils import keccak
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .bundles import BundleValidationError, canonical_json_bytes, import_bundle, manifest_sha256
from .registry import DECISIONS, DECISION_NAMES, case_id_bytes


MAX_SAFE_INTEGER = 2**53 - 1
MAX_UINT256 = 2**256 - 1
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
_HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_HEX_BYTES = re.compile(r"0x(?:[0-9a-fA-F]{2})*\Z")
_QUANTITY = re.compile(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)\Z")
_DECIMAL = re.compile(r"[1-9][0-9]*\Z")
NETWORKS = {
    968: {"chain_id": 968, "chain_id_hex": "0x3c8", "name": "BOT Chain Testnet",
          "rpc_url": "https://rpc.bohr.life", "explorer_url": "https://scan.bohr.life",
          "native_currency": {"name": "BOT", "symbol": "BOT", "decimals": 18},
          "native_currency_status": "official_testnet_metadata", "warnings": []},
    677: {"chain_id": 677, "chain_id_hex": "0x2a5", "name": "BOT Chain Mainnet",
          "rpc_url": "https://rpc.botchain.ai", "explorer_url": "https://scan.botchain.ai",
          "native_currency": {"name": "BOT", "symbol": "BOT", "decimals": 18},
          "native_currency_status": "inferred_from_official_testnet_and_client_units",
          "warnings": ["mainnet_currency_metadata_inferred"]},
}
RPC_METHODS = frozenset({"eth_chainId", "eth_blockNumber", "eth_getBlockByNumber",
    "eth_gasPrice", "eth_getBalance", "eth_estimateGas", "eth_getCode",
    "eth_getTransactionReceipt", "eth_getTransactionByHash", "eth_call"})
GETTERS = {
    "getCase": (["bytes32"], ["(address,uint256,uint256,uint256,uint64)"]),
    "getVersion": (["uint256"], ["(bytes32,uint256,uint256,address,bytes32,string,string,uint64)"]),
    "getReview": (["uint256"], ["(uint256,bytes32,uint256,bytes32,address,uint8,bytes32,string,uint64)"]),
    "getVersionId": (["bytes32", "uint256"], ["uint256"]),
    "getReviewId": (["bytes32", "uint256"], ["uint256"]),
}
READ_PAGE_SIZE = 3
REVIEW_SCAN_LIMIT = 6
_ALLOWED_REVERT = object()


def _observed_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _finite_json_constant(_):
    raise ValueError("invalid_json_constant")


class BotError(ValueError):
    """A stable public error code, without provider response bodies."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _address(value: Any) -> str:
    if not isinstance(value, str) or not _ADDRESS.fullmatch(value) or int(value, 16) == 0:
        raise ValueError("invalid_address")
    return value.lower()


def _identifier(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_SAFE_INTEGER:
        return str(value)
    if isinstance(value, str) and len(value) <= 78 and _DECIMAL.fullmatch(value) and int(value) <= MAX_UINT256:
        return value
    raise ValueError("invalid_onchain_identifier")


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain_id: Literal[677, 968]
    action: Literal["deploy", "create_case", "append_version", "add_review"]
    account: str
    contract_address: str | None = None
    case_id: str | None = Field(default=None, min_length=1, max_length=512)
    local_version_id: int | None = Field(default=None, ge=1, le=MAX_SAFE_INTEGER, strict=True)
    onchain_version_id: str | None = None
    local_review_id: int | None = Field(default=None, ge=1, le=MAX_SAFE_INTEGER, strict=True)
    evidence_uri: str | None = None

    @field_validator("chain_id", mode="before")
    @classmethod
    def strict_chain(cls, value):
        if type(value) is not int:
            raise ValueError("invalid_chain_id")
        return value

    _account = field_validator("account")(_address)

    @field_validator("contract_address", mode="before")
    @classmethod
    def valid_contract(cls, value):
        return None if value is None else _address(value)

    @field_validator("onchain_version_id", mode="before")
    @classmethod
    def valid_version(cls, value):
        return None if value is None else _identifier(value)

    @field_validator("case_id")
    @classmethod
    def valid_case(cls, value):
        if value is not None:
            case_id_bytes(value)
        return value

    @field_validator("evidence_uri")
    @classmethod
    def valid_uri(cls, value):
        if value is None:
            return value
        if len(value.encode("utf-8")) > 512:
            raise ValueError("invalid_evidence_uri")
        canonical_json_bytes(value)
        if value:
            parsed = urlsplit(value)
            if parsed.username or parsed.password or parsed.fragment or parsed.query:
                raise ValueError("invalid_evidence_uri")
            if parsed.scheme not in ("https", "ipfs", "ar", "urn") and not value.startswith("/api/"):
                raise ValueError("invalid_evidence_uri")
            if parsed.scheme == "https":
                host = parsed.hostname
                if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                    raise ValueError("public_evidence_host_required")
                try:
                    if not ipaddress.ip_address(host).is_global:
                        raise ValueError("public_evidence_host_required")
                except ValueError as exc:
                    if str(exc) == "public_evidence_host_required":
                        raise
                    if "." not in host:
                        raise ValueError("public_evidence_host_required") from None
        return value

    @model_validator(mode="after")
    def action_fields(self):
        if self.action == "deploy":
            if any(value is not None for value in (self.contract_address, self.case_id,
                    self.local_version_id, self.onchain_version_id, self.local_review_id, self.evidence_uri)):
                raise ValueError("deployment_fields_mismatch")
        else:
            if self.contract_address is None or self.case_id is None or self.local_version_id is None:
                raise ValueError("commitment_fields_required")
            if self.action == "create_case" and self.onchain_version_id is not None:
                raise ValueError("create_parent_mismatch")
            if self.action in ("append_version", "add_review") and self.onchain_version_id is None:
                raise ValueError("onchain_version_required")
            if (self.action == "add_review") != (self.local_review_id is not None):
                raise ValueError("review_fields_mismatch")
        return self


class PrepareSubmission(PrepareRequest):
    """Ephemeral readiness proof accompanies the stable public intent."""

    review_preflight_cursor: str | None = Field(default=None, min_length=1, max_length=128, strict=True)

    @model_validator(mode="after")
    def review_cursor_action(self):
        if self.review_preflight_cursor is not None and self.action != "add_review":
            raise ValueError("review_preflight_action_required")
        return self


class ReviewPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prepared: PrepareRequest
    cursor: str | None = Field(default=None, min_length=1, max_length=128, strict=True)

    @model_validator(mode="after")
    def review_action(self):
        if self.prepared.action != "add_review":
            raise ValueError("review_preflight_action_required")
        return self


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain_id: Literal[677, 968]
    transaction_hash: str
    prepared: PrepareRequest
    minimum_confirmations: int = Field(default=3, ge=1, le=100, strict=True)

    @field_validator("chain_id", mode="before")
    @classmethod
    def strict_chain(cls, value):
        return PrepareRequest.strict_chain(value)

    @field_validator("transaction_hash")
    @classmethod
    def valid_hash(cls, value):
        if not _HASH.fullmatch(value) or int(value, 16) == 0:
            raise ValueError("invalid_transaction_hash")
        return value.lower()

    @model_validator(mode="after")
    def same_chain(self):
        if self.chain_id != self.prepared.chain_id:
            raise ValueError("prepared_chain_mismatch")
        return self


class ReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain_id: Literal[677, 968]
    contract_address: str
    case_id: str = Field(min_length=1, max_length=512)
    version_id: str | None = None
    review_id: str | None = None
    version_offset: int = Field(default=0, ge=0, le=MAX_SAFE_INTEGER, strict=True)
    review_offset: int = Field(default=0, ge=0, le=MAX_SAFE_INTEGER, strict=True)

    read_block_number: str | None = None
    read_block_hash: str | None = None

    @field_validator("read_block_number", mode="before")
    @classmethod
    def valid_block_number(cls, value):
        if value is None:
            return value
        if (not isinstance(value, str) or len(value) > 78
                or re.fullmatch(r"0|[1-9][0-9]*", value) is None or int(value) > MAX_UINT256):
            raise ValueError("invalid_read_block_number")
        return value

    @field_validator("read_block_hash")
    @classmethod
    def valid_block_hash(cls, value):
        if value is not None and (not _HASH.fullmatch(value) or int(value, 16) == 0):
            raise ValueError("invalid_read_block_hash")
        return value.lower() if value is not None else None

    @model_validator(mode="after")
    def paired_anchor(self):
        if (self.read_block_number is None) != (self.read_block_hash is None):
            raise ValueError("read_block_anchor_required")
        return self

    _contract = field_validator("contract_address")(_address)

    @field_validator("chain_id", mode="before")
    @classmethod
    def strict_chain(cls, value):
        return PrepareRequest.strict_chain(value)

    @field_validator("case_id")
    @classmethod
    def valid_case(cls, value):
        return PrepareRequest.valid_case(value)

    @field_validator("version_id", "review_id", mode="before")
    @classmethod
    def valid_version(cls, value):
        return PrepareRequest.valid_version(value)


@dataclass(frozen=True)
class Artifact:
    bytecode: str
    runtime: str
    metadata: dict

    @classmethod
    def load(cls, artifact_path: Path, source_path: Path):
        try:
            payload = artifact_path.read_bytes()
            artifact = json.loads(payload)
            source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            creation, runtime = artifact["bytecode"].lower(), artifact["deployedBytecode"].lower()
            if (artifact["contractName"] != "ClueTideRegistry" or artifact["evmVersion"] != "paris"
                    or artifact["sourceSha256"] != source_hash or not creation.startswith("0x")
                    or len(creation) < 4 or not runtime.startswith("0x") or not _HEX_BYTES.fullmatch(creation)
                    or not _HEX_BYTES.fullmatch(runtime) or len(runtime) < 4):
                raise ValueError
            metadata = {"contract_name": artifact["contractName"], "compiler": artifact["compiler"],
                "evm_version": artifact["evmVersion"], "source_sha256": source_hash,
                "artifact_sha256": hashlib.sha256(payload).hexdigest(),
                "bytecode_sha256": hashlib.sha256(bytes.fromhex(creation[2:])).hexdigest(),
                "runtime_sha256": hashlib.sha256(bytes.fromhex(runtime[2:])).hexdigest()}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise BotError("artifact_integrity_error") from None
        return cls(creation, runtime, metadata)


def _quantity(value: Any) -> int:
    if not isinstance(value, str) or not _QUANTITY.fullmatch(value) or len(value) > 66:
        raise BotError("invalid_rpc_quantity")
    return int(value, 16)


def _hash(value: Any) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise BotError("invalid_rpc_hash")
    return value.lower()


def _data(value: Any) -> str:
    if not isinstance(value, str) or not _HEX_BYTES.fullmatch(value):
        raise BotError("invalid_rpc_data")
    return value.lower()


def _calldata(name: str, types: list[str], values: list) -> str:
    return "0x" + (keccak(text=name + "(" + ",".join(types) + ")")[:4] + encode(types, values)).hex()


def _revert_data(error: dict) -> str | None:
    value = error.get("data")
    for _ in range(3):
        if isinstance(value, str) and _HEX_BYTES.fullmatch(value):
            return value.lower()
        if not isinstance(value, dict):
            return None
        value = value.get("data", value.get("result"))
    return None


class BotRPC:
    """A fixed-network request budget, bounded replies, and sanitized errors."""

    def __init__(self, chain_id: int, client: httpx.Client, *, max_calls=20, deadline_seconds=60):
        if chain_id not in NETWORKS or type(chain_id) is not int:
            raise BotError("unsupported_chain")
        if (type(max_calls) is not int or not 1 <= max_calls <= 20
                or type(deadline_seconds) not in (int, float) or not 1 <= deadline_seconds <= 60):
            raise BotError("invalid_rpc_limits")
        self.chain_id, self.client = chain_id, client
        self.calls, self.max_calls = 0, max_calls
        self.deadline = time.monotonic() + deadline_seconds

    def request(self, method: str, params: list, *, allowed_revert: str | None = None):
        if method not in RPC_METHODS:
            raise BotError("forbidden_rpc_method")
        remaining = self.deadline - time.monotonic()
        if self.calls >= self.max_calls or remaining <= 0:
            raise BotError("rpc_limit_reached")
        self.calls += 1
        try:
            with self.client.stream("POST", NETWORKS[self.chain_id]["rpc_url"],
                json={"jsonrpc": "2.0", "id": self.calls, "method": method, "params": params},
                timeout=min(8.0, remaining)) as response:
                response.raise_for_status()
                payload = bytearray()
                for chunk in response.iter_bytes(chunk_size=16384):
                    if time.monotonic() >= self.deadline:
                        raise BotError("rpc_limit_reached")
                    payload.extend(chunk)
                    if len(payload) > 262144:
                        raise BotError("rpc_response_too_large")
                try:
                    body = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_finite_json_constant)
                except (ValueError, UnicodeError, RecursionError):
                    raise BotError("invalid_rpc_response") from None
        except BotError:
            raise
        except Exception:
            raise BotError("rpc_transport_error") from None
        if (not isinstance(body, dict) or body.get("jsonrpc") != "2.0"
                or type(body.get("id")) is not int or body["id"] != self.calls):
            raise BotError("invalid_rpc_response")
        if ("result" in body) == ("error" in body):
            raise BotError("invalid_rpc_response")
        if "error" in body:
            error = body["error"]
            if (not isinstance(error, dict) or type(error.get("code")) is not int
                    or not isinstance(error.get("message"), str)):
                raise BotError("invalid_rpc_response")
            if allowed_revert and _revert_data(error) == allowed_revert:
                return _ALLOWED_REVERT
            raise BotError("rpc_error")
        return body["result"]

    def verify_chain(self):
        if _quantity(self.request("eth_chainId", [])) != self.chain_id:
            raise BotError("chain_id_mismatch")

    def code(self, address: str, artifact: Artifact, block="latest"):
        code = _data(self.request("eth_getCode", [address, block]))
        if code != artifact.runtime:
            raise BotError("contract_runtime_mismatch")

    def getter(self, address: str, name: str, argument, block: str, *, allow_missing=False):
        if name not in GETTERS:
            raise BotError("forbidden_getter")
        inputs, outputs = GETTERS[name]
        arguments = list(argument) if len(inputs) > 1 else [argument]
        result = self.request("eth_call", [{"to": address, "data": _calldata(name, inputs, arguments)}, block],
            allowed_revert="0x" + keccak(text="UnknownCase()")[:4].hex() if allow_missing and name == "getCase" else None)
        if result is _ALLOWED_REVERT and allow_missing:
            return None
        try:
            values = decode(outputs, bytes.fromhex(_data(result)[2:]))[0]
        except Exception:
            raise BotError("invalid_getter_response") from None
        if name in ("getVersionId", "getReviewId"):
            if values == 0:
                raise BotError("invalid_getter_response")
            return str(values)
        if name == "getCase":
            names = ("author", "head_version_id", "version_count", "review_count", "created_at")
        elif name == "getVersion":
            names = ("case_id_hex", "version_id", "parent_version_id", "author", "content_hash",
                     "schema_version", "evidence_uri", "created_at")
        else:
            names = ("review_id", "case_id_hex", "version_id", "content_hash", "reviewer", "decision",
                     "review_hash", "review_uri", "created_at")
        record = dict(zip(names, values))
        for key, value in record.items():
            if isinstance(value, bytes):
                record[key] = ("0x" if key == "case_id_hex" else "") + value.hex()
            elif isinstance(value, int):
                record[key] = str(value)
        if name == "getReview":
            record["decision"] = DECISION_NAMES.get(int(record["decision"]), "unknown")
        return record


def _rpc_unavailable(error: BotError) -> bool:
    return (error.code.startswith(("rpc_", "invalid_rpc_"))
            or error.code in {"invalid_getter_response", "invalid_receipt_status", "invalid_receipt_logs",
                              "bot_rpc_unavailable"})


@dataclass
class ReviewCursor:
    token: str
    fingerprint: str
    touched: float
    lock: Any = field(default_factory=threading.Lock)
    block_number: int | None = None
    block_hash: str | None = None
    total_count: int = 0
    scanned_count: int = 0
    existing_review_id: str | None = None
    invalid: bool = False

    def result(self) -> dict:
        return {"cursor": self.token,
                "status": "duplicate" if self.existing_review_id else
                          "ready" if self.scanned_count == self.total_count else "scanning",
                "scanned_count": str(self.scanned_count), "total_count": str(self.total_count),
                "read_block_number": str(self.block_number), "read_block_hash": self.block_hash,
                "existing_review_id": self.existing_review_id}


class BotService:
    """Prepare saved-case commitments and verify wallet-submitted transactions."""

    def __init__(self, required: Callable[[str], dict], *, artifact_path: Path | None = None,
                 source_path: Path | None = None, client_factory: Callable[[], httpx.Client] | None = None):
        root = Path(__file__).resolve().parents[2]
        self.required = required
        self._review_cursors: dict[str, ReviewCursor] = {}
        self._review_cursors_lock = threading.Lock()
        self.artifact_path = artifact_path or root / "contracts/artifacts/ClueTideRegistry.json"
        self.source_path = source_path or root / "contracts/ClueTideRegistry.sol"
        self.client_factory = client_factory or (lambda: httpx.Client(timeout=8, trust_env=False, follow_redirects=False))

    def artifact(self) -> Artifact:
        return Artifact.load(self.artifact_path, self.source_path)

    def networks(self) -> dict:
        return {"networks": copy.deepcopy(list(NETWORKS.values())), "artifact": self.artifact().metadata,
                "mode": "wallet_signed"}

    def _saved(self, request: PrepareRequest) -> tuple[dict, dict, Any]:
        document = self.required(request.case_id)
        if not isinstance(document, dict) or document.get("id") != request.case_id:
            raise BotError("saved_case_mismatch")
        versions = document.get("versions", [])
        version = next((item for item in versions if item.get("version_id") == request.local_version_id), None)
        if version is None or version.get("case_id") != request.case_id:
            raise BotError("saved_version_mismatch")
        try:
            bundle = import_bundle(Path(document["bundle_files"][str(request.local_version_id)]))
            if manifest_sha256(bundle.manifest) != version["content_hash"]:
                raise BotError("saved_bundle_commitment_mismatch")
            if bundle.report.get("case_id") != request.case_id:
                raise BotError("saved_report_case_mismatch")
        except (BundleValidationError, KeyError, TypeError):
            raise BotError("saved_bundle_unavailable") from None
        if version.get("case_id_hex") != "0x" + case_id_bytes(request.case_id).hex():
            raise BotError("saved_case_mismatch")
        return document, version, bundle

    def _intent(self, request: PrepareRequest, artifact: Artifact) -> tuple[dict, dict, dict]:
        transaction = {"from": request.account, "data": artifact.bytecode, "value": "0x0",
                       "chainId": NETWORKS[request.chain_id]["chain_id_hex"]}
        commitments: dict = {}
        saved: dict = {}
        if request.action == "deploy":
            return transaction, commitments, saved
        document, version, bundle = self._saved(request)
        case_bytes = case_id_bytes(request.case_id)
        content_hash = version["content_hash"]
        schema = version.get("schema_version")
        if (not isinstance(schema, str) or not 1 <= len(schema.encode("utf-8")) <= 64
                or not re.fullmatch(r"[0-9a-f]{64}", content_hash) or int(content_hash, 16) == 0):
            raise BotError("saved_version_mismatch")
        commitments = {"case_id_hex": "0x" + case_bytes.hex(), "content_hash": content_hash,
                       "local_version_id": request.local_version_id}
        uri = request.evidence_uri if request.evidence_uri is not None else "urn:cluetide:manifest:" + content_hash
        saved = {"version": version, "document": document, "evidence_uri": uri}
        if request.action == "create_case":
            if version.get("parent_version_id") != 0:
                raise BotError("initial_version_required")
            data = _calldata("createCase", ["bytes32", "bytes32", "string", "string"],
                             [case_bytes, bytes.fromhex(content_hash), schema, uri])
        elif request.action == "append_version":
            parent = next((v for v in document["versions"] if v.get("version_id") == version.get("parent_version_id")), None)
            if parent is None or parent.get("case_id") != request.case_id or bundle.report.get("parent_manifest_hash") != parent.get("content_hash"):
                raise BotError("saved_parent_mismatch")
            parent_request = request.model_copy(update={"local_version_id": parent["version_id"]})
            _, _, parent_bundle = self._saved(parent_request)
            if manifest_sha256(parent_bundle.manifest) != parent["content_hash"]:
                raise BotError("saved_parent_mismatch")
            saved["parent"] = parent
            commitments["onchain_version_id"] = request.onchain_version_id
            commitments["parent_content_hash"] = parent["content_hash"]
            data = _calldata("appendVersion", ["bytes32", "uint256", "bytes32", "string", "string"],
                             [case_bytes, int(request.onchain_version_id), bytes.fromhex(content_hash), schema, uri])
        else:
            review = next((r for r in document.get("reviews", []) if r.get("review_id") == request.local_review_id), None)
            if (review is None or review.get("case_id") != request.case_id
                    or review.get("version_id") != request.local_version_id or review.get("content_hash") != content_hash):
                raise BotError("saved_review_mismatch")
            payload = {"case_id": request.case_id, "version_id": request.local_version_id,
                       "content_hash": content_hash, "comment": review.get("comment"), "reviewer": review.get("reviewer")}
            if not isinstance(review.get("comment"), str) or hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != review.get("review_hash"):
                raise BotError("saved_review_commitment_mismatch")
            decision = review.get("decision")
            if decision not in DECISIONS:
                raise BotError("saved_review_mismatch")
            review_hash = review["review_hash"]
            review_uri = request.evidence_uri if request.evidence_uri is not None else "urn:cluetide:review:" + review_hash
            data = _calldata("addReview", ["bytes32", "uint256", "bytes32", "uint8", "bytes32", "string"],
                             [case_bytes, int(request.onchain_version_id), bytes.fromhex(content_hash),
                              DECISIONS[decision], bytes.fromhex(review_hash), review_uri])
            saved["review"] = review
            saved["review_uri"] = review_uri
            commitments.update(onchain_version_id=request.onchain_version_id, local_review_id=request.local_review_id,
                               review_hash=review_hash, decision=decision)
        transaction.update(to=request.contract_address, data=data)
        return transaction, commitments, saved

    @staticmethod
    def _version_matches(version: dict, case_hex: str, content_hash: str, version_id: str):
        if version["case_id_hex"] != case_hex or version["content_hash"] != content_hash or version["version_id"] != version_id:
            raise BotError("onchain_version_mismatch")

    @staticmethod
    def _records(rpc: BotRPC, address: str, case_hex: str, case: dict, block: str,
                 kind: Literal["version", "review"], *, offset=0, limit=READ_PAGE_SIZE):
        total = int(case[kind + "_count"])
        if offset > total:
            raise BotError("inventory_offset_out_of_bounds")
        records = []
        seen = set()
        title = kind.title()
        for index in range(offset, min(total, offset + limit)):
            identifier = rpc.getter(address, "get" + title + "Id", (bytes.fromhex(case_hex[2:]), index), block)
            if identifier in seen:
                raise BotError("invalid_getter_response")
            seen.add(identifier)
            record = rpc.getter(address, "get" + title, int(identifier), block)
            if record["case_id_hex"] != case_hex or record[kind + "_id"] != identifier:
                raise BotError("onchain_" + kind + "_mismatch")
            records.append(record)
        end = offset + len(records)
        return records, {"offset": str(offset), "total_count": str(total),
                         "next_offset": str(end) if end < total else None,
                         "complete": offset == 0 and end == total}

    @staticmethod
    def _anchor(rpc: BotRPC, block: int, expected: str | None = None,
                *, error="read_block_changed", missing_error="rpc_chain_view_unavailable") -> str:
        record = rpc.request("eth_getBlockByNumber", [hex(block), False])
        if record is None:
            raise BotError(missing_error)
        if not isinstance(record, dict):
            raise BotError("invalid_rpc_response")
        number, block_hash = _quantity(record.get("number")), _hash(record.get("hash"))
        if number != block:
            raise BotError("invalid_rpc_response")
        if expected is not None and block_hash != expected:
            raise BotError(error)
        return block_hash

    @staticmethod
    def _review_fingerprint(request, artifact, transaction, commitments):
        return hashlib.sha256(canonical_json_bytes({"prepared": request.model_dump(),
            "artifact": artifact.metadata, "transaction": transaction, "commitments": commitments})).hexdigest()

    @contextmanager
    def _review_cursor(self, fingerprint: str, token: str | None):
        with self._review_cursors_lock:
            now = time.monotonic()
            for key, item in list(self._review_cursors.items()):
                if now - item.touched >= 600 and not item.lock.locked():
                    del self._review_cursors[key]
            if token is None:
                if len(self._review_cursors) >= 64:
                    raise BotError("review_preflight_capacity")
                item = ReviewCursor(secrets.token_urlsafe(32), fingerprint, now)
                self._review_cursors[item.token] = item
            else:
                item = self._review_cursors.get(token)
                if item is None:
                    raise BotError("review_preflight_expired")
            if item.fingerprint != fingerprint:
                raise BotError("review_preflight_mismatch")
            if item.invalid:
                raise BotError("review_preflight_invalid")
            if not item.lock.acquire(blocking=False):
                raise BotError("review_preflight_busy")
            item.touched = now
        try:
            yield item
        except BotError as exc:
            if exc.code == "review_preflight_invalid":
                item.invalid = True
            raise
        finally:
            with self._review_cursors_lock:
                item.touched = time.monotonic()
                item.lock.release()
                if item.block_number is None:
                    self._review_cursors.pop(item.token, None)

    @staticmethod
    def _duplicate_review(reviews, request, commitments):
        for review in reviews:
            if (review["reviewer"].lower() == request.account
                    and review["version_id"] == request.onchain_version_id
                    and review["content_hash"] == commitments["content_hash"]
                    and review["review_hash"] == commitments["review_hash"]
                    and review["decision"] == commitments["decision"]):
                return review["review_id"]
        return None

    def review_preflight(self, payload: dict | ReviewPreflightRequest) -> dict:
        envelope = ReviewPreflightRequest.model_validate(payload)
        request = envelope.prepared
        artifact = self.artifact()
        transaction, commitments, _ = self._intent(request, artifact)
        fingerprint = self._review_fingerprint(request, artifact, transaction, commitments)
        with self._review_cursor(fingerprint, envelope.cursor) as item:
            with self.client_factory() as client:
                rpc = BotRPC(request.chain_id, client)
                rpc.verify_chain()
                block = item.block_number
                if block is None:
                    block = _quantity(rpc.request("eth_blockNumber", []))
                block_hash = self._anchor(rpc, block, item.block_hash, error="review_preflight_invalid")
                rpc.code(request.contract_address, artifact, hex(block))
                case = rpc.getter(request.contract_address, "getCase", case_id_bytes(request.case_id), hex(block))
                version = rpc.getter(request.contract_address, "getVersion", int(request.onchain_version_id), hex(block))
                self._version_matches(version, commitments["case_id_hex"], commitments["content_hash"], request.onchain_version_id)
                total = int(case["review_count"])
                if item.block_number is not None and total != item.total_count:
                    raise BotError("review_preflight_invalid")
                reviews = []
                if item.existing_review_id is None:
                    reviews, _ = self._records(rpc, request.contract_address, commitments["case_id_hex"],
                        case, hex(block), "review", offset=item.scanned_count, limit=REVIEW_SCAN_LIMIT)
                self._anchor(rpc, block, block_hash, error="review_preflight_invalid")
                # Progress is published only after every getter and both anchor checks succeed.
                item.block_number, item.block_hash, item.total_count = block, block_hash, total
                item.scanned_count += len(reviews)
                item.existing_review_id = item.existing_review_id or self._duplicate_review(reviews, request, commitments)
                return item.result()

    def prepare(self, payload: dict | PrepareRequest | PrepareSubmission) -> dict:
        if isinstance(payload, PrepareRequest):
            payload = payload.model_dump()
        submission = PrepareSubmission.model_validate(payload)
        request = PrepareRequest.model_validate(submission.model_dump(exclude={"review_preflight_cursor"}))
        artifact = self.artifact()
        transaction, commitments, saved = self._intent(request, artifact)
        if submission.review_preflight_cursor is not None:
            fingerprint = self._review_fingerprint(request, artifact, transaction, commitments)
            with self._review_cursor(fingerprint, submission.review_preflight_cursor) as item:
                return self._prepare(request, artifact, transaction, commitments, saved, item)
        return self._prepare(request, artifact, transaction, commitments, saved)

    def _prepare(self, request, artifact, transaction, commitments, saved, cursor=None):
        warnings = list(NETWORKS[request.chain_id]["warnings"])
        if request.action != "deploy" and (not request.evidence_uri or request.evidence_uri.startswith(("urn:", "/api/"))):
            warnings.append("public_evidence_uri_unavailable")
        with self.client_factory() as client:
            rpc = BotRPC(request.chain_id, client)
            rpc.verify_chain()
            block = _quantity(rpc.request("eth_blockNumber", []))
            block_tag = hex(block)
            block_hash = None
            if request.action == "add_review":
                block_hash = self._anchor(rpc, block)
                if cursor is not None:
                    if cursor.block_number is None:
                        raise BotError("review_preflight_invalid")
                    if block < cursor.block_number:
                        raise BotError("rpc_chain_view_unavailable")
                    self._anchor(rpc, cursor.block_number, cursor.block_hash, error="review_preflight_invalid")
            if request.action != "deploy":
                rpc.code(request.contract_address, artifact, block_tag)
                case = rpc.getter(request.contract_address, "getCase", case_id_bytes(request.case_id), block_tag,
                                  allow_missing=request.action == "create_case")
                if request.action == "create_case":
                    if case is not None:
                        raise BotError("case_exists")
                else:
                    if case is None:
                        raise BotError("unknown_case")
                    version = rpc.getter(request.contract_address, "getVersion", int(request.onchain_version_id), block_tag)
                    expected_hash = saved["parent"]["content_hash"] if request.action == "append_version" else commitments["content_hash"]
                    self._version_matches(version, commitments["case_id_hex"], expected_hash, request.onchain_version_id)
                    if request.action == "append_version":
                        if case["author"].lower() != request.account or version["author"].lower() != request.account:
                            raise BotError("unauthorized_author")
                        if case["head_version_id"] != request.onchain_version_id:
                            raise BotError("stale_parent")
                    else:
                        total = int(case["review_count"])
                        if cursor is None:
                            if total > REVIEW_SCAN_LIMIT:
                                raise BotError("review_preflight_required")
                            reviews, _ = self._records(rpc, request.contract_address,
                                commitments["case_id_hex"], case, block_tag, "review", limit=REVIEW_SCAN_LIMIT)
                            self._anchor(rpc, block, block_hash)
                            if self._duplicate_review(reviews, request, commitments):
                                raise BotError("review_exists")
                        else:
                            self._anchor(rpc, cursor.block_number, cursor.block_hash, error="review_preflight_invalid")
                            self._anchor(rpc, block, block_hash, error="review_preflight_invalid")
                            if total < cursor.total_count:
                                raise BotError("review_preflight_invalid")
                            if total > cursor.total_count:
                                cursor.block_number, cursor.block_hash, cursor.total_count = block, block_hash, total
                                raise BotError("review_preflight_changed")
                            if cursor.existing_review_id is not None:
                                raise BotError("review_exists")
                            if cursor.scanned_count != cursor.total_count:
                                raise BotError("review_preflight_required")
            fees = {"gas_limit": None, "gas_price_wei": None, "estimated_max_fee_wei": None,
                    "balance_wei": None, "sufficient_balance": None}
            for method, target, params in [
                ("eth_estimateGas", "gas_limit", [{k: v for k, v in transaction.items() if k != "chainId"}, block_tag]),
                ("eth_gasPrice", "gas_price_wei", []),
                ("eth_getBalance", "balance_wei", [request.account, block_tag]),
            ]:
                try:
                    amount = _quantity(rpc.request(method, params))
                    if target == "gas_limit":
                        if amount == 0 or amount > 30_000_000:
                            raise BotError("invalid_gas_estimate")
                        amount = (amount * 120 + 99) // 100
                        transaction["gas"] = hex(amount)
                    elif target == "gas_price_wei":
                        if amount == 0:
                            raise BotError("invalid_gas_price")
                        transaction["gasPrice"] = hex(amount)
                    fees[target] = str(amount)
                except BotError:
                    warnings.append({"gas_limit": "gas_estimate_unavailable", "gas_price_wei": "gas_price_unavailable",
                                     "balance_wei": "balance_unavailable"}[target])
            if fees["gas_limit"] is not None and fees["gas_price_wei"] is not None:
                fees["estimated_max_fee_wei"] = str(int(fees["gas_limit"]) * int(fees["gas_price_wei"]))
                if fees["balance_wei"] is not None:
                    fees["sufficient_balance"] = int(fees["balance_wei"]) >= int(fees["estimated_max_fee_wei"])
                    if not fees["sufficient_balance"]:
                        warnings.append("insufficient_balance")
        warnings.extend(["wallet_controls_nonce_and_final_fees", "hash_commitment_does_not_certify_facts"])
        return {"network": copy.deepcopy(NETWORKS[request.chain_id]), "action": request.action,
                "transaction": transaction, "commitments": commitments, "fees": fees,
                "artifact": artifact.metadata, "warnings": warnings, "read_block_number": str(block),
                "read_block_hash": block_hash,
                "read_observed_at_utc": _observed_at()}

    @staticmethod
    def _event_id(receipt: dict, address: str, action: str, commitments: dict) -> str:
        signature = "ReviewAdded(bytes32,uint256,uint256,address)" if action == "add_review" else "VersionAdded(bytes32,uint256,uint256,bytes32)"
        event_topic = "0x" + keccak(text=signature).hex()
        found = []
        logs = receipt.get("logs")
        if not isinstance(logs, list) or len(logs) > 200:
            raise BotError("invalid_receipt_logs")
        for log in logs:
            if not isinstance(log, dict) or str(log.get("address", "")).lower() != address:
                continue
            topics = log.get("topics")
            if not isinstance(topics, list) or not topics or str(topics[0]).lower() != event_topic:
                continue
            if log.get("removed") is True or len(topics) != (4 if action == "add_review" else 3):
                raise BotError("commitment_event_mismatch")
            if _hash(topics[1]) != commitments["case_id_hex"]:
                raise BotError("commitment_event_mismatch")
            version_id = str(int(_hash(topics[2]), 16))
            if action == "add_review":
                if version_id != commitments["onchain_version_id"]:
                    raise BotError("commitment_event_mismatch")
                identifier = str(int(_hash(topics[3]), 16))
            else:
                try:
                    parent, content = decode(["uint256", "bytes32"], bytes.fromhex(_data(log.get("data"))[2:]))
                except Exception:
                    raise BotError("commitment_event_mismatch") from None
                if content.hex() != commitments["content_hash"] or str(parent) != commitments.get("onchain_version_id", "0"):
                    raise BotError("commitment_event_mismatch")
                identifier = version_id
            if identifier == "0":
                raise BotError("commitment_event_mismatch")
            found.append(identifier)
        if len(found) != 1:
            raise BotError("commitment_event_missing")
        return found[0]

    def verify(self, payload: dict | VerifyRequest) -> dict:
        try:
            return self._verify(payload)
        except BotError as exc:
            if _rpc_unavailable(exc):
                raise BotError("bot_rpc_unavailable") from None
            raise

    def _verify(self, payload: dict | VerifyRequest) -> dict:
        request = VerifyRequest.model_validate(payload)
        prepared = request.prepared
        artifact = self.artifact()
        expected, commitments, saved = self._intent(prepared, artifact)
        result = {"status": "pending", "transaction_hash": request.transaction_hash,
                  "tx_link": NETWORKS[request.chain_id]["explorer_url"] + "/tx/" + request.transaction_hash,
                  "confirmations": 0, "minimum_confirmations": request.minimum_confirmations,
                  "contract_address": prepared.contract_address, "getter_state": {}, "onchain_ids": {},
                  "artifact": artifact.metadata, "commitments": commitments,
                  "verification_scope": "canonical_at_read_time_with_confirmations",
                  "gas_used": None, "effective_gas_price_wei": None, "actual_fee_wei": None,
                  "gas_price_source": "unavailable"}
        with self.client_factory() as client:
            rpc = BotRPC(request.chain_id, client)
            rpc.verify_chain()
            receipt = rpc.request("eth_getTransactionReceipt", [request.transaction_hash])
            if receipt is None:
                result["reason"] = "receipt_pending"
                result["read_observed_at_utc"] = _observed_at()
                return result
            transaction = rpc.request("eth_getTransactionByHash", [request.transaction_hash])
            try:
                if transaction is None:
                    raise BotError("transaction_pending")
                if not isinstance(receipt, dict) or not isinstance(transaction, dict):
                    raise BotError("invalid_rpc_response")
                for record in (receipt, transaction):
                    if _hash(record.get("transactionHash", record.get("hash"))) != request.transaction_hash:
                        raise BotError("transaction_hash_mismatch")
                    if str(record.get("from", "")).lower() != expected["from"]:
                        raise BotError("transaction_sender_mismatch")
                    if (str(record["to"]).lower() if record.get("to") else None) != expected.get("to"):
                        raise BotError("transaction_target_mismatch")
                if _quantity(transaction.get("value")) != 0 or _data(transaction.get("input")) != expected["data"]:
                    raise BotError("transaction_input_mismatch")
                if transaction.get("chainId") is not None and _quantity(transaction["chainId"]) != request.chain_id:
                    raise BotError("transaction_chain_mismatch")
                block_number = _quantity(receipt.get("blockNumber"))
                block_hash = _hash(receipt.get("blockHash"))
                if transaction.get("blockNumber") is None or transaction.get("blockHash") is None:
                    raise BotError("transaction_pending")
                if _quantity(transaction.get("blockNumber")) != block_number or _hash(transaction.get("blockHash")) != block_hash:
                    raise BotError("transaction_block_mismatch")
                canonical = rpc.request("eth_getBlockByNumber", [hex(block_number), False])
                if not isinstance(canonical, dict) or _quantity(canonical.get("number")) != block_number or _hash(canonical.get("hash")) != block_hash:
                    raise BotError("receipt_block_not_canonical")
                block_transactions = canonical.get("transactions")
                if (not isinstance(block_transactions, list) or len(block_transactions) > 8192
                        or request.transaction_hash not in [_hash(value) for value in block_transactions]):
                    raise BotError("transaction_not_in_canonical_block")
                current = _quantity(rpc.request("eth_blockNumber", []))
                if current < block_number:
                    raise BotError("receipt_block_ahead_of_head")
                result["confirmations"] = current - block_number + 1
                result["block_number"] = str(block_number)
                result["block_hash"] = block_hash
                if receipt.get("gasUsed") is not None:
                    result["gas_used"] = str(_quantity(receipt["gasUsed"]))
                if receipt.get("effectiveGasPrice") is not None:
                    result["effective_gas_price_wei"] = str(_quantity(receipt["effectiveGasPrice"]))
                    result["gas_price_source"] = "receipt_effective_gas_price"
                elif transaction.get("type") in (None, "0x0") and transaction.get("gasPrice") is not None:
                    result["effective_gas_price_wei"] = str(_quantity(transaction["gasPrice"]))
                    result["gas_price_source"] = "transaction_legacy_gas_price"
                if result["gas_used"] is not None and result["effective_gas_price_wei"] is not None:
                    result["actual_fee_wei"] = str(int(result["gas_used"]) * int(result["effective_gas_price_wei"]))
                status = _quantity(receipt.get("status"))
                if status == 0:
                    result.update(status="failed", reason="execution_reverted")
                    result["read_observed_at_utc"] = _observed_at()
                    return result
                if status != 1:
                    raise BotError("invalid_receipt_status")
                if result["confirmations"] < request.minimum_confirmations:
                    result["reason"] = "confirmations_pending"
                    result["read_observed_at_utc"] = _observed_at()
                    return result
                address = prepared.contract_address
                if prepared.action == "deploy":
                    try:
                        address = _address(receipt.get("contractAddress"))
                    except ValueError:
                        raise BotError("deployment_address_missing") from None
                rpc.code(address, artifact, hex(block_number))
                result["contract_address"] = address
                if prepared.action != "deploy":
                    identifier = self._event_id(receipt, address, prepared.action, commitments)
                    case = rpc.getter(address, "getCase", case_id_bytes(prepared.case_id), hex(block_number))
                    if prepared.action == "add_review":
                        review = rpc.getter(address, "getReview", int(identifier), hex(block_number))
                        version = rpc.getter(address, "getVersion", int(prepared.onchain_version_id), hex(block_number))
                        self._version_matches(version, commitments["case_id_hex"], commitments["content_hash"], prepared.onchain_version_id)
                        for key, expected_value in {"review_id": identifier, "case_id_hex": commitments["case_id_hex"],
                            "version_id": prepared.onchain_version_id, "content_hash": commitments["content_hash"],
                            "review_hash": commitments["review_hash"], "reviewer": prepared.account,
                            "decision": commitments["decision"], "review_uri": saved["review_uri"]}.items():
                            if review.get(key) != expected_value:
                                raise BotError("onchain_review_mismatch")
                        result["getter_state"] = {"case": case, "version": version, "review": review}
                        result["onchain_ids"] = {"version_id": prepared.onchain_version_id, "review_id": identifier}
                    else:
                        version = rpc.getter(address, "getVersion", int(identifier), hex(block_number))
                        self._version_matches(version, commitments["case_id_hex"], commitments["content_hash"], identifier)
                        if (version["parent_version_id"] != commitments.get("onchain_version_id", "0")
                                or version["author"] != prepared.account or case["author"] != prepared.account
                                or version["schema_version"] != saved["version"]["schema_version"]
                                or version["evidence_uri"] != saved["evidence_uri"]):
                            raise BotError("onchain_version_mismatch")
                        result["getter_state"] = {"case": case, "version": version}
                        result["onchain_ids"] = {"version_id": identifier}
                self._anchor(rpc, block_number, block_hash, error="receipt_block_not_canonical",
                             missing_error="receipt_block_not_canonical")
                result["status"] = "verified"
            except BotError as exc:
                if _rpc_unavailable(exc):
                    raise
                pending = {"transaction_pending", "transaction_block_mismatch", "receipt_block_not_canonical",
                           "transaction_not_in_canonical_block", "receipt_block_ahead_of_head"}
                result.update(status="pending" if exc.code in pending else "mismatch", reason=exc.code)
        result["read_observed_at_utc"] = _observed_at()
        return result

    def read(self, payload: dict | ReadRequest) -> dict:
        request = ReadRequest.model_validate(payload)
        artifact = self.artifact()
        case_hex = "0x" + case_id_bytes(request.case_id).hex()
        with self.client_factory() as client:
            rpc = BotRPC(request.chain_id, client)
            rpc.verify_chain()
            block = (int(request.read_block_number) if request.read_block_number is not None
                     else _quantity(rpc.request("eth_blockNumber", [])))
            block_hash = self._anchor(rpc, block, request.read_block_hash)
            rpc.code(request.contract_address, artifact, hex(block))
            case = rpc.getter(request.contract_address, "getCase", case_id_bytes(request.case_id), hex(block),
                              allow_missing=True)
            state = {"case": case, "versions": [], "reviews": [], "inventory": {
                kind: {"offset": "0", "total_count": "0", "next_offset": None, "complete": True}
                for kind in ("versions", "reviews")}}
            if case is None and (request.version_id is not None or request.review_id is not None):
                raise BotError("unknown_case")
            if case is not None and request.version_id is not None:
                version = rpc.getter(request.contract_address, "getVersion", int(request.version_id), hex(block))
                if version["case_id_hex"] != case_hex or version["version_id"] != request.version_id:
                    raise BotError("onchain_version_mismatch")
                state["version"] = version
            if case is not None and request.review_id is not None:
                review = rpc.getter(request.contract_address, "getReview", int(request.review_id), hex(block))
                if (review["case_id_hex"] != case_hex or review["review_id"] != request.review_id
                        or (request.version_id is not None and review["version_id"] != request.version_id)):
                    raise BotError("onchain_review_mismatch")
                state["review"] = review
            if case is not None:
                for kind, offset in (("version", request.version_offset), ("review", request.review_offset)):
                    records, inventory = self._records(rpc, request.contract_address, case_hex, case,
                                                       hex(block), kind, offset=offset)
                    state[kind + "s"] = records
                    state["inventory"][kind + "s"] = inventory
            self._anchor(rpc, block, block_hash)
        return {"network": copy.deepcopy(NETWORKS[request.chain_id]), "contract_address": request.contract_address,
                "case_id_hex": case_hex, "getter_state": state, "artifact": artifact.metadata,
                "case_status": "registered" if case is not None else "unregistered",
                "read_block_number": str(block), "read_block_hash": block_hash, "verification_scope": "getter_values_at_read_block",
                "read_observed_at_utc": _observed_at()}
