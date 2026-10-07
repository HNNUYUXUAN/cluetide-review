"""Stream-counted request limits before API JSON or multipart parsing."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send


JSON_BODY_LIMIT_BYTES = 64 * 1024
IMPORT_BODY_LIMIT_BYTES = 21 * 1024 * 1024
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_REJECTION_BODY = b'{"detail":"API request body exceeds the size limit"}'


class _RequestBodyTooLarge(Exception):
    """Fixed internal signal; neither body bytes nor headers are included."""


def _declared_too_large(headers: list[tuple[bytes, bytes]], limit: int) -> bool:
    bound = str(limit).encode("ascii")
    for name, value in headers:
        if name.lower() != b"content-length":
            continue
        value = value.strip()
        # Invalid or missing lengths still receive the actual stream-byte gate.
        # Comparing digit strings avoids parsing an unbounded integer header.
        if not value.isdigit():
            continue
        digits = value.lstrip(b"0") or b"0"
        if len(digits) > len(bound) or (len(digits) == len(bound) and digits > bound):
            return True
    return False


class RequestBodyLimitMiddleware:
    """Bound consumed API mutation bodies without buffering the request.

    FastAPI/Starlette may convert receive errors into parsing responses. Once
    the byte gate fires, those responses are suppressed and replaced by the
    same safe 413. Normal API handlers parse their body before responding.
    GET, exported responses, non-API paths and non-HTTP scopes pass through.
    """

    def __init__(self, app: ASGIApp, *, json_limit_bytes: int = JSON_BODY_LIMIT_BYTES,
                 import_limit_bytes: int = IMPORT_BODY_LIMIT_BYTES):
        if any(type(value) is not int or value <= 0 for value in (json_limit_bytes, import_limit_bytes)):
            raise ValueError("Request byte limits must be positive integers")
        self.app = app
        self.json_limit_bytes = json_limit_bytes
        self.import_limit_bytes = import_limit_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        method = scope.get("method", "").upper()
        if (scope["type"] != "http" or method not in _MUTATING_METHODS
                or not (path == "/api" or path.startswith("/api/"))):
            await self.app(scope, receive, send)
            return
        limit = (self.import_limit_bytes if method == "POST" and path == "/api/bundles/import"
                 else self.json_limit_bytes)
        rejected = False
        oversized = False
        received_bytes = 0

        async def reject() -> None:
            nonlocal rejected
            if rejected:
                return
            rejected = True
            await send({"type": "http.response.start", "status": 413, "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_REJECTION_BODY)).encode("ascii")),
                (b"x-content-type-options", b"nosniff"),
                (b"referrer-policy", b"no-referrer"),
            ]})
            await send({"type": "http.response.body", "body": _REJECTION_BODY, "more_body": False})

        if _declared_too_large(scope.get("headers", []), limit):
            await reject()
            return

        async def bounded_receive() -> Message:
            nonlocal received_bytes, oversized
            if oversized:
                raise _RequestBodyTooLarge
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > limit:
                    oversized = True
                    raise _RequestBodyTooLarge
            return message

        async def bounded_send(message: Message) -> None:
            if oversized:
                await reject()
                return
            await send(message)

        try:
            await self.app(scope, bounded_receive, bounded_send)
        except Exception:
            if not oversized:
                raise
            await reject()
        if oversized:
            await reject()
