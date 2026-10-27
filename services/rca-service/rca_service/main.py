"""Root-Cause Analysis Service: deterministic, evidence-backed diagnostics."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Path
from sqlalchemy import select

from obs_common.api import AppError, install_common, ok, page_meta, require_internal, require_role
from obs_common.broker import ALL_SPECS, TRACE_COMPLETED_SPEC, Broker, NonRetryableError, RabbitBroker
from obs_common.db import Database
from obs_common.filters import DiagnosticFilters
from obs_common.http import DownstreamError, SyncDownstreamClient, downstream_to_app_error
from obs_common.logging_setup import configure_logging
from obs_common.validation import TRACE_ID_PATTERN

from .config import Settings
from .models import DiagnosticReport
from .service import RcaService, report_dict

log = logging.getLogger("root-cause-analysis")


def create_app(
    settings: Settings | None = None,
    *,
    broker: Broker | None = None,
    db: Database | None = None,
    transports: dict | None = None,
) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(settings.service_name, settings.log_level)
    db = db or Database(settings.database_url)
    broker = broker or RabbitBroker(settings.broker_url)
    transports = transports or {}

    def client(name: str, url: str) -> SyncDownstreamClient:
        return SyncDownstreamClient(name, url, timeout=settings.downstream_timeout_seconds, retries=1,
                                    internal_token=settings.internal_api_token, transport=transports.get(name))

    service = RcaService(
        db, settings,
        traces=client("trace-service", settings.trace_service_url),
        logs=client("log-service", settings.log_service_url),
        metrics=client("metrics-service", settings.metrics_service_url),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            broker.declare_topology(ALL_SPECS)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not declare topology yet", extra={"error": repr(exc)})
        broker.start_consumer(TRACE_COMPLETED_SPEC, service.handle_event)
        yield
        broker.stop()
        for c in (service.traces, service.logs, service.metrics):
            c.close()

    app = FastAPI(
        title="Root-Cause Analysis Service", version="1.0.0",
        description="Deterministic rules engine: correlates traces, logs and metrics into evidence-backed diagnostics.",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None, redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    install_common(app, settings=settings, readiness_checks={"database": db.ping, "broker_consumer": broker.consumers_running})
    app.state.db, app.state.service = db, service
    guard = [Depends(require_internal)]

    @app.get("/api/v1/diagnostics", tags=["diagnostics"], dependencies=guard, summary="List diagnostic reports")
    def list_diagnostics(f: DiagnosticFilters = Depends()):
        with db.session() as s:
            items, total = service.list_reports(s, f)
        return ok(items, meta=page_meta(f.page, f.page_size, total))

    @app.get("/api/v1/diagnostics/{report_id}", tags=["diagnostics"], dependencies=guard, summary="Report with evidence")
    def get_diagnostic(report_id: str = Path(..., pattern=r"^(\d{1,18}|[0-9a-fA-F]{32})$", description="Report id or trace id")):
        with db.session() as s:
            if len(report_id) == 32:
                row = s.scalar(select(DiagnosticReport).where(DiagnosticReport.trace_id == report_id.lower()))
            else:
                row = s.get(DiagnosticReport, int(report_id))
            if row is None:
                raise AppError(404, "diagnostic_not_found", f"Diagnostic {report_id} was not found")
            data = report_dict(row, include_evidence=True)
        return ok(data, warnings=data.get("warnings") or None)

    @app.post("/api/v1/diagnostics/analyze/{trace_id}", tags=["diagnostics"], dependencies=[*guard, require_role("operator")],
              summary="Run (or re-run) the analysis for a trace")
    def analyze_trace(trace_id: str = Path(..., pattern=TRACE_ID_PATTERN)):
        try:
            data = service.analyze_trace(trace_id.lower(), trigger="manual")
        except DownstreamError as exc:
            raise downstream_to_app_error(exc) from exc
        except NonRetryableError as exc:
            raise AppError(404, "trace_not_found", f"Trace {trace_id.lower()} was not found") from exc
        return ok(data, status_code=201 if data["analysis_count"] == 1 else 200, warnings=data.get("warnings") or None)

    return app

# Run with: uvicorn rca_service.main:create_app --factory
