"""Common HTTP plumbing: response envelope, error handling, middleware, auth, health."""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from fastapi import Depends, FastAPI, Query, Request, Security
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from starlette.exceptions import HTTPException as StarletteHTTPException

from .context import CORRELATION_HEADER, correlation_id_var, get_correlation_id, sanitize_correlation_id
from .timeutil import to_iso, to_naive_utc

log = logging.getLogger("obs_common.api")

ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3, "service": 3}

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "gateway_timeout",
}


class AppError(Exception):
    """Raised by handlers to produce a structured error envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any = None,
        *,
        source: str | None = None,
        service: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.source = source
        self.service = service
        self.headers = headers or {}


def _encode(data: Any) -> Any:
    return jsonable_encoder(data, custom_encoder={datetime: to_iso})


def envelope(
    data: Any = None,
    *,
    meta: dict | None = None,
    warnings: list | None = None,
    error: dict | None = None,
) -> dict:
    meta = dict(meta or {})
    cid = get_correlation_id()
    if cid:
        meta.setdefault("correlation_id", cid)
    return {"data": _encode(data), "meta": _encode(meta), "warnings": _encode(warnings or []), "error": error}


def ok(
    data: Any = None,
    *,
    status_code: int = 200,
    meta: dict | None = None,
    warnings: list | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(envelope(data, meta=meta, warnings=warnings), status_code=status_code, headers=headers)


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    details: Any = None,
    source: str = "service",
    service: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    err = {"code": code, "message": message, "details": _encode(details), "source": source, "service": service}
    hdrs = dict(headers or {})
    hdrs.setdefault("X-Error-Source", source)
    return JSONResponse(envelope(None, error=err), status_code=status_code, headers=hdrs)


def page_meta(page: int, page_size: int, total: int) -> dict:
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if page_size else 0,
    }


@dataclass
class PageParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def pagination(
    page: int = Query(1, ge=1, le=100000, description="1-based page number"),
    page_size: int = Query(25, ge=1, le=200, description="Items per page (max 200)"),
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


@dataclass
class TimeRange:
    start: datetime | None
    end: datetime | None


def time_range(
    start: datetime | None = Query(None, description="ISO-8601 start (inclusive)"),
    end: datetime | None = Query(None, description="ISO-8601 end (exclusive)"),
) -> TimeRange:
    s = to_naive_utc(start) if start else None
    e = to_naive_utc(end) if end else None
    if s and e and e <= s:
        raise AppError(422, "invalid_time_range", "'end' must be later than 'start'", {"start": to_iso(s), "end": to_iso(e)})
    return TimeRange(s, e)


# ---------------------------------------------------------------- auth (internal)

_internal_scheme = APIKeyHeader(name="X-Internal-Token", auto_error=False, description="Service-to-service token")


async def require_internal(request: Request, token: str | None = Security(_internal_scheme)) -> None:
    expected = request.app.state.settings.internal_api_token
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise AppError(401, "unauthorized", "Missing or invalid internal token")


def current_role(request: Request) -> str:
    role = (request.headers.get("X-User-Role") or "service").lower()
    return role if role in ROLE_RANK else "viewer"


def current_user(request: Request) -> str:
    return (request.headers.get("X-User-Name") or "service")[:100]


def require_role(minimum: str) -> Callable:
    """Defence in depth: services re-check the role the gateway forwarded."""

    def dependency(request: Request) -> str:
        role = current_role(request)
        if ROLE_RANK[role] < ROLE_RANK[minimum]:
            raise AppError(403, "forbidden", f"Requires role '{minimum}' or higher")
        return role

    return Depends(dependency)


# ---------------------------------------------------------------- middleware


class CorrelationMiddleware:
    """Reads/creates X-Correlation-ID, exposes it via contextvar, echoes it in the response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
        cid = sanitize_correlation_id(headers.get(CORRELATION_HEADER.lower()))
        token = correlation_id_var.set(cid)
        scope.setdefault("state", {})["correlation_id"] = cid
        try:
            from opentelemetry import trace  # local import keeps the dependency optional

            span = trace.get_current_span()
            if span.is_recording():
                span.set_attribute("correlation_id", cid)
        except Exception:  # pragma: no cover - otel not installed/active
            pass

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                hdrs = list(message.get("headers", []))
                hdrs.append((CORRELATION_HEADER.lower().encode(), cid.encode()))
                message["headers"] = hdrs
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            correlation_id_var.reset(token)


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                hdrs = list(message.get("headers", []))
                hdrs.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"cache-control", b"no-store"),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                message["headers"] = hdrs
            await send(message)

        await self.app(scope, receive, send_wrapper)


class _BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Rejects request bodies larger than max_bytes (Content-Length and streamed bodies)."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            return await self._reject(scope, receive, send)

        received = 0
        started = False

        async def counting_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(scope, receive, send)

    async def _reject(self, scope, receive, send):
        response = error_response(
            413, "payload_too_large", f"Request body exceeds the {self.max_bytes} byte limit"
        )
        await response(scope, receive, send)


# ---------------------------------------------------------------- handlers & health


def _validation_details(exc: RequestValidationError) -> list[dict]:
    # Never echo user input back (it may contain secrets or hostile content).
    return [
        {"field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"), "message": err.get("msg", "invalid")}
        for err in exc.errors()
    ]


def install_common(
    app: FastAPI,
    *,
    settings: Any,
    readiness_checks: dict[str, Callable[[], bool]] | None = None,
) -> None:
    app.state.settings = settings
    service = settings.service_name

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(CorrelationMiddleware)

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return error_response(
            exc.status_code,
            exc.code,
            exc.message,
            details=exc.details,
            source=exc.source or "service",
            service=exc.service or service,
            headers=exc.headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code = _STATUS_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ")
        return error_response(exc.status_code, code, message, service=service, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return error_response(
            422, "validation_error", "Request validation failed", details=_validation_details(exc), service=service
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"path": request.url.path})
        # Never leak stack traces or infrastructure details to clients.
        return error_response(500, "internal_error", "An internal error occurred", service=service)

    checks = readiness_checks or {}

    @app.get("/health", tags=["health"], summary="Liveness probe", include_in_schema=True)
    async def health():
        return {"status": "ok", "service": service}

    @app.get("/ready", tags=["health"], summary="Readiness probe (dependencies)")
    async def ready():
        results: dict[str, bool] = {}
        for name, check in checks.items():
            try:
                results[name] = bool(check())
            except Exception:
                results[name] = False
        healthy = all(results.values())
        return JSONResponse(
            {"status": "ready" if healthy else "not_ready", "service": service, "checks": results},
            status_code=200 if healthy else 503,
        )
