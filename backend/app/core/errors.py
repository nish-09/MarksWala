"""Application errors and the handlers that turn them into safe, actionable API responses.

Every error body has the shape {"error": {"code", "message", "details"?, "request_id"}}.
Internal exceptions are logged with a request id and never echoed to the client.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("MarksWala.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: Any = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class Unprocessable(AppError):
    status_code = 422
    code = "unprocessable"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedMedia(AppError):
    status_code = 415
    code = "unsupported_media_type"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"


def _body(request: Request, code: str, message: str, details: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None)}
    if details is not None:
        err["details"] = details
    return {"error": err}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse(_body(request, exc.code, exc.message, exc.details), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = [
            {"field": ".".join(str(p) for p in e["loc"] if p not in ("body", "query", "path")), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            _body(request, "validation_error", "Some fields are invalid.", fields),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        msg = {404: "Not found.", 405: "Method not allowed."}.get(exc.status_code, str(exc.detail))
        return JSONResponse(_body(request, f"http_{exc.status_code}", msg), status_code=exc.status_code)

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError):
        log.warning("integrity error: %s", exc.orig, extra={"request_id": getattr(request.state, "request_id", None)})
        return JSONResponse(
            _body(request, "conflict", "That change conflicts with existing data (duplicate or in-use record)."),
            status_code=409,
        )

    @app.exception_handler(OperationalError)
    async def _db_down(request: Request, exc: OperationalError):
        log.error("database unavailable: %s", exc, extra={"request_id": getattr(request.state, "request_id", None)})
        return JSONResponse(
            _body(request, "database_unavailable", "The database is temporarily unavailable. Please retry shortly."),
            status_code=503,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"request_id": getattr(request.state, "request_id", None)})
        return JSONResponse(
            _body(request, "internal_error", "Something went wrong on our side. Please try again; if it persists, contact support with the request id."),
            status_code=500,
        )
