"""Deterministic reports for completed collection with no matching transfers."""
from .schemas import EvidenceSet


def empty_window_outcome(evidence: EvidenceSet) -> dict:
    """Describe successful empty queries without inventing a model execution."""
    coverage = evidence.coverage
    if coverage.status != "empty" or evidence.transfers or not evidence.finalized_anchor:
        raise ValueError("A deterministic empty report requires a finalized empty collection")
    observations = {item.get("observation_id"): item for item in evidence.raw.get("rpc_observations", [])}
    ids = [query.observation_id for query in coverage.queries]
    if not ids or coverage.completed_queries != coverage.planned_queries or any(
        "error" in observations.get(identifier, {})
        or observations[identifier].get("method") != "eth_getLogs"
        or observations[identifier].get("result") != [] for identifier in ids
    ):
        raise ValueError("Empty collection queries lack successful raw observations")
    evidence_id = "collection:empty-transfer-queries"
    summary = "在本次已 finalized 的有限窗口中，两个方向的日志查询均完成，未返回匹配该地址与 ERC-20 的 Transfer。"
    return {
        "status": "completed", "execution_source": "deterministic_collection",
        "report": {
            "summary": summary, "classification": "unresolved",
            "claims": [{"text": summary, "evidence_ids": [evidence_id], "interpretation": False}],
            "limitations": ["结果仅描述所选节点、本次窗口与过滤条件；不证明窗口外没有活动。",
                            "节点返回与 finalized 锚点未构成独立共识证明。",
                            "本报告由采集结果确定性生成，没有调用模型。"],
        },
        "evidence": [{"evidence_id": evidence_id, "kind": "empty_transfer_queries", "status": "ok",
                      "payload": {"source_observation_ids": ids, "query_count": len(ids),
                                  "from_block": evidence.request.from_block, "to_block": evidence.request.to_block,
                                  "address": evidence.request.address, "token_address": evidence.request.token_address,
                                  "matching_transfer_count": 0}}],
        "trace": [{"event": "deterministic_empty_window", "query_count": len(ids)}],
        "model_requests": 0, "tool_attempts": 0, "model_names": [], "stop_reason": None,
    }
