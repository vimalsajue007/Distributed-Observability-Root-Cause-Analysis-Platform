"""API Gateway: authentication (JWT), RBAC, routing, request audit, system health."""

from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import jwt
from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from obs_common.api import ROLE_RANK, AppError, install_common, ok, page_meta, pagination, PageParams
from obs_common.context import get_correlation_id
from obs_common.db import Database
from obs_common.http import DownstreamClient, DownstreamError, downstream_to_app_error
from obs_common.logging_setup import configure_logging
from obs_common.security import create_token, decode_token, hash_password, verify_password
from obs_common.telemetry import HttpSink, TelemetrySink, setup_telemetry
from obs_common.timeutil import utcnow

from .config import Settings
from .models import GatewayRequest, User
from .routes import ROUTES, SCENARIO_HEADER, Route

log = logging.getLogger("api-gateway")
sample_log = logging.getLogger("api-gateway.sample")  # shipped to the platform (sample traffic only)

UPSTREAM_NAMES = {
    "query": "query-service", "rca": "rca-service", "metrics": "metrics-service", "order": "order-service",
}
SCENARIO_PATTERN = re.compile(r"^[a-z0-9_]{1,40}$")
# Only /api/v1/sample is traced: every other URL is excluded by this negative look-ahead.
NOT_SAMPLE = r"^(?!.*/api/v1/sample)"
_DUMMY_HASH = hash_password("dummy-password-for-timing", iterations=1000)
bearer = HTTPBearer(auto_error=False, description="JWT obtained from POST /api/v1/auth/login")


@dataclass
class Principal:
    username: str
    role: str


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class LoginGuard:
    """Per-username+client lockout after repeated failures (in-memory, per gateway instance)."""

    def __init__(self, max_failures: int, window_seconds: int):
        self.max, self.window = max_failures, window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        items = [t for t in self._failures.get(key, []) if now - t < self.window]
        self._failures[key] = items
        return items

    def retry_after(self, key: str) -> int:
        with self._lock:
            now = time.monotonic()
            items = self._recent(key, now)
            if len(items) >= self.max:
                return max(1, int(self.window - (now - items[0])))
            return 0

    def fail(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._recent(key, now).append(now)

    def success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    sink: TelemetrySink | None = None,
    transports: dict | None = None,
) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    settings.validate_secrets()
    configure_logging(settings.service_name, settings.log_level)
    db = db or Database(settings.database_url)
    transports = transports or {}
    guard = LoginGuard(settings.login_max_failures, settings.login_lockout_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await run_in_threadpool(seed_users)
        except Exception as exc:  # noqa: BLE001
            log.error("could not seed users (is the database migrated?)", extra={"error": repr(exc)})
        yield
        if telemetry:
            telemetry.shutdown()
        for c in {*clients.values(), *health_clients.values()}:
            await c.aclose()

    app = FastAPI(
        title="Observability Platform API Gateway", version="1.0.0",
        description="Single public entry point. Authenticates users (JWT), enforces roles and routes to internal services.\n\n"
                    "**Roles:** viewer < operator < admin. Responses use the envelope `{data, meta, warnings, error}`; "
                    "`error.source` is `gateway` for gateway-originated failures and `downstream` (with `error.service`) otherwise.",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None, redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    install_common(app, settings=settings, readiness_checks={"database": db.ping})
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                           allow_methods=["GET", "POST", "PUT", "PATCH"], allow_headers=["Authorization", "Content-Type", "X-Scenario", "X-Correlation-ID"])

    telemetry = None
    if settings.telemetry_enabled:
        the_sink = sink or HttpSink(settings.ingestion_service_url, settings.ingestion_api_key)
        telemetry = setup_telemetry(app, service_name="api-gateway", sink=the_sink, logger_name="api-gateway.sample",
                                    include_prefixes=("/api/v1/sample",), excluded_urls=f"health,ready,docs,openapi.json,redoc,{NOT_SAMPLE}")

    def mk(key: str, url: str, timeout: float, retries: int = 1) -> DownstreamClient:
        return DownstreamClient(UPSTREAM_NAMES.get(key, key), url, timeout=timeout, retries=retries, backoff=settings.downstream_backoff_seconds,
                                internal_token=settings.internal_api_token, transport=transports.get(UPSTREAM_NAMES.get(key, key)),
                                instrument=telemetry.instrument_client if (telemetry and key == "order") else None)

    clients: dict[str, DownstreamClient] = {
        "query": mk("query", settings.query_service_url, settings.gateway_timeout_seconds),
        "rca": mk("rca", settings.rca_service_url, settings.gateway_timeout_seconds),
        "metrics": mk("metrics", settings.metrics_service_url, settings.gateway_timeout_seconds),
        "order": mk("order", settings.order_service_url, settings.sample_timeout_seconds, retries=0),
    }
    health_targets = {
        "ingestion-service": settings.ingestion_service_url, "trace-service": settings.trace_service_url, "log-service": settings.log_service_url,
        "metrics-service": settings.metrics_service_url, "rca-service": settings.rca_service_url, "query-service": settings.query_service_url,
        "order-service": settings.order_service_url, "payment-service": settings.payment_service_url, "notification-service": settings.notification_service_url,
    }
    health_clients = {
        name: DownstreamClient(name, url, timeout=2.0, retries=0, transport=transports.get(f"health:{name}") or transports.get(name))
        for name, url in health_targets.items()
    }
    app.state.db, app.state.clients, app.state.health_clients, app.state.telemetry = db, clients, health_clients, telemetry

    # ------------------------------------------------------------------ users / auth

    def seed_users() -> None:
        with db.session() as s:
            existing = {u.username for u in s.scalars(select(User))}
            for username, role, pw in (("admin", "admin", settings.seed_admin_password), ("operator", "operator", settings.seed_operator_password),
                                       ("viewer", "viewer", settings.seed_viewer_password)):
                if username not in existing:
                    s.add(User(username=username, role=role, password_hash=hash_password(pw, iterations=settings.password_iterations)))

    def authenticate(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
        if creds is None or creds.scheme.lower() != "bearer":
            raise AppError(401, "unauthorized", "Authentication required", source="gateway", headers={"WWW-Authenticate": "Bearer"})
        try:
            claims = decode_token(settings.jwt_secret, creds.credentials)
        except jwt.ExpiredSignatureError:
            raise AppError(401, "token_expired", "Session expired, please sign in again", source="gateway", headers={"WWW-Authenticate": "Bearer"}) from None
        except jwt.PyJWTError:
            raise AppError(401, "unauthorized", "Invalid token", source="gateway", headers={"WWW-Authenticate": "Bearer"}) from None
        with db.session() as s:
            user = s.scalar(select(User).where(User.username == claims["sub"]))
            if user is None or not user.is_active:
                raise AppError(401, "unauthorized", "Account is disabled or no longer exists", source="gateway")
            principal = Principal(user.username, user.role)  # role comes from the DB, not the token
        request.state.principal = principal
        return principal

    def needs(minimum: str):
        def dep(principal: Principal = Depends(authenticate)) -> Principal:
            if ROLE_RANK[principal.role] < ROLE_RANK[minimum]:
                raise AppError(403, "forbidden", f"This action requires the '{minimum}' role", source="gateway")
            return principal
        return dep

    @app.post("/api/v1/auth/login", tags=["auth"], summary="Sign in and receive a JWT")
    def login(body: LoginBody, request: Request):
        key = f"{body.username.lower()}|{request.client.host if request.client else '-'}"
        wait = guard.retry_after(key)
        if wait:
            raise AppError(429, "too_many_attempts", "Too many failed sign-in attempts. Try again later.", source="gateway", headers={"Retry-After": str(wait)})
        with db.session() as s:
            user = s.scalar(select(User).where(User.username == body.username))
            valid = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)  # constant-ish time either way
            if not (user and user.is_active and valid):
                guard.fail(key)
                log.warning("failed login", extra={"username": body.username[:64]})
                raise AppError(401, "invalid_credentials", "Invalid username or password", source="gateway")
            user.last_login_at = utcnow()
            username, role = user.username, user.role
        guard.success(key)
        token = create_token(settings.jwt_secret, subject=username, role=role, ttl_seconds=settings.jwt_ttl_seconds)
        return ok({"access_token": token, "token_type": "bearer", "expires_in": settings.jwt_ttl_seconds, "user": {"username": username, "role": role}})

    @app.get("/api/v1/auth/me", tags=["auth"], summary="Current user")
    def me(principal: Principal = Depends(authenticate)):
        return ok({"username": principal.username, "role": principal.role})

    # ------------------------------------------------------------------ audit

    def record(request: Request, status: int, started: float, upstream: str | None, error_code: str | None) -> None:
        principal = getattr(request.state, "principal", None)
        try:
            with db.session() as s:
                s.add(GatewayRequest(
                    correlation_id=(get_correlation_id() or "-")[:64], method=request.method, path=request.url.path[:255], status_code=status,
                    duration_ms=int((time.monotonic() - started) * 1000), username=principal.username if principal else None,
                    upstream=upstream, error_code=error_code))
                cutoff = utcnow() - timedelta(days=settings.request_log_retention_days)
                if int(started) % 50 == 0:  # opportunistic retention
                    s.query(GatewayRequest).filter(GatewayRequest.created_at < cutoff).delete()
        except Exception as exc:  # noqa: BLE001
            log.warning("could not record gateway request", extra={"error": repr(exc)})

    # ------------------------------------------------------------------ proxy

    def relay(upstream: str, status: int, payload: Any) -> JSONResponse:
        if not isinstance(payload, dict):
            raise AppError(502, "downstream_error", f"{UPSTREAM_NAMES[upstream]} returned an invalid response", source="downstream", service=UPSTREAM_NAMES[upstream])
        err = payload.get("error")
        if status >= 400 and isinstance(err, dict):
            if err.get("source", "service") == "service":
                err["source"] = "downstream"
            err.setdefault("service", UPSTREAM_NAMES[upstream])
        return JSONResponse(payload, status_code=status)

    def make_handler(route: Route):
        async def handler(request: Request) -> JSONResponse:
            started = time.monotonic()
            principal: Principal = request.state.principal
            is_sample = route.tag == "sample"
            status, code = 500, None
            try:
                headers = {"X-User-Role": principal.role, "X-User-Name": principal.username}
                scenario = request.headers.get(SCENARIO_HEADER)
                if is_sample and scenario:
                    if not SCENARIO_PATTERN.match(scenario):
                        raise AppError(422, "invalid_scenario", "X-Scenario may only contain lowercase letters, digits and underscores", source="gateway")
                    headers[SCENARIO_HEADER] = scenario
                body = await request.body() if request.method in ("POST", "PUT", "PATCH") else b""
                if body:
                    headers["Content-Type"] = request.headers.get("content-type", "application/json")
                target = route.target.format(**request.path_params)
                if is_sample:
                    sample_log.info("sample request", extra={"scenario": scenario or "normal", "user": principal.username})
                try:
                    resp = await clients[route.upstream].send(request.method, target, params=list(request.query_params.multi_items()) or None,
                                                              content=body or None, headers=headers)
                except DownstreamError as exc:
                    raise downstream_to_app_error(exc) from exc
                try:
                    payload = resp.json()
                except ValueError:
                    payload = None
                if resp.status_code >= 500 and not (isinstance(payload, dict) and payload.get("error")):
                    raise AppError(502, "downstream_error", f"{UPSTREAM_NAMES[route.upstream]} returned an invalid or failing response",
                                   source="downstream", service=UPSTREAM_NAMES[route.upstream])
                status = resp.status_code
                if status >= 400 and isinstance(payload, dict):
                    code = (payload.get("error") or {}).get("code")
                if is_sample:
                    sample_log.info("sample request finished", extra={"status_code": status})
                return relay(route.upstream, status, payload)
            except AppError as exc:
                status, code = exc.status_code, exc.code
                raise
            finally:
                await run_in_threadpool(record, request, status, started, UPSTREAM_NAMES[route.upstream], code)
        return handler

    for r in ROUTES:
        app.add_api_route(r.path, make_handler(r), methods=[r.method], tags=[r.tag], summary=f"{r.summary} (requires {r.role})",
                          dependencies=[Depends(needs(r.role))], response_class=JSONResponse)

    # ------------------------------------------------------------------ system

    @app.get("/api/v1/system/health", tags=["system"], summary="Health of every platform service", dependencies=[Depends(needs("viewer"))])
    async def system_health():
        import asyncio

        async def probe(name: str, client: DownstreamClient) -> dict[str, Any]:
            t0 = time.monotonic()
            try:
                resp = await client.send("GET", "/ready")
                ms = round((time.monotonic() - t0) * 1000, 1)
                try:
                    body = resp.json()
                except ValueError:
                    body = {}
                checks = (body.get("data") or body.get("checks") or {}) if isinstance(body, dict) else {}
                state = "up" if resp.status_code == 200 else "degraded"
                return {"service_name": name, "status": state, "latency_ms": ms, "http_status": resp.status_code, "checks": checks, "detail": None if state == "up" else "dependency check failing"}
            except DownstreamError as exc:
                return {"service_name": name, "status": "down", "latency_ms": None, "http_status": None, "checks": {}, "detail": f"{exc.kind}: {exc.message}"}

        results = await asyncio.gather(*(probe(n, c) for n, c in health_clients.items()))
        db_ok = await run_in_threadpool(db.ping)
        results = [{"service_name": "api-gateway", "status": "up" if db_ok else "degraded", "latency_ms": 0.0, "http_status": 200, "checks": {"database": db_ok}, "detail": None}, *results]
        states = {r["status"] for r in results}
        overall = "down" if "down" in states else "degraded" if "degraded" in states else "up"
        return ok({"status": overall, "services": results, "checked_at": utcnow()})

    @app.get("/api/v1/system/requests", tags=["system"], summary="Recent gateway requests (admin)", dependencies=[Depends(needs("admin"))])
    def recent_requests(page: PageParams = Depends(pagination)):
        with db.session() as s:
            total = s.scalar(select(func.count()).select_from(GatewayRequest)) or 0
            rows = s.scalars(select(GatewayRequest).order_by(GatewayRequest.id.desc()).offset(page.offset).limit(page.page_size)).all()
            data = [{"id": r.id, "correlation_id": r.correlation_id, "method": r.method, "path": r.path, "status_code": r.status_code, "duration_ms": r.duration_ms,
                     "username": r.username, "upstream": r.upstream, "error_code": r.error_code, "created_at": r.created_at} for r in rows]
        return ok(data, meta=page_meta(page.page, page.page_size, total))

    return app

# Run with: uvicorn api_gateway.main:create_app --factory
