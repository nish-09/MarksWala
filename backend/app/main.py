"""MarksWala API application."""
from __future__ import annotations

import logging
import time
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.routers import register_routers
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging

log = logging.getLogger("MarksWala.api")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="MarksWala API",
        version="1.0.0",
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/api/openapi.json",
    )
    install_error_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        # CSRF defence in depth (cookies are also SameSite=Lax): browsers always send Origin on
        # state-changing requests, so a foreign origin is rejected outright.
        if request.method in UNSAFE:
            origin = request.headers.get("origin")
            if origin is None and (ref := request.headers.get("referer")):
                p = urlparse(ref)
                origin = f"{p.scheme}://{p.netloc}"
            if origin is not None and origin not in settings.cors_origin_list:
                host_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
                if origin != host_origin:
                    return JSONResponse(
                        {"error": {"code": "bad_origin", "message": "Cross-origin request rejected.", "request_id": request.state.request_id}},
                        status_code=403,
                    )
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "same-origin"
        log.info("%s %s -> %s (%.0f ms)", request.method, request.url.path, response.status_code, (time.perf_counter() - started) * 1000)
        return response

    api = APIRouter(prefix="/api")
    register_routers(api)
    app.include_router(api)

    @app.get("/api/health", tags=["health"])
    def health():
        return {"status": "ok"}

    @app.get("/api/health/ready", tags=["health"])
    def ready():
        """Probes every dependency; 503 if any is down."""
        checks: dict[str, str] = {}
        try:
            from app.db.session import engine

            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as e:
            checks["database"] = f"error: {type(e).__name__}"
        try:
            from app.services.ratelimit import get_redis

            get_redis().ping()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = f"error: {type(e).__name__}"
        try:
            from app.providers.registry import get_vector_store

            get_vector_store().ping()
            checks["qdrant"] = "ok"
        except Exception as e:
            checks["qdrant"] = f"error: {type(e).__name__}"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)

    return app


app = create_app()
