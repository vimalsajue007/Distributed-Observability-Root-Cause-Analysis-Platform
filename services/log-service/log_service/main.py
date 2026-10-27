"""Log Processing Service - normalises logs, links them to traces, exposes search APIs."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Path, Query
from sqlalchemy import func, select

from obs_common.api import AppError, PageParams, install_common, ok, page_meta, pagination, require_internal, require_role
from obs_common.broker import ALL_SPECS, LOG_SPEC, Broker, RabbitBroker
from obs_common.db import Database, escape_like
from obs_common.filters import LogFilters
from obs_common.logging_setup import configure_logging
from obs_common.validation import SERVICE_NAME_PATTERN

from .config import Settings
from .models import ErrorPattern, ProcessedLog
from .processor import LogProcessor

log = logging.getLogger("log-processing")


def log_dict(row: ProcessedLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_id": row.event_id,
        "trace_id": row.trace_id,
        "span_id": row.span_id,
        "correlation_id": row.correlation_id,
        "service_name": row.service_name,
        "severity": row.severity,
        "message": row.message,
        "timestamp": row.timestamp,
        "created_at": row.created_at,
        "logger": row.logger,
        "attributes": row.attributes or {},
    }


def build_conditions(f: LogFilters) -> list:
    cond = []
    if f.severity:
        cond.append(ProcessedLog.severity.in_(f.severity))
    if f.service_name:
        cond.append(ProcessedLog.service_name == f.service_name)
    if f.trace_id:
        cond.append(ProcessedLog.trace_id == f.trace_id)
    if f.span_id:
        cond.append(ProcessedLog.span_id == f.span_id)
    if f.correlation_id:
        cond.append(ProcessedLog.correlation_id == f.correlation_id)
    if f.start:
        cond.append(ProcessedLog.timestamp >= f.start)
    if f.end:
        cond.append(ProcessedLog.timestamp < f.end)
    if f.q:
        cond.append(ProcessedLog.message.like(f"%{escape_like(f.q)}%", escape="\\"))
    return cond


def create_app(settings: Settings | None = None, *, broker: Broker | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(settings.service_name, settings.log_level)
    db = db or Database(settings.database_url)
    broker = broker or RabbitBroker(settings.broker_url)
    processor = LogProcessor(db, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            broker.declare_topology(ALL_SPECS)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not declare topology yet", extra={"error": repr(exc)})
        broker.start_consumer(LOG_SPEC, processor.handle_event)
        yield
        broker.stop()

    app = FastAPI(
        title="Log Processing Service",
        version="1.0.0",
        description="Normalises logs, de-duplicates them, correlates them with traces and detects repeated error patterns.",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    install_common(app, settings=settings, readiness_checks={"database": db.ping, "broker_consumer": broker.consumers_running})
    app.state.db, app.state.processor = db, processor
    # Log data is access controlled: operators and admins only (the gateway forwards the role).
    guard = [Depends(require_internal), require_role("operator")]

    @app.get("/api/v1/logs", tags=["logs"], dependencies=guard, summary="Search logs")
    def search_logs(f: LogFilters = Depends()):
        cond = build_conditions(f)
        order = (ProcessedLog.timestamp.asc(), ProcessedLog.id.asc()) if f.order == "asc" else (ProcessedLog.timestamp.desc(), ProcessedLog.id.desc())
        with db.session() as s:
            total = s.scalar(select(func.count()).select_from(ProcessedLog).where(*cond)) or 0
            rows = s.scalars(select(ProcessedLog).where(*cond).order_by(*order).offset((f.page - 1) * f.page_size).limit(f.page_size)).all()
            data = [log_dict(r) for r in rows]
        return ok(data, meta=page_meta(f.page, f.page_size, total))

    # NOTE: declared before /logs/{log_id} so "patterns" is not parsed as an id.
    @app.get("/api/v1/logs/patterns", tags=["logs"], dependencies=guard, summary="Repeated error patterns")
    def error_patterns(
        service_name: str | None = Query(None, pattern=SERVICE_NAME_PATTERN),
        min_occurrences: int = Query(1, ge=1),
        page: PageParams = Depends(pagination),
    ):
        cond = [ErrorPattern.occurrence_count >= min_occurrences]
        if service_name:
            cond.append(ErrorPattern.service_name == service_name.lower())
        with db.session() as s:
            total = s.scalar(select(func.count()).select_from(ErrorPattern).where(*cond)) or 0
            rows = s.scalars(
                select(ErrorPattern).where(*cond).order_by(ErrorPattern.occurrence_count.desc(), ErrorPattern.id).offset(page.offset).limit(page.page_size)
            ).all()
            data = [
                {
                    "id": r.id, "service_name": r.service_name, "pattern": r.pattern, "sample_message": r.sample_message,
                    "severity": r.severity, "occurrence_count": r.occurrence_count, "first_seen": r.first_seen,
                    "last_seen": r.last_seen, "last_trace_id": r.last_trace_id,
                }
                for r in rows
            ]
        return ok(data, meta=page_meta(page.page, page.page_size, total))

    @app.get("/api/v1/logs/{log_id}", tags=["logs"], dependencies=guard, summary="Get one log record")
    def get_log(log_id: int = Path(..., ge=1)):
        with db.session() as s:
            row = s.get(ProcessedLog, log_id)
            if row is None:
                raise AppError(404, "log_not_found", f"Log {log_id} was not found")
            return ok(log_dict(row))

    return app

# Run with: uvicorn log_service.main:create_app --factory
