"""Bounded English release API, independently mountable on a release-only server."""
import json

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .releases import (ChainRequest, CreateTask, CreateVersion, ExecuteRequest, MAX_REQUEST_BYTES,
                       ReadRequest, ReleaseError, ReleaseService, VerifyRequest, attempt_identifier, identifier)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _finite(_):
    raise ValueError()


def create_release_router(service: ReleaseService) -> APIRouter:
    router = APIRouter(prefix="/api/releases", tags=["Controlled releases"])

    def invoke(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except ReleaseError as exc:
            raise HTTPException(exc.status, detail=exc.message) from None
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
            raise HTTPException(422, detail="The release request or stored record could not be validated.") from None
        except OSError:
            raise HTTPException(503, detail="The release storage is unavailable. Preserve the execution state and retry verification.") from None

    async def body(request, model):
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > MAX_REQUEST_BYTES:
                raise HTTPException(413, detail="The release request exceeds the size limit.")
        try:
            value = json.loads(payload, object_pairs_hook=_unique, parse_constant=_finite)
            return model.model_validate(value)
        except (ValueError, TypeError, UnicodeError, RecursionError, ValidationError):
            raise HTTPException(422, detail="The release request could not be validated.") from None

    async def call(operation, *args, **kwargs):
        return await run_in_threadpool(invoke, operation, *args, **kwargs)

    @router.get("/config")
    async def config():
        return await call(service.config)

    @router.get("/tasks")
    async def tasks():
        return await call(service.list_tasks)

    @router.post("/tasks", status_code=201)
    async def create_task(request: Request):
        return await call(service.create_task, await body(request, CreateTask))

    @router.get("/tasks/{task_id}")
    async def task(task_id: str):
        return await call(service.get_task, task_id)

    @router.post("/tasks/{task_id}/versions", status_code=201)
    async def version(task_id: str, request: Request):
        return await call(service.create_version, task_id, await body(request, CreateVersion))

    @router.get("/tasks/{task_id}/bundle")
    async def bundle(task_id: str, version: str | None = None):
        version_id = None
        if version is not None:
            if not version.isascii() or not version.isdigit() or len(version) > 4 or not 1 <= int(version) <= 1000:
                raise HTTPException(422, detail="A valid release version is required.")
            version_id = int(version)
        payload = await call(service.bundle, task_id, version_id)
        return Response(payload, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="release-bundle.zip"', "Cache-Control": "no-store"})

    @router.post("/prepare")
    async def prepare(request: Request):
        return await call(service.prepare, await body(request, ChainRequest))

    @router.post("/verify")
    async def verify(request: Request):
        return await call(service.verify, await body(request, VerifyRequest))

    @router.post("/read")
    async def read(request: Request):
        return await call(service.read, await body(request, ReadRequest))

    @router.post("/tasks/{task_id}/execute")
    async def execute(task_id: str, request: Request):
        return await call(service.execute, task_id, await body(request, ExecuteRequest))

    @router.get("/attempts/{attempt_id}/receipt")
    async def receipt(attempt_id: str, task_id: str | None = None):
        return await call(service.receipt, attempt_id, task_id)

    @router.post("/receipts/verify")
    async def verify_receipt(request: Request):
        class ReceiptRequest(ReadRequest):
            envelope: dict
        value = await body(request, ReceiptRequest)
        if value.attempt_id is None or value.version_id is not None:
            raise HTTPException(422, detail="A task and execution attempt are required for receipt verification.")
        return await call(service.verify_receipt, value.task_id, value.attempt_id, value.envelope)

    def artifact_response(payload):
        return Response(payload, media_type="text/plain; charset=utf-8", headers={
            "Content-Disposition": 'attachment; filename="artifact.html"', "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'"})

    @router.get("/tasks/{task_id}/artifact")
    async def artifact(task_id: str):
        return artifact_response(await call(service.artifact, task_id))

    @router.get("/attempts/{attempt_id}/artifact")
    async def attempt_artifact(attempt_id: str, task_id: str | None = None):
        return artifact_response(await call(service.artifact, task_id, attempt_id))

    return router
