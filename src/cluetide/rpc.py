"""A small, validated read-only JSON-RPC surface with no signing methods."""

from __future__ import annotations

import re
import json
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from .schemas import MAX_SAFE_INTEGER, normalize_address, normalize_hash


QUANTITY = re.compile(r"^0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)$")
HEX_DATA = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")
ALLOWED_METHODS = frozenset({
    "eth_chainId", "eth_blockNumber", "eth_getBlockByNumber", "eth_getBlockByHash",
    "eth_getTransactionByHash", "eth_getTransactionReceipt", "eth_getLogs", "eth_call",
    "eth_getCode", "eth_getBalance",
})


class RpcError(RuntimeError):
    """Safe-to-display error; never includes endpoint URLs or response bodies."""

    def __init__(self, message: str, *, code: int | None = None):
        super().__init__(message)
        self.code = code


class RpcReader(Protocol):
    async def call(self, method: str, params: list[Any]) -> Any: ...


def parse_quantity(value: Any) -> int:
    if not isinstance(value, str) or not QUANTITY.fullmatch(value):
        raise ValueError("Expected a canonical hexadecimal RPC quantity")
    number = int(value, 16)
    if number > MAX_SAFE_INTEGER:
        raise ValueError("RPC block/index/chain quantities must fit a JSON safe integer")
    return number


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON response member")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("Non-finite JSON response number")


def _block_tag(value: Any, *, allow_finalized: bool = False) -> None:
    if allow_finalized and value == "finalized":
        return
    if isinstance(value, dict):
        if set(value) != {"blockHash", "requireCanonical"} or value["requireCanonical"] is not True:
            raise ValueError("Hash-pinned state reads requireCanonical=true")
        normalize_hash(value["blockHash"])
        return
    parse_quantity(value)


def validate_rpc_request(method: str, params: list[Any], *, chain_id: int) -> None:
    if method not in ALLOWED_METHODS:
        raise ValueError("RPC method is outside the fixed read-only allowlist")
    if not isinstance(params, list):
        raise ValueError("RPC params must be an array")
    if method in {"eth_chainId", "eth_blockNumber"}:
        if params:
            raise ValueError("RPC method takes no parameters")
        return
    if method in {"eth_getTransactionByHash", "eth_getTransactionReceipt"}:
        if len(params) != 1:
            raise ValueError("Expected one transaction hash")
        normalize_hash(params[0])
        return
    if method in {"eth_getBlockByNumber", "eth_getBlockByHash"}:
        if len(params) != 2 or params[1] is not False:
            raise ValueError("Block reads must request transaction hashes only")
        if method == "eth_getBlockByHash":
            normalize_hash(params[0])
        else:
            if isinstance(params[0], dict):
                raise ValueError("Block header method requires a number or finalized tag")
            _block_tag(params[0], allow_finalized=True)
        return
    if method == "eth_getLogs":
        if chain_id == 677:
            raise ValueError("eth_getLogs is disabled for BOT mainnet chain 677; use getters")
        if len(params) != 1 or not isinstance(params[0], dict):
            raise ValueError("Expected one bounded log filter")
        log_filter = params[0]
        if set(log_filter) != {"address", "fromBlock", "toBlock", "topics"}:
            raise ValueError("Log filter requires address, fromBlock, toBlock and topics")
        normalize_address(log_filter["address"])
        start, end = parse_quantity(log_filter["fromBlock"]), parse_quantity(log_filter["toBlock"])
        if start > end or end - start + 1 > 2_000:
            raise ValueError("Log filter must span between 1 and 2000 numeric blocks")
        topics = log_filter["topics"]
        if not isinstance(topics, list) or not 1 <= len(topics) <= 4:
            raise ValueError("Log filter must contain 1 to 4 topics")
        for topic in topics:
            if topic is None:
                continue
            if isinstance(topic, list):
                if not 1 <= len(topic) <= 8:
                    raise ValueError("Topic alternatives are bounded at 8")
                for option in topic:
                    normalize_hash(option)
            else:
                normalize_hash(topic)
        return
    if method == "eth_call":
        if len(params) != 2 or not isinstance(params[0], dict):
            raise ValueError("Expected a contract read and pinned block")
        transaction = params[0]
        if set(transaction) != {"to", "data"}:
            raise ValueError("Contract reads permit only to and data")
        normalize_address(transaction["to"])
        data = transaction["data"]
        if not isinstance(data, str) or not HEX_DATA.fullmatch(data) or len(data) > 8_194:
            raise ValueError("Contract read data must be bounded hexadecimal bytes")
        if len(data) < 10:
            raise ValueError("Contract read requires a four-byte selector")
        _block_tag(params[1])
        return
    if method in {"eth_getCode", "eth_getBalance"}:
        if len(params) != 2:
            raise ValueError("Expected an address and pinned block")
        normalize_address(params[0])
        _block_tag(params[1])


class ReadOnlyRpcClient:
    def __init__(self, endpoint: str, *, chain_id: int = 1, timeout: float = 15,
                 max_calls: int = 80, http_client: httpx.AsyncClient | None = None):
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("RPC endpoint must be HTTP(S) without embedded credentials")
        if isinstance(chain_id, bool) or not isinstance(chain_id, int) or chain_id <= 0:
            raise ValueError("chain_id must be a positive integer")
        if not 1 <= max_calls <= 200:
            raise ValueError("max_calls must be between 1 and 200")
        self._endpoint = endpoint
        self.chain_id = chain_id
        self.max_calls = max_calls
        self.calls_attempted = 0
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False)

    async def call(self, method: str, params: list[Any]) -> Any:
        validate_rpc_request(method, params, chain_id=self.chain_id)
        if self.calls_attempted >= self.max_calls:
            raise RpcError("RPC attempt budget exhausted")
        self.calls_attempted += 1
        request_id = self.calls_attempted
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            async with self._http.stream("POST", self._endpoint, json=payload) as response:
                response.raise_for_status()
                chunks = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 4 * 1024 * 1024:
                        raise RpcError("RPC response exceeds the size limit")
                    chunks.append(chunk)
                body = json.loads(b"".join(chunks), object_pairs_hook=_json_object, parse_constant=_reject_constant)
        except RpcError:
            raise
        except httpx.TimeoutException:
            raise RpcError("RPC request timed out") from None
        except httpx.HTTPStatusError as exc:
            raise RpcError(f"RPC HTTP status {exc.response.status_code}") from None
        except (httpx.HTTPError, ValueError, RecursionError):
            raise RpcError("RPC transport or JSON response failure") from None
        if (not isinstance(body, dict) or body.get("jsonrpc") != "2.0"
                or type(body.get("id")) is not int or body["id"] != request_id):
            raise RpcError("RPC response envelope is invalid")
        if "error" in body:
            error = body["error"]
            code = error.get("code") if isinstance(error, dict) else None
            code = code if isinstance(code, int) and not isinstance(code, bool) else None
            raise RpcError("RPC returned an error" + (f" (code {code})" if code is not None else ""), code=code)
        if "result" not in body:
            raise RpcError("RPC response is missing result")
        return body["result"]

    async def close(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def __aenter__(self) -> "ReadOnlyRpcClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
