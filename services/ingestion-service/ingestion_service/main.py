"""Telemetry Ingestion Service - validates telemetry and publishes it to the broker."""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from fastapi import Body, Depends, FastAPI, Query, Request, Security
from fastapi.concurrency import run_in_threadpool
from fastapi.security import APIKeyHeader
from sqlalchemy.exc import SQLAlchemyError

from obs_common.api import AppError, install_common, ok, require_internal, require_role
from obs_common.broker import ALL_SPECS, Broker, BrokerUnavailable, RabbitBroker
from obs_common.db import Database
from obs_common.logging_setup import configure_logging
from obs_common.timeutil import utcnow

from .config import Settings
from .models import Base
from .service import IngestResult, IngestionService

log = logging.getLogger("ingestion")

_api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False, description="Ingestion API key for instrumented applications")
_internal_scheme = APIKeyHeader(name="X-Internal-Token", auto_error=False)


async def authenticate_producer(
    request: Request,
    api_key: str | None = Security(_api_key_scheme),
    internal: str | None = Security(_internal_scheme),
) -> None:
    settings: Settings = request.app.state.settings
    if api_key and any(hmac.compare_digest(api_key.encode(), k.encode()) for k in settings.api_keys):
        return
    if internal and hmac.compare_digest(internal.encode(), settings.internal_api_token.encode()):
        return
    raise AppError(401, "unauthorized", "Missing or invalid X-API-Key")


def create_app(settings: Settings | None = None, *, broker: Broker | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(settings.service_name, settings.log_level)
    db = db or Database(settings.database_url)
    broker = broker or RabbitBroker(settings.broker_url)
    service = IngestionService(db, broker, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await run_in_threadpool(broker.declare_topology, ALL_SPECS)
        except BrokerUnavailable as exc:
            log.warning("broker not reachable at startup; events will be buffered", extra={"error": str(exc)})
        if settings.run_background_workers:
            service.start_background()
        yield
        service.stop()
        broker.stop()

    app = FastAPI(
        title="Telemetry Ingestion Service",
        version="1.0.0",
        description="Receives logs, metrics and traces, validates/normalises them and publishes them to RabbitMQ.",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    install_common(app, settings=settings, readiness_checks={"database": db.ping, "broker": broker.ping})
    app.state.db, app.state.broker, app.state.service = db, broker, service

    def _extract_events(body: Any, *, key: str = "events") -> list[Any]:
        if isinstance(body, dict) and isinstance(body.get(key), list):
            events = body[key]
        elif isinstance(body, dict) and key not in body:
            events = [body]  # a single event object
        else:
            raise AppError(422, "telemetry_rejected", f"Body must be an event object or {{\"{key}\": [...]}}")
        if len(events) > settings.max_batch_events:
            raise AppError(413, "batch_too_large", f"At most {settings.max_batch_events} events per request")
        return events

    async def _run(event_type: str, events: list[Any]) -> IngestResult:
        try:
            return await run_in_threadpool(service.ingest, event_type, events)
        except SQLAlchemyError:
            raise AppError(503, "storage_unavailable", "Events could not be durably accepted; please retry") from None

    def _respond(result: IngestResult):
        if result.accepted == 0 and result.rejected > 0:
            raise AppError(422, "telemetry_rejected", "All submitted events were rejected", result.errors[:100])
        warnings = []
        if result.queued_for_retry:
            warnings.append({"code": "broker_unavailable", "message": f"{result.queued_for_retry} event(s) buffered for re-publication"})
        if result.rejected:
            warnings.append({"code": "partial_rejection", "message": f"{result.rejected} event(s) were rejected"})
        return ok(result.as_dict(), status_code=202, warnings=warnings)

    auth = [Depends(authenticate_producer)]
    tags = ["telemetry"]

    @app.post("/api/v1/telemetry/logs", status_code=202, tags=tags, dependencies=auth, summary="Ingest log events (single or batch)")
    async def ingest_logs(body: Any = Body(...)):
        return _respond(await _run("log", _extract_events(body)))

    @app.post("/api/v1/telemetry/traces", status_code=202, tags=tags, dependencies=auth, summary="Ingest spans (single or batch)")
    async def ingest_traces(body: Any = Body(...)):
        return _respond(await _run("trace", _extract_events(body)))

    @app.post("/api/v1/telemetry/metrics", status_code=202, tags=tags, dependencies=auth, summary="Ingest metric samples (single or batch)")
    async def ingest_metrics(body: Any = Body(...)):
        return _respond(await _run("metric", _extract_events(body)))

    @app.post("/api/v1/telemetry/batch", status_code=202, tags=tags, dependencies=auth, summary="Ingest logs, traces and metrics in one request")
    async def ingest_batch(body: Any = Body(...)):
        if not isinstance(body, dict) or not any(isinstance(body.get(k), list) for k in ("logs", "traces", "metrics")):
            raise AppError(422, "telemetry_rejected", 'Body must be {"logs": [...], "traces": [...], "metrics": [...]}')
        total = IngestResult()
        count = 0
        for key, event_type in (("traces", "trace"), ("logs", "log"), ("metrics", "metric")):
            events = body.get(key) or []
            if not isinstance(events, list):
                raise AppError(422, "telemetry_rejected", f"'{key}' must be an array")
            count += len(events)
            if count > settings.max_batch_events:
                raise AppError(413, "batch_too_large", f"At most {settings.max_batch_events} events per request")
            if events:
                total.merge(await _run(event_type, events), key)
        return _respond(total)

    @app.get("/api/v1/telemetry/stats", tags=tags, dependencies=[Depends(require_internal), require_role("operator")], summary="Ingestion success/failure statistics")
    async def stats(window_minutes: int = Query(60, ge=1, le=10080)):
        since = utcnow() - timedelta(minutes=window_minutes)
        data = await run_in_threadpool(service.stats, since)
        return ok({**data, "window_minutes": window_minutes})

    @app.post("/api/v1/telemetry/republish", tags=["admin"], dependencies=[Depends(require_internal), require_role("admin")], summary="Force re-publication of buffered events")
    async def republish():
        published = await run_in_threadpool(service.republish_pending)
        return ok({"published": published})

    return app


# Run with: uvicorn ingestion_service.main:create_app --factory
