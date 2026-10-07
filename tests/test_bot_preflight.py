"""Fixed-block review cursors, retry boundaries, and public intent compatibility."""

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading

from eth_abi import decode
from eth_utils import keccak
from fastapi import FastAPI
import httpx
import pytest
from pydantic import ValidationError

from cluetide.bot import (BotError, PrepareRequest, PrepareSubmission, ReadRequest,
                         ReviewPreflightRequest, VerifyRequest)
from cluetide.bot_api import create_bot_router
from test_bot import (AUTHOR, BLOCK_HASH, CASE_ID, CONTRACT, REVIEWER, TX_HASH, MockNode,
                      mined_deployment, request, saved, service, setup_inventory)


REVIEW_GETTER = "0x" + keccak(text="getReview(uint256)")[:4].hex()


def review_request(**patch):
    return request("add_review", account=REVIEWER, onchain_version_id="7", local_review_id=21, **patch)


class ChainView:
    """Keep independently readable snapshots as the head and canonical hashes change."""

    def __init__(self, saved, *, count=13):
        self.node = MockNode()
        self.svc = service(self.node, saved)
        self.saved, self.snapshots, self.hashes = saved, {}, {}
        self.head = 100
        self.add(100, count=count)
        self.node.values["eth_blockNumber"] = lambda _: hex(self.head)
        self.node.values["eth_getBlockByNumber"] = self.block
        self.node.values["eth_call"] = self.call
        self.node.values["eth_getCode"] = self.svc.artifact().runtime

    def add(self, block, *, count, versions=2):
        mock = MockNode()
        version_records, review_records = setup_inventory(mock, self.svc, self.saved,
                                                          version_count=versions, review_count=count)
        for record in review_records.values():
            record[4] = AUTHOR
        self.snapshots[block] = (mock.values["eth_call"], version_records, review_records)
        self.hashes[block] = "0x" + format(block, "064x")
        self.head = block
        return version_records, review_records

    def block(self, payload):
        number = int(payload["params"][0], 16)
        if number not in self.hashes:
            return None
        return {"number": hex(number), "hash": self.hashes[number], "transactions": []}

    def call(self, payload):
        number = int(payload["params"][1], 16)
        fixture_payload = copy.deepcopy(payload)
        fixture_payload["params"][1] = "0x64"
        return self.snapshots[number][0](fixture_payload)

    def page(self, cursor=None, prepared=None):
        return self.svc.review_preflight({"prepared": prepared or review_request(), "cursor": cursor})

    def ready(self):
        result = self.page()
        while result["status"] == "scanning":
            result = self.page(result["cursor"])
        return result


def test_preflight_completes_large_inventory_with_bounded_pages_and_prepares(saved):
    chain = ChainView(saved)
    with pytest.raises(BotError, match="review_preflight_required"):
        chain.svc.prepare(review_request())
    chain.node.calls.clear()
    first = chain.page()
    assert first == {"cursor": first["cursor"], "status": "scanning", "scanned_count": "6",
                     "total_count": "13", "read_block_number": "100", "read_block_hash": chain.hashes[100],
                     "existing_review_id": None}
    assert len(chain.node.calls) == 19
    with pytest.raises(BotError, match="review_preflight_required"):
        chain.svc.prepare({**review_request(), "review_preflight_cursor": first["cursor"]})
    second = chain.page(first["cursor"])
    assert second["scanned_count"] == "12" and second["status"] == "scanning"
    third = chain.page(first["cursor"])
    assert third["status"] == "ready" and third["scanned_count"] == "13"
    assert max(call["id"] for call in chain.node.calls) <= 20
    chain.node.calls.clear()
    plan = chain.svc.prepare({**review_request(), "review_preflight_cursor": first["cursor"]})
    assert plan["commitments"]["onchain_version_id"] == "7"
    assert plan["fees"]["sufficient_balance"] is True
    assert all(not call["params"][0]["data"].startswith(REVIEW_GETTER)
               for call in chain.node.calls if call["method"] == "eth_call")
    assert all(call["params"][1] == "0x64" for call in chain.node.calls if call["method"] == "eth_call")


def test_preflight_detects_duplicate_on_later_page_and_blocks_prepare(saved):
    chain = ChainView(saved, count=9)
    chain.snapshots[100][2][38][4] = REVIEWER
    first = chain.page()
    result = chain.page(first["cursor"])
    assert result["status"] == "duplicate" and result["existing_review_id"] == "38"
    assert result["scanned_count"] == "9"
    with pytest.raises(BotError, match="review_exists"):
        chain.svc.prepare({**review_request(), "review_preflight_cursor": result["cursor"]})


@pytest.mark.parametrize("failure_stage", ["getter", "closing_anchor"])
def test_failed_page_retries_from_last_committed_offset(saved, failure_stage):
    chain = ChainView(saved)
    first = chain.page()
    original_call, original_block = chain.node.values["eth_call"], chain.node.values["eth_getBlockByNumber"]
    if failure_stage == "getter":
        def fail(payload):
            data = payload["params"][0]["data"]
            if data.startswith(REVIEW_GETTER) and decode(["uint256"], bytes.fromhex(data[10:]))[0] == 40:
                raise httpx.ReadTimeout("provider-private-content")
            return original_call(payload)
        chain.node.values["eth_call"] = fail
    else:
        anchors = 0
        def fail(payload):
            nonlocal anchors
            anchors += 1
            if anchors == 2:
                raise httpx.ReadTimeout("provider-private-content")
            return original_block(payload)
        chain.node.values["eth_getBlockByNumber"] = fail
    with pytest.raises(BotError, match="rpc_transport_error"):
        chain.page(first["cursor"])
    assert chain.svc._review_cursors[first["cursor"]].scanned_count == 6
    chain.node.values["eth_call"], chain.node.values["eth_getBlockByNumber"] = original_call, original_block
    retried = chain.page(first["cursor"])
    assert retried["scanned_count"] == "12"
    assert chain.page(first["cursor"])["status"] == "ready"


def test_read_pages_share_original_anchor_as_head_advances(saved):
    chain = ChainView(saved, count=8)
    body = {"chain_id": 968, "contract_address": CONTRACT, "case_id": CASE_ID}
    first = chain.svc.read(body)
    chain.add(101, count=10, versions=3)
    second = chain.svc.read({**body, "review_offset": 3, "read_block_number": first["read_block_number"],
                             "read_block_hash": first["read_block_hash"]})
    assert second["read_block_number"] == "100"
    assert second["getter_state"]["case"]["review_count"] == "8"
    assert second["getter_state"]["inventory"]["reviews"]["total_count"] == "8"
    assert [item["review_id"] for item in second["getter_state"]["reviews"]] == ["34", "35", "36"]
    chain.hashes[100] = BLOCK_HASH
    with pytest.raises(BotError, match="read_block_changed"):
        chain.svc.read({**body, "read_block_number": "100", "read_block_hash": first["read_block_hash"]})


@pytest.mark.parametrize("anchor", [
    {"read_block_number": "100"}, {"read_block_hash": BLOCK_HASH},
    {"read_block_number": 100, "read_block_hash": BLOCK_HASH},
    {"read_block_number": "01", "read_block_hash": BLOCK_HASH},
    {"read_block_number": "-1", "read_block_hash": BLOCK_HASH},
    {"read_block_number": "100", "read_block_hash": "private-input"},
])
def test_read_anchors_are_strict_paired_public_values(anchor):
    with pytest.raises(ValidationError):
        ReadRequest.model_validate({"chain_id": 968, "contract_address": CONTRACT, "case_id": CASE_ID, **anchor})


def test_ready_cursor_follows_count_growth_and_keeps_exact_older_version(saved):
    chain = ChainView(saved, count=7)
    ready = chain.ready()
    chain.add(101, count=10, versions=3)
    with pytest.raises(BotError, match="review_preflight_changed"):
        chain.svc.prepare({**review_request(), "review_preflight_cursor": ready["cursor"]})
    cursor = chain.svc._review_cursors[ready["cursor"]]
    assert (cursor.scanned_count, cursor.total_count, cursor.block_number) == (7, 10, 101)
    chain.node.calls.clear()
    continued = chain.page(ready["cursor"])
    assert continued["status"] == "ready" and continued["scanned_count"] == "10"
    review_ids = [decode(["uint256"], bytes.fromhex(call["params"][0]["data"][10:]))[0]
                  for call in chain.node.calls if call["method"] == "eth_call"
                  and call["params"][0]["data"].startswith(REVIEW_GETTER)]
    assert review_ids == [38, 39, 40]
    plan = chain.svc.prepare({**review_request(), "review_preflight_cursor": ready["cursor"]})
    assert plan["commitments"]["onchain_version_id"] == "7"
    assert plan["read_block_number"] == "101"


def test_head_growth_without_new_reviews_keeps_ready_cursor(saved):
    chain = ChainView(saved, count=7)
    ready = chain.ready()
    chain.add(101, count=7, versions=4)
    plan = chain.svc.prepare({**review_request(), "review_preflight_cursor": ready["cursor"]})
    assert plan["commitments"]["onchain_version_id"] == "7"
    assert chain.svc._review_cursors[ready["cursor"]].block_number == 100


def test_new_duplicate_in_tail_is_found_after_count_growth(saved):
    chain = ChainView(saved, count=7)
    ready = chain.ready()
    _, reviews = chain.add(101, count=8)
    reviews[38][4] = REVIEWER
    with pytest.raises(BotError, match="review_preflight_changed"):
        chain.svc.prepare({**review_request(), "review_preflight_cursor": ready["cursor"]})
    assert chain.page(ready["cursor"])["status"] == "duplicate"


@pytest.mark.parametrize("change", ["reorg", "count_rollback"])
def test_prepare_invalidates_cursor_after_canonical_or_inventory_rollback(saved, change):
    chain = ChainView(saved, count=7)
    ready = chain.ready()
    if change == "reorg":
        chain.hashes[100] = BLOCK_HASH
    else:
        chain.add(101, count=6)
    with pytest.raises(BotError, match="review_preflight_invalid"):
        chain.svc.prepare({**review_request(), "review_preflight_cursor": ready["cursor"]})
    with pytest.raises(BotError, match="review_preflight_invalid"):
        chain.page(ready["cursor"])


def test_page_checks_closing_anchor_before_publishing_progress(saved):
    chain = ChainView(saved)
    first = chain.page()
    original = chain.node.values["eth_getBlockByNumber"]
    seen = 0
    def reorganize(payload):
        nonlocal seen
        seen += 1
        if seen == 2:
            chain.hashes[100] = BLOCK_HASH
        return original(payload)
    chain.node.values["eth_getBlockByNumber"] = reorganize
    with pytest.raises(BotError, match="review_preflight_invalid"):
        chain.page(first["cursor"])
    assert chain.svc._review_cursors[first["cursor"]].scanned_count == 6


@pytest.mark.parametrize("change", ["account", "uri", "target", "saved_intent", "artifact"])
def test_cursor_binds_full_normalized_request_intent_and_artifact(saved, monkeypatch, change):
    chain = ChainView(saved, count=7)
    ready = chain.ready()
    prepared = review_request()
    if change == "account":
        prepared["account"] = AUTHOR
    elif change == "uri":
        prepared["evidence_uri"] = "https://example.com/review"
    elif change == "target":
        prepared["onchain_version_id"] = "8"
    elif change == "saved_intent":
        saved["reviews"][0]["decision"] = "supported"
    else:
        artifact = chain.svc.artifact()
        monkeypatch.setattr(chain.svc, "artifact", lambda: replace(artifact,
                            metadata={**artifact.metadata, "artifact_sha256": "a" * 64}))
    with pytest.raises(BotError, match="review_preflight_mismatch"):
        chain.svc.prepare({**prepared, "review_preflight_cursor": ready["cursor"]})


def test_expired_cursor_and_capacity_are_bounded(saved):
    chain = ChainView(saved, count=0)
    first = chain.page()
    chain.svc._review_cursors[first["cursor"]].touched -= 601
    with pytest.raises(BotError, match="review_preflight_expired"):
        chain.page(first["cursor"])
    results = [chain.page() for _ in range(64)]
    assert len(chain.svc._review_cursors) == 64
    assert len({item["cursor"] for item in results}) == 64
    with pytest.raises(BotError, match="review_preflight_capacity"):
        chain.page()
    chain.svc._review_cursors[results[0]["cursor"]].touched -= 601
    assert chain.page()["status"] == "ready"
    assert len(chain.svc._review_cursors) == 64


def test_cursor_lock_rejects_parallel_page_without_wait_or_double_advance(saved):
    chain = ChainView(saved)
    first = chain.page()
    entered, release = threading.Event(), threading.Event()
    original = chain.node.values["eth_chainId"]
    def pause(_):
        entered.set()
        assert release.wait(timeout=5)
        return original
    chain.node.values["eth_chainId"] = pause
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(chain.page, first["cursor"])
        assert entered.wait(timeout=5)
        try:
            with pytest.raises(BotError, match="review_preflight_busy"):
                chain.page(first["cursor"])
            with pytest.raises(BotError, match="review_preflight_busy"):
                chain.svc.prepare({**review_request(), "review_preflight_cursor": first["cursor"]})
        finally:
            release.set()
        assert future.result(timeout=5)["scanned_count"] == "12"
    assert chain.svc._review_cursors[first["cursor"]].scanned_count == 12


def test_prepare_submission_keeps_original_verify_request_schema():
    prepared = review_request()
    submission = PrepareSubmission.model_validate({**prepared, "review_preflight_cursor": "opaque-cursor"})
    assert PrepareRequest.model_validate(submission.model_dump(exclude={"review_preflight_cursor"})).action == "add_review"
    VerifyRequest.model_validate({"chain_id": 968, "transaction_hash": TX_HASH, "prepared": prepared})
    with pytest.raises(ValidationError):
        VerifyRequest.model_validate({"chain_id": 968, "transaction_hash": TX_HASH, "prepared": submission.model_dump()})
    with pytest.raises(ValidationError):
        ReviewPreflightRequest.model_validate({"prepared": request()})
    with pytest.raises(ValidationError):
        ReviewPreflightRequest.model_validate({"prepared": prepared, "scanned_count": 100})


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["eth_chainId", "eth_getTransactionReceipt", "eth_getTransactionByHash",
                                    "eth_getBlockByNumber", "eth_blockNumber", "eth_getCode"])
async def test_verify_rpc_failure_is_sanitized_503_then_same_intent_verifies(stage):
    node = MockNode()
    svc = service(node)
    mined_deployment(node, svc)
    original = node.values[stage]
    node.values[stage] = {"error": {"code": -32000, "message": "provider-private-content"}}
    app = FastAPI()
    app.include_router(create_bot_router(svc.required, service=svc))
    intent = {"chain_id": 968, "transaction_hash": TX_HASH, "prepared": request(), "minimum_confirmations": 2}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        failure = await client.post("/api/bot/verify", json=intent)
        assert failure.status_code == 503
        assert failure.json() == {"detail": "bot_rpc_unavailable"}
        node.values[stage] = original
        success = await client.post("/api/bot/verify", json=intent)
        assert success.status_code == 200 and success.json()["status"] == "verified"


@pytest.mark.parametrize("missing", ["transaction", "block_fields", "canonical", "membership", "head"])
def test_verify_incomplete_chain_views_remain_pending(missing):
    node = MockNode()
    svc = service(node)
    mined_deployment(node, svc)
    if missing == "transaction":
        node.values["eth_getTransactionByHash"] = None
    elif missing == "block_fields":
        node.values["eth_getTransactionByHash"].update(blockHash=None, blockNumber=None)
    elif missing == "canonical":
        node.values["eth_getBlockByNumber"] = None
    elif missing == "membership":
        node.values["eth_getBlockByNumber"]["transactions"] = []
    else:
        node.values["eth_blockNumber"] = "0x62"
    result = svc.verify({"chain_id": 968, "transaction_hash": TX_HASH, "prepared": request(), "minimum_confirmations": 2})
    assert result["status"] == "pending"


def test_six_review_compatibility_preserves_public_plan_with_bounded_optional_fees(saved):
    chain = ChainView(saved, count=6)
    plan = chain.svc.prepare(review_request())
    assert plan["transaction"]["gas"]
    assert plan["fees"]["gas_limit"] == "120000"
    assert plan["fees"]["gas_price_wei"] is None
    assert plan["fees"]["balance_wei"] is None
    assert {"gas_price_unavailable", "balance_unavailable"} <= set(plan["warnings"])
    assert len(chain.node.calls) == 20


@pytest.mark.parametrize("changed", ["runtime", "target_version"])
def test_ready_prepare_rechecks_current_runtime_and_exact_target(saved, changed):
    chain = ChainView(saved, count=7)
    ready = chain.ready()
    versions, _ = chain.add(101, count=7, versions=3)
    if changed == "runtime":
        chain.node.values["eth_getCode"] = "0x1234"
        expected = "contract_runtime_mismatch"
    else:
        versions[7][4] = bytes.fromhex("a" * 64)
        expected = "onchain_version_mismatch"
    with pytest.raises(BotError, match=expected):
        chain.svc.prepare({**review_request(), "review_preflight_cursor": ready["cursor"]})


@pytest.mark.asyncio
async def test_preflight_http_route_and_ephemeral_prepare_submission(saved):
    chain = ChainView(saved, count=7)
    app = FastAPI()
    app.include_router(create_bot_router(chain.svc.required, service=chain.svc))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        required = await client.post("/api/bot/prepare", json=review_request())
        assert required.status_code == 409 and required.json() == {"detail": "review_preflight_required"}
        first = await client.post("/api/bot/review-preflight", json={"prepared": review_request()})
        assert first.status_code == 200
        cursor = first.json()["cursor"]
        ready = await client.post("/api/bot/review-preflight", json={"prepared": review_request(), "cursor": cursor})
        assert ready.status_code == 200 and ready.json()["status"] == "ready"
        plan = await client.post("/api/bot/prepare", json={**review_request(), "review_preflight_cursor": cursor})
        assert plan.status_code == 200
        assert plan.json()["commitments"]["onchain_version_id"] == "7"
        injected = await client.post("/api/bot/review-preflight",
            json={"prepared": review_request(), "cursor": cursor, "scanned_count": "7"})
        assert injected.status_code == 422 and injected.json() == {"detail": "invalid_bot_request"}
        unsuitable = await client.post("/api/bot/review-preflight", json={"prepared": request()})
        assert unsuitable.status_code == 422


def test_lagging_prepare_node_preserves_cursor_for_retry(saved):
    chain = ChainView(saved, count=7)
    ready = chain.ready()
    chain.add(99, count=6)
    with pytest.raises(BotError, match="rpc_chain_view_unavailable"):
        chain.svc.prepare({**review_request(), "review_preflight_cursor": ready["cursor"]})
    cursor = chain.svc._review_cursors[ready["cursor"]]
    assert cursor.scanned_count == 7 and cursor.invalid is False and cursor.block_number == 100
    chain.head = 100
    assert chain.svc.prepare({**review_request(), "review_preflight_cursor": ready["cursor"]})["transaction"]["gas"]


@pytest.mark.parametrize("operation", ["page", "prepare"])
def test_temporarily_missing_anchor_keeps_cursor_retryable(saved, operation):
    chain = ChainView(saved, count=7)
    first = chain.page() if operation == "page" else chain.ready()
    original = chain.node.values["eth_getBlockByNumber"]
    chain.node.values["eth_getBlockByNumber"] = lambda _: None
    def invoke():
        return (chain.page(first["cursor"]) if operation == "page" else
                chain.svc.prepare({**review_request(), "review_preflight_cursor": first["cursor"]}))
    with pytest.raises(BotError, match="rpc_chain_view_unavailable"):
        invoke()
    cursor = chain.svc._review_cursors[first["cursor"]]
    assert cursor.invalid is False and cursor.scanned_count == int(first["scanned_count"])
    chain.node.values["eth_getBlockByNumber"] = original
    recovered = invoke()
    assert recovered["status"] == "ready" if operation == "page" else recovered["transaction"]["gas"]
