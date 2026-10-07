import asyncio
import json

import httpx
import pytest

from cluetide.rpc import ReadOnlyRpcClient, RpcError


def test_http_error_never_includes_endpoint_or_response_credentials():
    def handler(request):
        return httpx.Response(401, text="Authorization: Bearer synthetic-private-value")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            rpc = ReadOnlyRpcClient("https://rpc.invalid/?key=synthetic-private-value", http_client=http)
            with pytest.raises(RpcError) as error:
                await rpc.call("eth_chainId", [])
            assert str(error.value) == "RPC HTTP status 401"
            assert rpc.calls_attempted == 1
    asyncio.run(run())


def test_failed_rpc_response_counts_budget_and_no_automatic_retry():
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={"jsonrpc":"2.0", "id": payload["id"],
            "error":{"code":-32000, "message":"sensitive upstream diagnostics"}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            rpc = ReadOnlyRpcClient("https://rpc.invalid", max_calls=1, http_client=http)
            with pytest.raises(RpcError, match="code -32000"):
                await rpc.call("eth_chainId", [])
            with pytest.raises(RpcError, match="budget exhausted"):
                await rpc.call("eth_chainId", [])
            assert rpc.calls_attempted == 1
    asyncio.run(run())
    assert len(requests) == 1


def test_response_id_and_missing_result_are_rejected():
    async def run(response):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))) as http:
            rpc = ReadOnlyRpcClient("https://rpc.invalid", http_client=http)
            with pytest.raises(RpcError):
                await rpc.call("eth_chainId", [])
    asyncio.run(run({"jsonrpc":"2.0", "id":2, "result":"0x1"}))
    asyncio.run(run({"jsonrpc":"2.0", "id":1}))


@pytest.mark.parametrize("response_id", [True, 1.0, "1", None])
def test_response_id_requires_exact_integer_type(response_id):
    async def run():
        response = {"jsonrpc": "2.0", "id": response_id, "result": "0x1"}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))) as http:
            rpc = ReadOnlyRpcClient("https://rpc.invalid", http_client=http)
            with pytest.raises(RpcError, match="envelope is invalid"):
                await rpc.call("eth_chainId", [])
            assert rpc.calls_attempted == 1
    asyncio.run(run())


def test_readonly_validation_blocks_network_before_request():
    requests = []
    def handler(request):
        requests.append(request)
        raise AssertionError("Forbidden calls must not reach transport")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            rpc = ReadOnlyRpcClient("https://rpc.invalid", http_client=http)
            with pytest.raises(ValueError):
                await rpc.call("eth_sendRawTransaction", ["0x00"])
            assert rpc.calls_attempted == 0
    asyncio.run(run())
    assert requests == []


@pytest.mark.parametrize("body", [
    b'{"jsonrpc":"2.0","id":1,"id":1,"result":"0x1"}',
    b'{"jsonrpc":"2.0","id":1,"result":NaN}',
])
def test_invalid_json_extensions_are_rejected(body):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as http:
            rpc = ReadOnlyRpcClient("https://rpc.invalid", http_client=http)
            with pytest.raises(RpcError, match="JSON response failure"):
                await rpc.call("eth_chainId", [])
    asyncio.run(run())
