"""Routes return bounded public diagnostics without submitted-field echo."""

import httpx
import pytest
from fastapi import FastAPI
from eth_utils import keccak

from cluetide.bot import BotError, BotService
from cluetide.bot_api import create_bot_router
from test_bot import CASE_ID, CONTRACT, MockNode


@pytest.fixture
def api():
    app = FastAPI()
    service = BotService(lambda _: {})
    app.include_router(create_bot_router(lambda _: {}, service=service))
    return app, service


@pytest.mark.asyncio
async def test_networks_route_public_fixed_metadata(api):
    app, _ = api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/bot/networks")
    assert response.status_code == 200
    assert {n["chain_id"] for n in response.json()["networks"]} == {677, 968}
    assert response.json()["artifact"]["evm_version"] == "paris"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,payload", [
    ("prepare", {"chain_id": 968, "action": "deploy", "account": "sensitive account input"}),
    ("prepare", {"private_key": "sensitive credential input"}),
    ("verify", {"transaction_hash": "sensitive hash input"}),
    ("read", ["sensitive malformed request"]),
])
async def test_request_validation_uses_fixed_public_code(api, path, payload):
    app, _ = api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/bot/" + path, json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_bot_request"}
    assert "sensitive" not in response.text


@pytest.mark.asyncio
async def test_request_byte_limit_and_sanitized_business_error(api, monkeypatch):
    app, service = api
    def fail(_):
        raise BotError("rpc_transport_error")
    monkeypatch.setattr(service, "prepare", fail)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        huge = await client.post("/api/bot/prepare", content=b" " * 16385)
        error = await client.post("/api/bot/prepare", json={"chain_id": 968, "action": "deploy", "account": "0x" + "1" * 40})
    assert huge.status_code == 413
    assert huge.json() == {"detail": "bot_request_too_large"}
    assert error.status_code == 503
    assert error.json() == {"detail": "bot_rpc_unavailable"}


@pytest.mark.asyncio
async def test_request_duplicate_keys_use_fixed_code(api):
    app, _ = api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/bot/prepare", content=b'{"chain_id":968,"chain_id":677}')
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_bot_request"}


@pytest.mark.asyncio
async def test_deep_json_within_byte_limit_uses_fixed_validation_code(api):
    app, _ = api
    payload = b"[" * 1100 + b"0" + b"]" * 1100
    assert len(payload) < 16384
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/bot/prepare", content=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_bot_request"}


@pytest.mark.asyncio
async def test_read_route_distinguishes_unregistered_case_from_rpc_failure():
    node = MockNode()
    service = BotService(lambda _: {}, client_factory=node.client)
    node.values["eth_getCode"] = service.artifact().runtime
    node.values["eth_call"] = {"error": {"code": -32000, "message": "execution reverted",
        "data": "0x" + keccak(text="UnknownCase()")[:4].hex()}}
    app = FastAPI()
    app.include_router(create_bot_router(lambda _: {}, service=service))
    body = {"chain_id": 968, "contract_address": CONTRACT, "case_id": CASE_ID}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post("/api/bot/read", json=body)
        assert missing.status_code == 200
        assert missing.json()["case_status"] == "unregistered"
        assert missing.json()["getter_state"]["case"] is None
        node.values["eth_call"] = {"error": {"code": -1, "message": "private provider details"}}
        failure = await client.post("/api/bot/read", json=body)
        assert failure.status_code == 503
        assert failure.json() == {"detail": "bot_rpc_unavailable"}
        invalid_offset = await client.post("/api/bot/read", json={**body, "review_offset": True})
        assert invalid_offset.status_code == 422
        assert invalid_offset.json() == {"detail": "invalid_bot_request"}
