"""Bounded finalized-window collection with explicit partial/error coverage."""

from __future__ import annotations

import asyncio
from typing import Any

from .evidence import TRANSFER_TOPIC, address_topic, build_alerts, decode_transfer_log, transfer_identity, transfer_payload
from .rpc import RpcError, RpcReader, parse_quantity
from .schemas import BlockAnchor, Coverage, EvidenceSet, InvestigationRequest, QueryCoverage, TokenMetadata


def _public_error(exc: Exception) -> str:
    if isinstance(exc, RpcError):
        return str(exc)
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "RPC data failed schema validation"
    if isinstance(exc, TimeoutError):
        return "RPC collection deadline exceeded"
    return "RPC read failed"


def _anchor(header: Any, *, tag: str, expected_number: int | None = None) -> BlockAnchor:
    if not isinstance(header, dict):
        raise ValueError("Requested block header is unavailable")
    number = parse_quantity(header.get("number"))
    if expected_number is not None and number != expected_number:
        raise ValueError("Returned header is outside the requested block")
    return BlockAnchor(number=number, block_hash=header.get("hash"), tag=tag)


def _decode_abi_text(data: Any) -> str:
    if not isinstance(data, str) or not data.startswith("0x") or len(data) > 8_194:
        raise ValueError("Invalid ABI text")
    raw = bytes.fromhex(data[2:])
    if len(raw) == 32:
        result = raw.rstrip(b"\x00").decode("utf-8", errors="strict")
    else:
        if len(raw) < 64 or int.from_bytes(raw[:32], "big") != 32:
            raise ValueError("Invalid ABI dynamic string offset")
        length = int.from_bytes(raw[32:64], "big")
        if length > 256 or 64 + length > len(raw):
            raise ValueError("ABI string is outside the bounded result")
        result = raw[64:64 + length].decode("utf-8", errors="strict")
    if not result or len(result) > 256 or any(ord(ch) < 32 for ch in result):
        raise ValueError("Token text metadata is unavailable")
    return result


async def collect_transfers(request: InvestigationRequest, rpc: RpcReader, *,
                            deadline_seconds: float = 120) -> EvidenceSet:
    """Collect evidence without retries. Failed reads remain public observations.

    RPC credentials/endpoints are never copied to the public evidence object.
    The optional metadata uses EIP-1898 hash-pinned eth_call only; it does not
    fall back to latest state when the node cannot serve historical state.
    """
    if not isinstance(request, InvestigationRequest):
        request = InvestigationRequest.model_validate(request)
    if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float)) or not 0 < deadline_seconds <= 180:
        raise ValueError("Collection deadline must be between 0 and 180 seconds")
    observations: list[dict[str, Any]] = []
    result = EvidenceSet(
        request=request,
        coverage=Coverage(status="error", requested_from_block=request.from_block,
                          requested_to_block=request.to_block),
        raw={"rpc_observations": observations},
        warnings=["RPC observations depend on the configured provider; hashes establish integrity, not factual truth."],
    )

    async def observe(method: str, params: list[Any]) -> tuple[str, Any]:
        observation_id = f"rpc:{len(observations) + 1}"
        entry: dict[str, Any] = {"observation_id": observation_id, "method": method, "params": params}
        observations.append(entry)
        try:
            data = await rpc.call(method, params)
            entry["result"] = data
            return observation_id, data
        except asyncio.CancelledError:
            entry["error"] = "RPC read interrupted"
            raise
        except Exception as exc:
            entry["error"] = _public_error(exc)
            raise

    async def execute() -> None:
        _, chain = await observe("eth_chainId", [])
        if parse_quantity(chain) != request.chain_id:
            raise ValueError("Configured RPC returned a different chain")
        _, finalized = await observe("eth_getBlockByNumber", ["finalized", False])
        result.finalized_anchor = _anchor(finalized, tag="finalized")
        if request.to_block > result.finalized_anchor.number:
            result.coverage.issues.append("Requested end block exceeds the finalized anchor")
            return
        if request.to_block == result.finalized_anchor.number:
            result.window_end_anchor = result.finalized_anchor.model_copy(update={"tag": "window_end"})
        else:
            _, end_header = await observe("eth_getBlockByNumber", [hex(request.to_block), False])
            result.window_end_anchor = _anchor(end_header, tag="window_end", expected_number=request.to_block)

        ranges = [(start, min(start + request.log_chunk_size - 1, request.to_block))
                  for start in range(request.from_block, request.to_block + 1, request.log_chunk_size)]
        result.coverage.planned_queries = len(ranges) * 2
        if result.coverage.planned_queries > 40:
            result.coverage.issues.append("Requested chunk plan exceeds the 40-query collection limit")
            return
        records = {}
        transaction_blocks = {}
        for start, end in ranges:
            for direction in ("outgoing", "incoming"):
                topic = address_topic(request.address)
                topics = [TRANSFER_TOPIC, topic, None] if direction == "outgoing" else [TRANSFER_TOPIC, None, topic]
                params = [{"address": request.token_address, "fromBlock": hex(start), "toBlock": hex(end), "topics": topics}]
                observation_id = f"rpc:{len(observations) + 1}"
                coverage = QueryCoverage(direction=direction, from_block=start, to_block=end,
                                         status="error", observation_id=observation_id)
                result.coverage.queries.append(coverage)
                try:
                    _, logs = await observe("eth_getLogs", params)
                    if not isinstance(logs, list) or len(logs) > 5_000:
                        raise ValueError("Log response is invalid or exceeds the bounded result limit")
                    coverage.returned_logs = len(logs)
                    for log in logs:
                        try:
                            record = decode_transfer_log(log, request, direction=direction,
                                                         observation_id=observation_id, query_from=start, query_to=end)
                            block_identity = (record.block_number, record.block_hash)
                            prior_block = transaction_blocks.get(record.transaction_hash)
                            if prior_block is not None and prior_block != block_identity:
                                raise ValueError("A transaction appeared in conflicting finalized-window blocks")
                            transaction_blocks[record.transaction_hash] = block_identity
                            identity = transfer_identity(record)
                            if identity in records:
                                existing = records[identity]
                                if transfer_payload(existing) != transfer_payload(record):
                                    raise ValueError("Conflicting payloads share one log identity")
                                existing.observation_ids = sorted(set(existing.observation_ids + record.observation_ids))
                                existing.directions = sorted(set(existing.directions + record.directions))
                            else:
                                records[identity] = record
                            coverage.accepted_logs += 1
                        except (ValueError, TypeError, KeyError):
                            coverage.rejected_logs += 1
                    if coverage.rejected_logs:
                        coverage.error = "Returned logs failed transfer validation or conflicted with an existing identity"
                    else:
                        coverage.status = "complete"
                        result.coverage.completed_queries += 1
                except Exception as exc:
                    coverage.error = _public_error(exc)
                except asyncio.CancelledError:
                    coverage.error = "RPC read interrupted"
                    raise
                result.transfers = sorted(records.values(), key=lambda item: (
                    item.block_number, item.transaction_index if item.transaction_index is not None else -1,
                    item.log_index, item.transaction_hash, item.block_hash))

        _finish_coverage(result)
        result.alerts = build_alerts(result.transfers, request)
        result.metadata = TokenMetadata(block_anchor=result.window_end_anchor)
        block_reference = {"blockHash": result.window_end_anchor.block_hash, "requireCanonical": True}
        for field, selector in (("decimals", "0x313ce567"), ("symbol", "0x95d89b41"), ("name", "0x06fdde03")):
            try:
                observation_id, data = await observe("eth_call", [{"to": request.token_address, "data": selector}, block_reference])
                result.metadata.observation_ids.append(observation_id)
                if field == "decimals":
                    if not isinstance(data, str) or len(data) != 66 or not data.startswith("0x"):
                        raise ValueError("Invalid decimals ABI word")
                    value = int(data, 16)
                    if not 0 <= value <= 255:
                        raise ValueError("Decimals exceeds uint8")
                    result.metadata.decimals = value
                else:
                    setattr(result.metadata, field, _decode_abi_text(data))
            except Exception as exc:
                result.metadata.errors.append(f"{field}: {_public_error(exc)}")
        _finish_metadata(result.metadata)

    try:
        async with asyncio.timeout(deadline_seconds):
            await execute()
    except Exception as exc:
        if result.coverage.planned_queries and result.coverage.completed_queries == result.coverage.planned_queries:
            result.metadata.errors.append(_public_error(exc))
            result.warnings.append("Optional metadata collection was interrupted after log coverage completed.")
            _finish_metadata(result.metadata)
        else:
            result.coverage.issues.append(_public_error(exc))
        _finish_coverage(result)
        result.alerts = build_alerts(result.transfers, request)
    # Pydantic may copy dict values during construction, so bind observations explicitly.
    result.raw = {"rpc_observations": observations}
    return result


def _finish_coverage(result: EvidenceSet) -> None:
    coverage = result.coverage
    if coverage.planned_queries and coverage.completed_queries == coverage.planned_queries and not coverage.issues:
        coverage.status = "complete" if result.transfers else "empty"
    elif coverage.completed_queries or result.transfers:
        coverage.status = "partial"
    else:
        coverage.status = "error"


def _finish_metadata(metadata: TokenMetadata) -> None:
    present = sum(getattr(metadata, field) is not None for field in ("decimals", "symbol", "name"))
    metadata.status = "complete" if present == 3 else "partial" if present else "unavailable"
