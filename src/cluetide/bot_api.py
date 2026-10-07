"""FastAPI routes for unsigned BOT plans and read-only chain checks."""

from collections.abc import Callable
import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .bot import (BotError, BotService, PrepareSubmission, ReadRequest, ReviewPreflightRequest, VerifyRequest,
                  _finite_json_constant, _unique_object, _rpc_unavailable)


def create_bot_router(required: Callable[[str], dict], *, service: BotService | None = None) -> APIRouter:
    service = service or BotService(required)
    router = APIRouter(prefix="/api/bot", tags=["BOT registry"])

    def invoke(operation, *args):
        try:
            return operation(*args)
        except BotError as exc:
            if _rpc_unavailable(exc):
                raise HTTPException(503, detail="bot_rpc_unavailable") from None
            raise HTTPException(409, detail=exc.code) from None

    @router.get("/networks")
    def networks():
        return invoke(service.networks)

    async def read_body(request: Request, model):
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > 16384:
                raise HTTPException(413, detail="bot_request_too_large")
        try:
            body = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_finite_json_constant)
            return model.model_validate(body)
        except (ValueError, TypeError, UnicodeError, ValidationError, RecursionError):
            # Validation errors can contain the original field value. Public
            # responses use a fixed code rather than echoing submitted input.
            raise HTTPException(422, detail="invalid_bot_request") from None

    @router.post("/prepare")
    async def prepare(request: Request):
        body = await read_body(request, PrepareSubmission)
        return await run_in_threadpool(invoke, service.prepare, body)

    @router.post("/review-preflight")
    async def review_preflight(request: Request):
        body = await read_body(request, ReviewPreflightRequest)
        return await run_in_threadpool(invoke, service.review_preflight, body)

    @router.post("/verify")
    async def verify(request: Request):
        body = await read_body(request, VerifyRequest)
        return await run_in_threadpool(invoke, service.verify, body)

    @router.post("/read")
    async def read(request: Request):
        body = await read_body(request, ReadRequest)
        return await run_in_threadpool(invoke, service.read, body)

    return router
