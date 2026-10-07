"""Bounded live and public-cache tool adapters; no transaction writer exists."""
import asyncio
import copy
import json
import re
from pathlib import Path
from .rpc import HEX_DATA, QUANTITY, RpcError, parse_quantity, validate_rpc_request
from .evidence import TRANSFER_TOPIC
from .schemas import MAX_SAFE_INTEGER, normalize_address, normalize_hash


class CachedRpc:
    def __init__(self, snapshot: dict):
        self.snapshot = snapshot

    def captured_headers(self) -> dict[int, dict]:
        headers = {}
        for name in ("start_block", "case_block", "end_block", "finalized"):
            header = self.snapshot.get(name)
            if isinstance(header, dict):
                number = parse_quantity(header.get("number"))
                if number in headers and headers[number].get("hash") != header.get("hash"):
                    raise RpcError("Captured headers conflict at one block number")
                headers[number] = header
        for header in self.snapshot.get("block_headers", {}).values():
            if isinstance(header, dict):
                number = parse_quantity(header.get("number"))
                if number in headers and headers[number].get("hash") != header.get("hash"):
                    raise RpcError("Captured headers conflict at one block number")
                headers[number] = header
        return headers

    async def call(self, method: str, params: list):
        validate_rpc_request(method, params, chain_id=1)
        scope = self.snapshot.get("request", {})
        direct = {"eth_chainId": "eth_chainId", "eth_getTransactionReceipt": "receipt", "eth_getTransactionByHash": "transaction"}
        if method in direct:
            key = direct[method]
            if method != "eth_chainId" and params != [self.snapshot.get("request", {}).get("transaction_hash", self.snapshot.get("transaction", {}).get("hash"))]:
                raise RpcError("Public cache does not cover this transaction")
        elif method == "eth_getBlockByNumber":
            if params[0] == "finalized":
                key = "finalized"
            else:
                header = self.captured_headers().get(parse_quantity(params[0]))
                if header is None:
                    raise RpcError("Public cache does not cover this block")
                return copy.deepcopy(header)
        elif method == "eth_getBlockByHash":
            header = next((value for value in self.captured_headers().values() if value.get("hash") == params[0]), None)
            if header is None:
                raise RpcError("Public cache does not cover this block")
            return copy.deepcopy(header)
        elif method == "eth_getLogs":
            query = params[0]
            if query.get("address", "").lower() != scope["token_address"].lower() or query.get("fromBlock") != hex(scope["start_block"]) or query.get("toBlock") != hex(scope["end_block"]):
                raise RpcError("Public cache does not cover this log window")
            topics = params[0].get("topics", [])
            subject = "0x" + "0" * 24 + self.snapshot["request"].get("subject_address", "")[2:].lower()
            if len(topics) != 3 or topics[0] != TRANSFER_TOPIC or topics[1:] not in ([subject, None], [None, subject]):
                raise RpcError("Public cache does not cover this subject")
            key = "logs_in" if len(topics) > 1 and topics[1] is None else "logs_out"
        elif method == "eth_call":
            if params[0].get("to", "").lower() != self.snapshot["request"]["token_address"].lower():
                raise RpcError("Public cache does not cover this token")
            selector = params[0].get("data", "")
            states = self.snapshot.get("token_state", {})
            block_tag = params[1]
            end_hash = self.snapshot.get("end_block", {}).get("hash")
            end_pinned = isinstance(block_tag, dict) and block_tag.get("blockHash") == end_hash and block_tag.get("requireCanonical") is True
            if end_pinned and states.get("metadata_block_number") == scope["end_block"] and states.get("metadata_block_hash") == end_hash:
                name = {"0x313ce567": "decimals", "0x95d89b41": "symbol", "0x06fdde03": "name"}.get(selector)
                if name in states:
                    return states[name]
            if selector in {"0x313ce567", "0x95d89b41", "0x06fdde03"} and end_pinned:
                name = {"0x313ce567": "decimals", "0x95d89b41": "symbol", "0x06fdde03": "name"}[selector]
                if name + "_window_end" in states:
                    return states[name + "_window_end"]
            if selector == "0x313ce567" and block_tag == hex(states.get("decimals_case_block_number", -1)) and "decimals_case_block" in states:
                return states["decimals_case_block"]
            if selector == "0x18160ddd":
                for suffix in ("before", "after"):
                    state_key = "totalSupply_" + suffix
                    number = states.get(state_key + "_block_number")
                    if type(number) is not int or state_key not in states:
                        continue
                    if block_tag == hex(number):
                        return states[state_key]
                    header = self.captured_headers().get(number, {})
                    state_hash = states.get(state_key + "_block_hash", header.get("hash"))
                    if (state_hash is not None and isinstance(block_tag, dict)
                            and block_tag.get("blockHash") == state_hash and block_tag.get("requireCanonical") is True):
                        return states[state_key]
            raise RpcError("Public cache has no matching historical token state")
        else:
            raise RpcError("Read method is unavailable in the public cache")
        if key not in self.snapshot or self.snapshot[key] is None:
            raise RpcError("Public cache does not cover this read")
        value = self.snapshot[key]
        if isinstance(value, dict) and "error" in value and "result" not in value:
            raise RpcError("Captured RPC read failed")
        return copy.deepcopy(value)

    async def close(self):
        pass


class InvestigationTools:
    def __init__(self, rpc, request, sources: list[dict], *, expected_transaction_blocks: dict | None = None,
                 block_anchors: dict[int, str] | None = None):
        self.rpc, self.request = rpc, request
        self.sources = {}
        for index, source in enumerate(sources):
            source_id = source.get("source_id", str(index))
            if source_id in self.sources and self.sources[source_id] != source:
                raise ValueError("One source ID must identify one captured source record")
            self.sources[source_id] = copy.deepcopy(source)
        self.observations = []
        self.transaction_identities = {}
        self.expected_transaction_blocks = {}
        self.block_anchors = {}
        captured_headers = getattr(rpc, "captured_headers", None)
        if callable(captured_headers):
            for number, header in captured_headers().items():
                if type(number) is not int or not 0 <= number <= MAX_SAFE_INTEGER:
                    raise ValueError("Captured historical state block number is invalid")
                if not isinstance(header, dict) or parse_quantity(header.get("number")) != number:
                    raise ValueError("Captured historical state header conflicts with its block number")
                self.block_anchors[number] = normalize_hash(header["hash"])
        for number, block_hash in (block_anchors or {}).items():
            if type(number) is not int or not 0 <= number <= MAX_SAFE_INTEGER:
                raise ValueError("Historical state block number is invalid")
            normalized_hash = normalize_hash(block_hash)
            if number in self.block_anchors and self.block_anchors[number] != normalized_hash:
                raise ValueError("Historical state anchor conflicts with a captured block header")
            self.block_anchors[number] = normalized_hash
        for tx_hash, anchor in (expected_transaction_blocks or {}).items():
            if not isinstance(anchor, dict) or set(anchor) != {"block_number", "block_hash"}:
                raise ValueError("Expected transaction blocks require a number and hash")
            number = anchor["block_number"]
            if type(number) is not int or not 0 <= number <= MAX_SAFE_INTEGER:
                raise ValueError("Expected transaction block number is invalid")
            self.expected_transaction_blocks[normalize_hash(tx_hash)] = {
                "block_number": number, "block_hash": normalize_hash(anchor["block_hash"])}
            if number in self.block_anchors and self.block_anchors[number] != normalize_hash(anchor["block_hash"]):
                raise ValueError("Historical state anchor conflicts with a collected transaction block")
            self.block_anchors.setdefault(number, normalize_hash(anchor["block_hash"]))

    async def _observe(self, method: str, params: list):
        # Retain attempted reads even if transport or cancellation fails. Error
        # text is fixed: upstream diagnostics can contain URLs or credentials.
        validate_rpc_request(method, params, chain_id=self.request.chain_id)
        observation = {"method": method, "params": params}
        self.observations.append(observation)
        try:
            result = await self.rpc.call(method, params)
        except asyncio.CancelledError:
            observation["error"] = "RPC read interrupted"
            raise
        except Exception:
            observation["error"] = "RPC read failed"
            raise
        observation["result"] = result
        return result

    async def get_receipt(self, tx_hash: str) -> dict:
        result = await self._observe("eth_getTransactionReceipt", [tx_hash])
        self._validate_transaction(result, tx_hash, receipt=True)
        projected = {k: result.get(k) for k in ("transactionHash", "blockHash", "blockNumber", "status", "from", "to")} if result else None
        if projected is not None:
            projected["logs"] = [log for log in result.get("logs", []) if log.get("address", "").lower() == self.request.token_address]
            projected["projection"] = "Selected receipt fields and requested-token logs; full receipt retained in raw agent RPC observations."
        return {"evidence_id": "receipt:" + tx_hash, "kind": "receipt", "payload": projected, "status": "ok" if result else "empty"}

    async def get_transaction(self, tx_hash: str) -> dict:
        result = await self._observe("eth_getTransactionByHash", [tx_hash])
        self._validate_transaction(result, tx_hash, receipt=False)
        projected = {k: result.get(k) for k in ("hash", "blockHash", "blockNumber", "from", "to", "input", "value")} if result else None
        return {"evidence_id": "transaction:" + tx_hash, "kind": "transaction", "payload": projected, "status": "ok" if result else "empty"}

    def _validate_transaction(self, result, tx_hash, *, receipt):
        if result is None:
            return
        hash_key = "transactionHash" if receipt else "hash"
        if not isinstance(result, dict):
            raise RpcError("Returned transaction does not match the investigation")
        try:
            if normalize_hash(result.get(hash_key)) != normalize_hash(tx_hash):
                raise ValueError("Mismatched transaction hash")
            block = parse_quantity(result.get("blockNumber"))
            block_hash = normalize_hash(result.get("blockHash"))
        except (KeyError, TypeError, ValueError):
            raise RpcError("Returned transaction failed confirmed-block schema validation") from None
        if not self.request.from_block <= block <= self.request.to_block:
            raise RpcError("Returned transaction is outside the investigation window")
        anchor = self.expected_transaction_blocks.get(tx_hash.lower())
        if anchor is not None and (block != anchor["block_number"] or block_hash != anchor["block_hash"]):
            raise RpcError("Returned transaction conflicts with the collected Transfer block")
        try:
            sender = normalize_address(result["from"])
            recipient = normalize_address(result["to"]) if result["to"] is not None else None
        except (KeyError, TypeError, ValueError):
            raise RpcError("Returned transaction failed participant schema validation") from None
        if not receipt:
            data, value = result.get("input"), result.get("value")
            # Native value is a uint256 quantity; block/index safe-integer
            # limits and ERC-20 token decimals do not apply to this field.
            if (not isinstance(data, str) or not HEX_DATA.fullmatch(data)
                    or not isinstance(value, str) or len(value) > 66 or not QUANTITY.fullmatch(value)):
                raise RpcError("Returned transaction failed input or native value schema validation")
        if receipt:
            try:
                if parse_quantity(result.get("status")) not in {0, 1}:
                    raise ValueError("Invalid receipt execution status")
                logs = result.get("logs")
                if not isinstance(logs, list) or len(logs) > 5_000:
                    raise ValueError("Invalid receipt logs")
                for log in logs:
                    if not isinstance(log, dict):
                        raise ValueError("Receipt log must be an object")
                    normalize_address(log.get("address"))
                    topics = log.get("topics")
                    if not isinstance(topics, list) or len(topics) > 4:
                        raise ValueError("Invalid receipt log topics")
                    for topic in topics:
                        normalize_hash(topic)
                    data = log.get("data")
                    if not isinstance(data, str) or not HEX_DATA.fullmatch(data):
                        raise ValueError("Invalid receipt log data")
                    if (parse_quantity(log.get("blockNumber")) != block
                            or normalize_hash(log.get("blockHash")) != block_hash
                            or normalize_hash(log.get("transactionHash")) != tx_hash.lower()):
                        raise ValueError("Receipt log conflicts with its transaction block")
                    parse_quantity(log.get("logIndex"))
                    if log.get("transactionIndex") is not None:
                        parse_quantity(log["transactionIndex"])
                    if log.get("removed") is not None and log["removed"] is not False:
                        raise ValueError("Receipt log is removed or malformed")
            except (KeyError, TypeError, ValueError):
                raise RpcError("Returned receipt failed status or log schema validation") from None
        identity = (block, block_hash, sender, recipient)
        previous = self.transaction_identities.get(tx_hash.lower())
        if previous is not None and previous != identity:
            raise RpcError("Returned transaction conflicts with a previous transaction observation")
        self.transaction_identities[tx_hash.lower()] = identity

    async def get_token_state(self, token_address: str, block_number: int) -> dict:
        block_hash = self.block_anchors.get(block_number)
        reference = {"blockHash": block_hash, "requireCanonical": True} if block_hash else hex(block_number)
        observation_index = len(self.observations)
        result = await self._observe("eth_call", [{"to": token_address, "data": "0x18160ddd"}, reference])
        if not isinstance(result, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", result):
            raise RpcError("Historical token getter did not return a complete uint256 ABI word")
        return {"evidence_id": f"state:{token_address}:{block_number}", "kind": "token_state", "payload": {
            "block_number": block_number, "block_hash": block_hash, "total_supply_raw": str(int(result, 16)),
            "block_selection": "EIP-1898" if block_hash else "number", "require_canonical": bool(block_hash),
            "anchor_status": "hash_pinned" if block_hash else "hash_unavailable",
            "rpc_observation_index": observation_index}, "status": "ok"}

    async def get_governance_source(self, source_id: str) -> dict:
        source = self.sources[source_id]
        kind = "governance_source" if source.get("category", "governance") == "governance" else "public_context_source"
        return {"evidence_id": "source:" + source_id, "kind": kind, "payload": copy.deepcopy(source), "status": "ok"}


def load_case(case_dir: Path) -> tuple[dict, list[dict]]:
    rpc_path, sources_path = case_dir / "rpc.json", case_dir / "sources.json"
    snapshot = json.loads(rpc_path.read_text(encoding="utf-8")) if rpc_path.exists() else {}
    sources = json.loads(sources_path.read_text(encoding="utf-8")) if sources_path.exists() else []
    if isinstance(sources, dict):
        sources = sources.get("sources", [])
    return snapshot, sources
