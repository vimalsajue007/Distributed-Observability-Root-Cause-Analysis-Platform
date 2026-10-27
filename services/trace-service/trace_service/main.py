"""Trace Processing Service - consumes span events, reconstructs traces, serves trace/dependency queries."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Depends, FastAPI, Path

from obs_common.api import AppError, PageParams, install_common, ok, page_meta, pagination, require_internal, require_role
from obs_common.broker import ALL_SPECS, TRACE_SPEC, Broker, RabbitBroker
from obs_common.db import Database
from obs_common.filters import TraceListFilters, WindowFilters, WindowOnly
from obs_common.logging_setup import configure_logging
from obs_common.timeutil import utcnow
from obs_common.validation import SERVICE_NAME_PATTERN, TRACE_ID_PATTERN

from .config import Settings
from .processor import TraceProcessor
from .queries import dependency_graph, list_traces, load_reconstruction, span_dict, trace_detail

log = logging.getLogger("trace-processing")

TraceIdPath = Path(..., pattern=TRACE_ID_PATTERN, description="32 hex character trace id")


def create_app(settings: Settings | None = None, *, broker: Broker | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(settings.service_name, settings.log_level)
    db = db or Database(settings.database_url)
    broker = broker or RabbitBroker(settings.broker_url)
    processor = TraceProcessor(db, broker, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            broker.declare_topology(ALL_SPECS)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not declare topology yet", extra={"error": repr(exc)})
        broker.start_consumer(TRACE_SPEC, processor.handle_event)
        if settings.run_background_workers:
            processor.start_background()
        yield
        processor.stop()
        broker.stop()

    app = FastAPI(
        title="Trace Processing Service",
        version="1.0.0",
        description="Groups spans into traces, reconstructs parent-child relationships and derives the service dependency graph.",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    install_common(
        app,
        settings=settings,
        readiness_checks={"database": db.ping, "broker_consumer": broker.consumers_running},
    )
    app.state.db, app.state.processor = db, processor
    guard = [Depends(require_internal)]

    @app.get("/api/v1/traces", tags=["traces"], dependencies=guard, summary="Search traces")
    def get_traces(f: TraceListFilters = Depends()):
        with db.session() as s:
            items, total = list_traces(s, f)
        return ok(items, meta=page_meta(f.page, f.page_size, total))

    @app.get("/api/v1/traces/{trace_id}", tags=["traces"], dependencies=guard, summary="Reconstructed trace with span tree")
    def get_trace(trace_id: str = TraceIdPath):
        with db.session() as s:
            trace, recon = load_reconstruction(s, processor, trace_id.lower())
            if trace is None or recon is None:
                raise AppError(404, "trace_not_found", f"Trace {trace_id.lower()} was not found")
            detail = trace_detail(trace, recon)
        return ok(detail, warnings=detail["warnings"])

    @app.get("/api/v1/traces/{trace_id}/spans", tags=["traces"], dependencies=guard, summary="Spans of a trace (execution order)")
    def get_spans(trace_id: str = TraceIdPath, page: PageParams = Depends(pagination)):
        with db.session() as s:
            trace, recon = load_reconstruction(s, processor, trace_id.lower())
            if trace is None or recon is None:
                raise AppError(404, "trace_not_found", f"Trace {trace_id.lower()} was not found")
            spans = [span_dict(n) for n in recon.spans]
        total = len(spans)
        return ok(spans[page.offset : page.offset + page.page_size], meta=page_meta(page.page, page.page_size, total))

    @app.get("/api/v1/dependencies", tags=["dependencies"], dependencies=guard, summary="Service dependency graph derived from spans")
    def get_dependencies(f: WindowFilters = Depends()):
        start, end = f.resolve(utcnow())
        with db.session() as s:
            graph = dependency_graph(s, start, end)
        return ok(graph)

    @app.get("/api/v1/dependencies/{service_name}", tags=["dependencies"], dependencies=guard, summary="Incoming and outgoing dependencies of one service")
    def get_service_dependencies(service_name: str = Path(..., pattern=SERVICE_NAME_PATTERN), f: WindowOnly = Depends()):
        start, end = f.resolve(utcnow())
        name = service_name.lower()
        with db.session() as s:
            graph = dependency_graph(s, start, end)
        node = next((n for n in graph["nodes"] if n["service_name"] == name), None)
        if node is None:
            raise AppError(404, "service_not_found", f"Service {name!r} has not been observed in this time window")
        return ok(
            {
                "window": graph["window"],
                "service": node,
                "incoming": [e for e in graph["edges"] if e["target"] == name],
                "outgoing": [e for e in graph["edges"] if e["source"] == name],
            }
        )

    @app.post("/api/v1/traces/_finalize", tags=["admin"], dependencies=[Depends(require_internal), require_role("admin")], summary="Apply the completion policy now (admin)")
    def finalize_now():
        ids = processor.finalize_due(utcnow() + timedelta(seconds=settings.trace_completion_wait_seconds + 1))
        return ok({"finalized": ids})

    return app

# Run with: uvicorn trace_service.main:create_app --factory
