"""Actual ASGI bytes, including chunked multipart, determine admission."""

import json

from fastapi import Body, FastAPI, File, UploadFile
import httpx
import pytest

from cluetide.request_limits import (
    IMPORT_BODY_LIMIT_BYTES, JSON_BODY_LIMIT_BYTES, RequestBodyLimitMiddleware,
)


def scope(path="/api/investigations", method="POST", headers=()):
    return {"type": "http", "method": method, "path": path, "headers": list(headers)}


async def exercise(app, request_scope, chunks):
    incoming = [{"type": "http.request", "body": body, "more_body": index < len(chunks) - 1}
                for index, body in enumerate(chunks)]
    sent = []
    reads = 0

    async def receive():
        nonlocal reads
        reads += 1
        return incoming.pop(0)

    async def send(message):
        sent.append(message)

    await app(request_scope, receive, send)
    return sent, reads


async def read_then_reply(request_scope, receive, send):
    while (await receive()).get("more_body", False):
        pass
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.mark.asyncio
async def test_declared_oversize_is_rejected_before_app_or_receive():
    async def no_app(*args):
        raise AssertionError("Declared oversize must not enter parsing")

    middleware = RequestBodyLimitMiddleware(no_app)
    messages, reads = await exercise(middleware,
        scope(headers=[(b"content-length", str(JSON_BODY_LIMIT_BYTES + 1).encode())]), [b"ignored"])
    assert reads == 0
    assert messages[0]["status"] == 413
    assert len(messages) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")], [(b"content-length", b"bad")]])
async def test_actual_chunks_enforce_limit_even_without_truthful_length(headers):
    middleware = RequestBodyLimitMiddleware(read_then_reply, json_limit_bytes=8)
    messages, reads = await exercise(middleware, scope(headers=headers), [b"1234", b"5678", b"9"])
    assert reads == 3
    assert messages[0]["status"] == 413
    assert json.loads(messages[1]["body"]) == {"detail": "API request body exceeds the size limit"}
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_exact_limit_succeeds_and_chunks_are_forwarded_individually():
    observed = []

    async def app(request_scope, receive, send):
        while True:
            message = await receive()
            observed.append(message["body"])
            if not message["more_body"]:
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    messages, reads = await exercise(RequestBodyLimitMiddleware(app, json_limit_bytes=8),
                                    scope(), [b"12", b"", b"345678"])
    assert reads == 3 and observed == [b"12", b"", b"345678"]
    assert messages[0]["status"] == 200


@pytest.mark.asyncio
async def test_import_allows_multipart_overhead_with_separate_total_limit():
    middleware = RequestBodyLimitMiddleware(read_then_reply, json_limit_bytes=8, import_limit_bytes=12)
    accepted, _ = await exercise(middleware, scope(path="/api/bundles/import"), [b"123456789012"])
    rejected, _ = await exercise(middleware, scope(path="/api/bundles/import"), [b"1234567890123"])
    assert accepted[0]["status"] == 200
    assert rejected[0]["status"] == 413
    assert JSON_BODY_LIMIT_BYTES == 64 * 1024
    assert IMPORT_BODY_LIMIT_BYTES == 21 * 1024 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize("path,method", [
    ("/api/investigations/test/bundle", "GET"), ("/api/health", "GET"),
    ("/static/data", "POST"), ("/api-extra", "POST"),
])
async def test_get_export_and_non_api_paths_pass_through(path, method):
    messages, _ = await exercise(RequestBodyLimitMiddleware(read_then_reply, json_limit_bytes=1),
                                 scope(path=path, method=method), [b"long"])
    assert messages[0]["status"] == 200 and messages[1]["body"] == b"ok"


@pytest.mark.asyncio
async def test_parsing_error_response_cannot_replace_safe_413():
    async def swallowed(request_scope, receive, send):
        try:
            while (await receive()).get("more_body", False):
                pass
        except Exception:
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"private payload diagnostics"})

    messages, _ = await exercise(RequestBodyLimitMiddleware(swallowed, json_limit_bytes=1),
                                 scope(), [b"synthetic-private-payload"])
    assert messages[0]["status"] == 413
    assert b"private" not in messages[1]["body"]
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_unrelated_application_error_is_preserved():
    async def failed(*args):
        raise RuntimeError("unrelated")

    with pytest.raises(RuntimeError, match="unrelated"):
        await exercise(RequestBodyLimitMiddleware(failed), scope(), [b""])


@pytest.mark.asyncio
async def test_real_fastapi_chunked_json_and_multipart_parsers_still_return_413():
    app = FastAPI()
    handlers_called = []

    @app.post("/api/investigations")
    async def start(data: dict = Body(...)):
        handlers_called.append("json")
        return data

    @app.post("/api/bundles/import")
    async def upload(file: UploadFile = File(...)):
        handlers_called.append("multipart")
        return {"size": len(await file.read())}

    # Exercise the same BaseHTTPMiddleware boundary as the local workbench.
    @app.middleware("http")
    async def passthrough(request, call_next):
        return await call_next(request)

    app.add_middleware(RequestBodyLimitMiddleware, json_limit_bytes=32, import_limit_bytes=256)

    async def stream(body):
        for offset in range(0, len(body), 17):
            yield body[offset:offset + 17]

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        normal = await client.post("/api/investigations", json={"ok": True})
        assert normal.status_code == 200
        json_body = b'{"text":"' + b"synthetic-private-value" * 10 + b'"}'
        rejected_json = await client.post("/api/investigations", content=stream(json_body),
                                          headers={"content-type": "application/json"})
        assert "content-length" not in rejected_json.request.headers
        multipart = (b"--cluetide-boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"case.zip\"\r\n"
                     b"Content-Type: application/zip\r\n\r\n" + b"synthetic-private-value" * 30
                     + b"\r\n--cluetide-boundary--\r\n")
        rejected_multipart = await client.post("/api/bundles/import", content=stream(multipart),
            headers={"content-type": "multipart/form-data; boundary=cluetide-boundary"})
        for response in (rejected_json, rejected_multipart):
            assert response.status_code == 413
            assert response.json() == {"detail": "API request body exceeds the size limit"}
            assert "synthetic-private-value" not in response.text
        assert handlers_called == ["json"]
