"""Unsigned release transactions and bounded, canonical getter verification."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

import httpx
from eth_abi import decode
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak

from .bot import BotError, BotRPC, NETWORKS, _ALLOWED_REVERT, _calldata, _data, _hash, _quantity

ReleaseChainError = BotError
ROOT = Path(__file__).resolve().parents[2]
CONTRACT_NAME = "ClueTideReleaseWitness"
COMPILER = "0.8.30+commit.73712a01"
COMPILER_SHA256 = "ccbd3ed44d5fbd26fe039702d403421f1212d2e8752e3cbe3bfd074986911586"
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
_HEX = re.compile(r"0x(?:[0-9a-fA-F]{2})*\Z")

ACTIONS = {
    "create_task": ("createTask", ["bytes32", "address", "address", "address", "address", "bytes32"],
        ["task_id", "proposer", "reviewer", "executor", "receipt_signer", "target_hash"]),
    "propose_version": ("proposeVersion", ["bytes32", "uint256", "bytes32", "bytes32", "uint64"],
        ["task_id", "parent_version_id", "manifest_hash", "artifact_hash", "deadline"]),
    "approve": ("approve", ["bytes32", "uint256", "bytes32", "bytes32", "uint64"],
        ["task_id", "version_id", "manifest_hash", "artifact_hash", "deadline"]),
    "revoke": ("revoke", ["bytes32", "uint256"], ["task_id", "version_id"]),
    "request_correction": ("requestCorrection", ["bytes32", "uint256"], ["task_id", "version_id"]),
    "consume": ("consume", ["bytes32", "uint256", "bytes32", "bytes32"],
        ["task_id", "version_id", "manifest_hash", "artifact_hash"]),
    "record_receipt": ("recordReceipt", ["bytes32", "uint256", "bytes32", "uint8", "bytes"],
        ["task_id", "attempt_id", "receipt_hash", "result", "signature"]),
}
GETTERS = {
    "task": ("getTask", ["bytes32"], "(address,address,address,address,address,bytes32,uint256,uint256,uint64)",
        ["owner", "proposer", "reviewer", "executor", "receipt_signer", "target_hash", "version_count", "attempt_count", "created_at"]),
    "version": ("getVersion", ["bytes32", "uint256"], "(uint256,uint256,bytes32,bytes32,uint64,uint8,uint64,uint256)",
        ["version_id", "parent_version_id", "manifest_hash", "artifact_hash", "deadline", "decision", "approved_at", "attempt_id"]),
    "attempt": ("getAttempt", ["bytes32", "uint256"], "(uint256,uint256,uint64,bytes32,uint8,uint64)",
        ["attempt_id", "version_id", "started_at", "receipt_hash", "result", "recorded_at"]),
}


def _address(value):
    if not isinstance(value, str) or not _ADDRESS.fullmatch(value) or int(value, 16) == 0:
        raise ReleaseChainError("invalid_address")
    return value.lower()


def _uint(value, *, maximum=2**256 - 1, minimum=1):
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]{0,77}", value):
        number = int(value)
    else:
        raise ReleaseChainError("invalid_integer")
    if not minimum <= number <= maximum:
        raise ReleaseChainError("invalid_integer")
    return number


def _commitment(value):
    value = _hash(value)
    if int(value, 16) == 0:
        raise ReleaseChainError("invalid_commitment")
    return value


def receipt_typed_data(chain_id, contract_address, task_id, version_id, attempt_id, receipt_hash, result):
    """Return the exact EIP-712 statement for a caller-controlled EOA signer."""
    return {"types": {
        "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
        "Receipt": [{"name": "taskId", "type": "bytes32"}, {"name": "versionId", "type": "uint256"},
            {"name": "attemptId", "type": "uint256"}, {"name": "receiptHash", "type": "bytes32"},
            {"name": "result", "type": "uint8"}]}, "primaryType": "Receipt",
        "domain": {"name": CONTRACT_NAME, "version": "1", "chainId": _uint(chain_id),
            "verifyingContract": _address(contract_address)},
        "message": {"taskId": _commitment(task_id), "versionId": _uint(version_id),
            "attemptId": _uint(attempt_id), "receiptHash": _commitment(receipt_hash),
            "result": _uint(result, maximum=3)}}


def recover_receipt_signer(chain_id, contract_address, task_id, version_id, attempt_id, receipt_hash, result, signature):
    """Authenticate the receipt statement independently from its submitter."""
    if not isinstance(signature, str) or not _HEX.fullmatch(signature) or len(signature) != 132:
        raise ReleaseChainError("invalid_signature")
    raw = bytes.fromhex(signature[2:])
    if raw[64] not in (27, 28) or int.from_bytes(raw[32:64], "big") > 0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0:
        raise ReleaseChainError("invalid_signature")
    try:
        message = encode_typed_data(full_message=receipt_typed_data(chain_id, contract_address, task_id,
            version_id, attempt_id, receipt_hash, result))
        return Account.recover_message(message, signature=signature).lower()
    except Exception:
        raise ReleaseChainError("invalid_signature") from None


@dataclass(frozen=True)
class ReleaseArtifact:
    bytecode: str
    runtime: str
    metadata: dict

    @classmethod
    def load(cls, artifact_path, source_path):
        try:
            payload = Path(artifact_path).read_bytes()
            artifact = json.loads(payload)
            source_hash = hashlib.sha256(Path(source_path).read_bytes()).hexdigest()
            creation, runtime = artifact["bytecode"].lower(), artifact["deployedBytecode"].lower()
            if (artifact["contractName"] != CONTRACT_NAME or artifact["sourceSha256"] != source_hash
                    or artifact["compiler"] != COMPILER or artifact["compilerSha256"] != COMPILER_SHA256
                    or artifact["evmVersion"] != "paris" or artifact["optimizerRuns"] != 200
                    or not _HEX.fullmatch(creation) or not _HEX.fullmatch(runtime)
                    or len(creation) < 4 or len(runtime) < 4):
                raise ValueError
            metadata = {"contract_name": CONTRACT_NAME, "compiler": COMPILER, "evm_version": "paris",
                "source_sha256": source_hash, "artifact_sha256": hashlib.sha256(payload).hexdigest(),
                "bytecode_sha256": hashlib.sha256(bytes.fromhex(creation[2:])).hexdigest(),
                "runtime_sha256": hashlib.sha256(bytes.fromhex(runtime[2:])).hexdigest()}
            return cls(creation, runtime, metadata)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise ReleaseChainError("artifact_integrity_error") from None


class ReleaseChainService:
    """Prepare only unsigned transactions; verify known IDs with bounded reads."""

    def __init__(self, *, client_factory=None, artifact_path=None, source_path=None):
        self.client_factory = client_factory or (lambda: httpx.Client(timeout=8, trust_env=False, follow_redirects=False))
        self.artifact_path = artifact_path or ROOT / "contracts/artifacts/ClueTideReleaseWitness.json"
        self.source_path = source_path or ROOT / "contracts/ClueTideReleaseWitness.sol"

    def artifact(self):
        return ReleaseArtifact.load(self.artifact_path, self.source_path)

    def networks(self):
        return {"networks": copy.deepcopy(list(NETWORKS.values())), "artifact": self.artifact().metadata}

    def _intent(self, payload, artifact):
        if not isinstance(payload, dict):
            raise ReleaseChainError("invalid_request")
        action = payload.get("action")
        if action != "deploy" and action not in ACTIONS:
            raise ReleaseChainError("invalid_action")
        chain = payload.get("chain_id")
        if type(chain) is not int or chain not in NETWORKS:
            raise ReleaseChainError("unsupported_chain")
        transaction = {"from": _address(payload.get("account")), "chainId": hex(chain), "value": "0x0"}
        if action == "deploy":
            if set(payload) - {"action", "chain_id", "account"}:
                raise ReleaseChainError("unexpected_request_field")
            transaction["data"] = artifact.bytecode
            return transaction, {}
        name, types, fields = ACTIONS[action]
        if set(payload) - set(fields) - {"action", "chain_id", "account", "contract_address"}:
            raise ReleaseChainError("unexpected_request_field")
        transaction["to"] = _address(payload.get("contract_address"))
        commitments, values = {}, []
        for field, abi_type in zip(fields, types):
            value = payload.get(field)
            if abi_type == "bytes32":
                value = _commitment(value)
                encoded = bytes.fromhex(value[2:])
            elif abi_type == "address":
                value = encoded = _address(value)
            elif abi_type == "bytes":
                if not isinstance(value, str) or not _HEX.fullmatch(value) or len(value) != 132:
                    raise ReleaseChainError("invalid_signature")
                value = value.lower()
                encoded = bytes.fromhex(value[2:])
            else:
                value = encoded = _uint(value, maximum=(2**64 - 1 if abi_type == "uint64" else 3 if field == "result" else 2**256 - 1),
                    minimum=0 if field == "parent_version_id" else 1)
            commitments[field] = str(value) if isinstance(value, int) else value
            values.append(encoded)
        transaction["data"] = _calldata(name, types, values)
        if action == "propose_version":
            commitments["version_id"] = str(int(commitments["parent_version_id"]) + 1)
        return transaction, commitments

    @staticmethod
    def _anchor(rpc, number, expected=None):
        block = rpc.request("eth_getBlockByNumber", [hex(number), False])
        if not isinstance(block, dict) or _quantity(block.get("number")) != number:
            raise ReleaseChainError("invalid_block")
        block_hash = _hash(block.get("hash"))
        if expected is not None and block_hash != expected:
            raise ReleaseChainError("canonical_block_changed")
        _quantity(block.get("timestamp"))
        return block_hash

    @staticmethod
    def _getter(rpc, address, kind, task_id, record_id, block, *, allow_missing=False):
        name, inputs, output, names = GETTERS[kind]
        args = [bytes.fromhex(_commitment(task_id)[2:])]
        if kind != "task":
            args.append(_uint(record_id))
        result = rpc.request("eth_call", [{"to": address, "data": _calldata(name, inputs, args)}, block],
            allowed_revert="0x" + keccak(text="UnknownTask()")[:4].hex() if allow_missing else None)
        if result is _ALLOWED_REVERT:
            return None
        try:
            raw = bytes.fromhex(_data(result)[2:])
            values = decode([output], raw)[0]
            if len(raw) != 32 * len(names):
                raise ValueError
            record = {}
            for key, value in zip(names, values):
                record[key] = "0x" + value.hex() if isinstance(value, bytes) else str(value) if isinstance(value, int) else value.lower()
            return record
        except Exception:
            raise ReleaseChainError("invalid_getter_response") from None

    def _state(self, rpc, address, commitments, block):
        state = {"task": self._getter(rpc, address, "task", commitments["task_id"], None, block)}
        if "version_id" in commitments:
            state["version"] = self._getter(rpc, address, "version", commitments["task_id"], commitments["version_id"], block)
        attempt_id = commitments.get("attempt_id")
        if attempt_id is None and "version" in state and state["version"]["attempt_id"] != "0":
            attempt_id = state["version"]["attempt_id"]
        if attempt_id:
            state["attempt"] = self._getter(rpc, address, "attempt", commitments["task_id"], attempt_id, block)
        return state

    def prepare(self, payload):
        artifact = self.artifact()
        transaction, commitments = self._intent(payload, artifact)
        with self.client_factory() as client:
            rpc = BotRPC(payload["chain_id"], client)
            rpc.verify_chain()
            block = _quantity(rpc.request("eth_blockNumber", []))
            anchor = self._anchor(rpc, block)
            if "to" in transaction:
                rpc.code(transaction["to"], artifact, hex(block))
            # eth_call executes a simulation at the fixed observation block.
            rpc.request("eth_call", [transaction, hex(block)])
            gas = _quantity(rpc.request("eth_estimateGas", [transaction, hex(block)]))
            if not 21000 <= gas <= 20_000_000:
                raise ReleaseChainError("invalid_gas_estimate")
            price = _quantity(rpc.request("eth_gasPrice", []))
            balance = _quantity(rpc.request("eth_getBalance", [transaction["from"], hex(block)]))
            self._anchor(rpc, block, anchor)
        transaction.update(gas=hex(min(20_000_000, (gas * 120 + 99) // 100)), gasPrice=hex(price))
        fee = int(transaction["gas"], 16) * price
        return {"action": payload["action"], "chain_id": payload["chain_id"], "transaction": transaction,
            "commitments": commitments, "read_block_number": str(block), "read_block_hash": anchor,
            "artifact": artifact.metadata, "estimated_fee_wei": str(fee), "balance_wei": str(balance),
            "fees": {"gas_limit": str(int(transaction["gas"], 16)), "gas_price_wei": str(price),
                "estimated_max_fee_wei": str(fee), "balance_wei": str(balance), "sufficient_balance": balance >= fee},
            "ready_to_send": balance >= fee, "warnings": ["insufficient_balance"] if balance < fee else []}

    def read(self, payload):
        return self._read_at_current(payload)

    def _read_at_current(self, payload, origin=None):
        allowed = {"chain_id", "contract_address", "task_id", "version_id", "attempt_id"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ReleaseChainError("invalid_request")
        artifact = self.artifact()
        address, task_id = _address(payload.get("contract_address")), _commitment(payload.get("task_id"))
        commitments = {"task_id": task_id}
        for key in ("version_id", "attempt_id"):
            if payload.get(key) is not None:
                commitments[key] = str(_uint(payload[key]))
        with self.client_factory() as client:
            rpc = BotRPC(payload.get("chain_id"), client)
            rpc.verify_chain()
            block = _quantity(rpc.request("eth_blockNumber", []))
            anchor = self._anchor(rpc, block)
            rpc.code(address, artifact, hex(block))
            state = self._state(rpc, address, commitments, hex(block))
            self._anchor(rpc, block, anchor)
            if origin is not None:
                self._anchor(rpc, origin[0], origin[1])
        return {"chain_id": payload["chain_id"], "contract_address": address, "task_id": task_id,
            "getter_state": state, "read_block_number": str(block), "read_block_hash": anchor, "artifact": artifact.metadata}

    @staticmethod
    def _matches(action, expected, state):
        task = state["task"]
        if action == "create_task":
            for key in ("proposer", "reviewer", "executor", "receipt_signer", "target_hash"):
                if task[key] != expected[key]:
                    raise ReleaseChainError("getter_commitment_mismatch")
        if action in ("propose_version", "approve", "consume"):
            version = state["version"]
            for key in ("version_id", "manifest_hash", "artifact_hash"):
                if version[key] != expected[key]:
                    raise ReleaseChainError("getter_commitment_mismatch")
            if "deadline" in expected and version["deadline"] != expected["deadline"]:
                raise ReleaseChainError("getter_commitment_mismatch")
        decision = {"propose_version": "0", "approve": "1", "revoke": "2", "request_correction": "3", "consume": "4"}.get(action)
        if decision and state["version"]["decision"] != decision:
            raise ReleaseChainError("getter_decision_mismatch")
        if action == "consume" and state["attempt"]["version_id"] != expected["version_id"]:
            raise ReleaseChainError("getter_commitment_mismatch")
        if action == "record_receipt":
            for key in ("attempt_id", "receipt_hash", "result"):
                if state["attempt"][key] != expected[key]:
                    raise ReleaseChainError("getter_commitment_mismatch")

    def verify(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"prepared", "transaction_hash", "min_confirmations"}:
            raise ReleaseChainError("invalid_request")
        request = payload.get("prepared")
        artifact = self.artifact()
        expected, commitments = self._intent(request, artifact)
        tx_hash = _commitment(payload.get("transaction_hash"))
        minimum = _uint(payload.get("min_confirmations", 2), maximum=64)
        base = {"status": "pending", "chain_id": request["chain_id"], "action": request["action"],
            "transaction_hash": tx_hash, "contract_address": expected.get("to"), "artifact": artifact.metadata,
            "commitments": commitments, "confirmations": "0", "minimum_confirmations": str(minimum)}
        with self.client_factory() as client:
            rpc = BotRPC(request["chain_id"], client)
            rpc.verify_chain()
            receipt = rpc.request("eth_getTransactionReceipt", [tx_hash])
            if receipt is None:
                return base
            if not isinstance(receipt, dict) or _hash(receipt.get("transactionHash")) != tx_hash:
                raise ReleaseChainError("invalid_receipt")
            tx = rpc.request("eth_getTransactionByHash", [tx_hash])
            if not isinstance(tx, dict) or _hash(tx.get("hash")) != tx_hash:
                raise ReleaseChainError("invalid_transaction")
            if _address(tx.get("from")) != expected["from"] or _data(tx.get("input")) != expected["data"] or _quantity(tx.get("value")) != 0:
                raise ReleaseChainError("transaction_intent_mismatch")
            if tx.get("chainId") is None or _quantity(tx["chainId"]) != request["chain_id"]:
                raise ReleaseChainError("transaction_chain_mismatch")
            if _address(receipt.get("from")) != expected["from"]:
                raise ReleaseChainError("transaction_intent_mismatch")
            if "to" in expected:
                if _address(tx.get("to")) != expected["to"] or _address(receipt.get("to")) != expected["to"] or receipt.get("contractAddress") is not None:
                    raise ReleaseChainError("transaction_intent_mismatch")
            elif tx.get("to") is not None or receipt.get("to") is not None:
                raise ReleaseChainError("transaction_intent_mismatch")
            block = _quantity(receipt.get("blockNumber"))
            anchor = _hash(receipt.get("blockHash"))
            if _quantity(tx.get("blockNumber")) != block or _hash(tx.get("blockHash")) != anchor:
                raise ReleaseChainError("transaction_block_mismatch")
            self._anchor(rpc, block, anchor)
            head = _quantity(rpc.request("eth_blockNumber", []))
            if head < block:
                raise ReleaseChainError("invalid_chain_head")
            confirmations = head - block + 1
            status = _quantity(receipt.get("status"))
            if status not in (0, 1):
                raise ReleaseChainError("invalid_receipt")
            base.update(block_number=str(block), block_hash=anchor, confirmations=str(confirmations),
                gas_used=str(_quantity(receipt.get("gasUsed"))), effective_gas_price=str(_quantity(receipt.get("effectiveGasPrice"))))
            base["actual_fee_wei"] = str(int(base["gas_used"]) * int(base["effective_gas_price"]))
            if status == 0:
                base["status"] = "failed"
                return base
            address = expected.get("to") or _address(receipt.get("contractAddress"))
            base["contract_address"] = address
            rpc.code(address, artifact, hex(block))
            state = {} if request["action"] == "deploy" else self._state(rpc, address, commitments, hex(block))
            if state:
                self._matches(request["action"], commitments, state)
                if request["action"] == "create_task" and state["task"]["owner"] != expected["from"]:
                    raise ReleaseChainError("getter_commitment_mismatch")
                if request["action"] == "record_receipt":
                    signer = recover_receipt_signer(request["chain_id"], address, commitments["task_id"],
                        state["attempt"]["version_id"], commitments["attempt_id"], commitments["receipt_hash"],
                        commitments["result"], commitments["signature"])
                    if signer != state["task"]["receipt_signer"]:
                        raise ReleaseChainError("receipt_signer_mismatch")
                    base["receipt_signer"] = signer
            self._anchor(rpc, block, anchor)
            base.update(status="verified" if confirmations >= minimum else "confirming", getter_state=state)
            base["onchain_ids"] = {}
            if "version" in state:
                base["onchain_ids"]["version_id"] = state["version"]["version_id"]
            if "attempt" in state:
                base["onchain_ids"].update(attempt_id=state["attempt"]["attempt_id"], version_id=state["attempt"]["version_id"])
            return base

    def authorize_execution(self, payload):
        """Verify a consumed grant before the executor touches the target."""
        if not isinstance(payload, dict) or set(payload) - {"chain_id", "contract_address", "task_id", "version_id", "attempt_id",
                "start_transaction_hash", "account", "manifest_hash", "artifact_hash", "minimum_confirmations", "target_hash"}:
            raise ReleaseChainError("invalid_request")
        prepared = {key: payload.get(key) for key in ("chain_id", "contract_address", "task_id", "version_id", "account", "manifest_hash", "artifact_hash")}
        prepared["action"] = "consume"
        result = self.verify({"prepared": prepared, "transaction_hash": payload.get("start_transaction_hash"),
            "min_confirmations": payload.get("minimum_confirmations", 2)})
        if result["status"] != "verified":
            return result
        state = result["getter_state"]
        if state["attempt"]["attempt_id"] != str(_uint(payload.get("attempt_id"))) or state["task"]["executor"] != _address(payload.get("account")):
            raise ReleaseChainError("execution_grant_mismatch")
        if payload.get("target_hash") is not None and state["task"]["target_hash"] != _commitment(payload["target_hash"]):
            raise ReleaseChainError("execution_target_mismatch")
        current = self._read_at_current({"chain_id": payload["chain_id"], "contract_address": payload["contract_address"],
            "task_id": payload["task_id"], "version_id": payload["version_id"], "attempt_id": payload["attempt_id"]},
            origin=(int(result["block_number"]), result["block_hash"]))
        latest = current["getter_state"]
        fixed = ("owner", "proposer", "reviewer", "executor", "receipt_signer", "target_hash")
        if (any(latest["task"][key] != state["task"][key] for key in fixed)
                or any(latest["version"][key] != state["version"][key] for key in
                    ("version_id", "manifest_hash", "artifact_hash", "deadline", "approved_at"))
                or latest["attempt"]["attempt_id"] != state["attempt"]["attempt_id"]
                or latest["attempt"]["version_id"] != state["attempt"]["version_id"]
                or latest["attempt"]["started_at"] != state["attempt"]["started_at"]
                or latest["version"]["attempt_id"] != state["attempt"]["attempt_id"]
                or latest["version"]["decision"] != "4"):
            raise ReleaseChainError("execution_grant_mismatch")
        if latest["attempt"]["result"] != "0" or int(latest["attempt"]["receipt_hash"], 16) != 0:
            raise ReleaseChainError("execution_already_reported")
        result["latest_getter_state"] = latest
        result["read_block_number"] = current["read_block_number"]
        result["read_block_hash"] = current["read_block_hash"]
        result["attempt_id"] = state["attempt"]["attempt_id"]
        result["version_id"] = state["attempt"]["version_id"]
        result["start_transaction_hash"] = result["transaction_hash"]
        return result
