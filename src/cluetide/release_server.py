"""Release-only application with an explicit deployment origin."""

from __future__ import annotations

import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from .releases import ReleaseService
from .releases_api import create_release_router
from .request_limits import RequestBodyLimitMiddleware
from .settings import LOCAL_DATA, ROOT


def _deployment_origin(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid public application origin") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or port not in {None, 443}):
        raise ValueError("The public application origin must be an HTTPS origin")
    return f"https://{parsed.hostname}", parsed.hostname


def create_release_app(*, service: ReleaseService | None = None,
                       public_origin: str | None = None,
                       codespaces_origin_rewrite: bool = False,
                       frontend_origins: tuple[str, ...] = (),
                       dist: Path | None = None) -> FastAPI:
    """Expose release APIs without the investigation or paid-model endpoints."""
    origin, host = _deployment_origin(public_origin)
    frontend_origins = tuple(_deployment_origin(value)[0] for value in frontend_origins)
    if codespaces_origin_rewrite and (
            not host or len(host.split(".")[0]) > 63
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?-8787\.app\.github\.dev", host)):
        raise ValueError("Codespaces origin rewriting requires a port 8787 app.github.dev origin")
    service = service or ReleaseService(LOCAL_DATA / "release-witness")
    allowed_hosts = {"127.0.0.1", "localhost", "testserver"}
    if host:
        allowed_hosts.add(host)
    local_origins = {
        f"http://{name}:{port}"
        for name in ("127.0.0.1", "localhost")
        for port in (8787, 5177, 4177)
    }
    application = FastAPI(title="ClueTide Agent Release Witness", docs_url=None, redoc_url=None)

    @application.middleware("http")
    async def application_boundary(request: Request, call_next):
        if request.url.hostname not in allowed_hosts:
            return JSONResponse({"detail": "Application host is not configured"}, status_code=403)
        incoming_origin = request.headers.get("origin")
        rewritten_same_origin = (
            codespaces_origin_rewrite
            and incoming_origin == "https://localhost:8787"
            and (request.url.hostname == host or (
                request.headers.getlist("host") == ["localhost:8787"]
                and request.headers.getlist("x-forwarded-host") == [host]
            ))
            and request.headers.get("sec-fetch-site") == "same-origin"
        )
        if (incoming_origin and incoming_origin not in local_origins | set(frontend_origins) | ({origin} if origin else set())
                and not rewritten_same_origin):
            return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
        if request.method != "GET" and request.headers.get("sec-fetch-site") == "cross-site" and incoming_origin not in frontend_origins:
            return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self' https://rpc.botchain.ai; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    application.add_middleware(RequestBodyLimitMiddleware)
    if frontend_origins:
        application.add_middleware(CORSMiddleware, allow_origins=list(frontend_origins),
                                  allow_methods=["GET", "POST"],
                                  allow_headers=["Content-Type", "X-ClueTide-Task-Key"],
                                  allow_credentials=False, max_age=600)

    @application.get("/api/health")
    def health():
        return {"status": "ok", "application": "agent_release_witness"}

    application.include_router(create_release_router(service))
    static = dist if dist is not None else ROOT / "frontend" / "dist"
    if static.exists():
        application.mount("/", StaticFiles(directory=static, html=True), name="release-product")
    return application


app = create_release_app(
    public_origin=os.environ.get("CLUETIDE_PUBLIC_ORIGIN"),
    codespaces_origin_rewrite=os.environ.get("CLUETIDE_CODESPACES_ORIGIN_REWRITE") == "1",
    frontend_origins=tuple(filter(None, os.environ.get("CLUETIDE_FRONTEND_ORIGINS", "").split(","))),
)
