"""Observability Query Service - stateless read API used by the console (through the gateway)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from fastapi import Depends, FastAPI, Path, Query, Request

from obs_common.api import AppError, current_role, current_user, install_common, ok, require_internal
from obs_common.filters import AlertFilters, DiagnosticFilters, LogFilters, TimeseriesFilters, TraceListFilters, WindowFilters, WindowOnly
from obs_common.http import DownstreamError, SyncDownstreamClient, downstream_to_app_error
from obs_common.logging_setup import configure_logging
from obs_common.timeutil import to_iso, utcnow
from obs_common.validation import SERVICE_NAME_PATTERN, TRACE_ID_PATTERN

from .config import Settings
from .service import Sources, can_read_logs

log = logging.getLogger("observability-query")


def create_app(settings: Settings | None = None, *, transports: dict | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(settings.service_name, settings.log_level)
    transports = transports or {}

    def client(name: str, url: str) -> SyncDownstreamClient:
        return SyncDownstreamClient(name, url, timeout=settings.downstream_timeout_seconds, retries=settings.downstream_retries,
                                    backoff=settings.downstream_backoff_seconds, internal_token=settings.internal_api_token, transport=transports.get(name))

    src = Sources(
        client("trace-service", settings.trace_service_url), client("log-service", settings.log_service_url),
        client("metrics-service", settings.metrics_service_url), client("rca-service", settings.rca_service_url),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        src.close()

    app = FastAPI(
        title="Observability Query Service", version="1.0.0",
        description="Stateless aggregation of traces, logs, metrics and diagnostics. Returns partial results with warnings when a source is degraded.",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None, redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )

    # The query service is stateless; it is ready when it can start serving (downstream health is reported via /system/health at the gateway).
    install_common(app, settings=settings, readiness_checks={})
    app.state.sources = src
    guard = [Depends(require_internal)]

    def ctx(request: Request) -> tuple[str, str]:
        return current_role(request), current_user(request)

    def primary(client_: SyncDownstreamClient, path: str, params: dict[str, Any] | None, request: Request) -> dict[str, Any]:
        role, user = ctx(request)
        try:
            return src.primary(client_, path, params, role, user)
        except DownstreamError as exc:
            raise downstream_to_app_error(exc) from exc

    def passthrough(body: dict[str, Any]):
        return ok(body.get("data"), meta=body.get("meta"), warnings=body.get("warnings") or None)

    # ------------------------------------------------------------------ traces

    @app.get("/api/v1/query/traces", tags=["traces"], dependencies=guard, summary="Search traces")
    def traces(request: Request, f: TraceListFilters = Depends()):
        return passthrough(primary(src.traces, "/api/v1/traces", f.params(), request))

    @app.get("/api/v1/query/traces/{trace_id}", tags=["traces"], dependencies=guard,
             summary="Trace detail enriched with correlated logs and its diagnostic report")
    def trace_detail(request: Request, trace_id: str = Path(..., pattern=TRACE_ID_PATTERN)):
        tid = trace_id.lower()
        role, user = ctx(request)
        body = primary(src.traces, f"/api/v1/traces/{tid}", None, request)
        data = body["data"]
        warnings: list[dict[str, str]] = list(body.get("warnings") or [])
        jobs: dict[str, tuple] = {"diagnostics": (src.rca, f"/api/v1/diagnostics/{tid}", None, role, user)}
        if can_read_logs(role):
            jobs["logs"] = (src.logs, "/api/v1/logs", {"trace_id": tid, "page_size": 200, "order": "asc"}, role, user)
        results = src.parallel(jobs)
        diag, w = results["diagnostics"]
        if w:
            warnings.append(w)
        if "logs" in results:
            logs, w = results["logs"]
            if w:
                warnings.append(w)
            data["logs"] = logs if logs is not None else []
            data["logs_available"] = logs is not None
        else:
            data["logs"], data["logs_available"] = [], False
            warnings.append({"code": "logs_restricted", "message": "Logs are hidden for the viewer role."})
        data["diagnostic"] = diag
        return ok(data, warnings=warnings or None)

    # ------------------------------------------------------------------ logs

    @app.get("/api/v1/query/logs", tags=["logs"], dependencies=guard, summary="Search logs (operator+)")
    def logs(request: Request, f: LogFilters = Depends()):
        if not can_read_logs(current_role(request)):
            raise AppError(403, "forbidden", "Requires role 'operator' or higher")
        return passthrough(primary(src.logs, "/api/v1/logs", f.params(), request))

    @app.get("/api/v1/query/logs/patterns", tags=["logs"], dependencies=guard, summary="Repeated error patterns (operator+)")
    def log_patterns(
        request: Request,
        service_name: str | None = Query(None, pattern=SERVICE_NAME_PATTERN),
        min_occurrences: int = Query(1, ge=1, le=1_000_000),
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(25, ge=1, le=200),
    ):
        if not can_read_logs(current_role(request)):
            raise AppError(403, "forbidden", "Requires role 'operator' or higher")
        params: dict[str, Any] = {"min_occurrences": min_occurrences, "page": page, "page_size": page_size}
        if service_name:
            params["service_name"] = service_name.lower()
        return passthrough(primary(src.logs, "/api/v1/logs/patterns", params, request))

    # ------------------------------------------------------------------ metrics

    @app.get("/api/v1/query/metrics/summary", tags=["metrics"], dependencies=guard, summary="Overall metrics summary")
    def m_summary(request: Request, f: WindowFilters = Depends()):
        return passthrough(primary(src.metrics, "/api/v1/metrics/summary", f.params(), request))

    @app.get("/api/v1/query/metrics/services", tags=["metrics"], dependencies=guard, summary="Per-service statistics")
    def m_services(request: Request, f: WindowFilters = Depends()):
        return passthrough(primary(src.metrics, "/api/v1/metrics/services", f.params(), request))

    @app.get("/api/v1/query/metrics/timeseries", tags=["metrics"], dependencies=guard, summary="Metric time series")
    def m_timeseries(request: Request, f: TimeseriesFilters = Depends()):
        return passthrough(primary(src.metrics, "/api/v1/metrics/timeseries", f.params(), request))

    @app.get("/api/v1/query/metrics/latency", tags=["metrics"], dependencies=guard, summary="Latency percentiles and histogram")
    def m_latency(request: Request, f: WindowFilters = Depends()):
        return passthrough(primary(src.metrics, "/api/v1/metrics/latency", f.params(), request))

    # ------------------------------------------------------------------ alerts & diagnostics

    @app.get("/api/v1/query/alerts", tags=["alerts"], dependencies=guard, summary="Alerts")
    def alerts(request: Request, f: AlertFilters = Depends()):
        return passthrough(primary(src.metrics, "/api/v1/alerts", f.params(), request))

    @app.get("/api/v1/query/alerts/rules", tags=["alerts"], dependencies=guard, summary="Alert rules")
    def alert_rules(request: Request):
        return passthrough(primary(src.metrics, "/api/v1/alerts/rules", None, request))

    @app.get("/api/v1/query/alerts/{alert_id}", tags=["alerts"], dependencies=guard, summary="One alert")
    def alert(request: Request, alert_id: int = Path(..., ge=1)):
        return passthrough(primary(src.metrics, f"/api/v1/alerts/{alert_id}", None, request))

    @app.get("/api/v1/query/diagnostics", tags=["diagnostics"], dependencies=guard, summary="Diagnostic reports")
    def diagnostics(request: Request, f: DiagnosticFilters = Depends()):
        return passthrough(primary(src.rca, "/api/v1/diagnostics", f.params(), request))

    @app.get("/api/v1/query/diagnostics/{report_id}", tags=["diagnostics"], dependencies=guard, summary="Diagnostic report with evidence")
    def diagnostic(request: Request, report_id: str = Path(..., pattern=r"^(\d{1,18}|[0-9a-fA-F]{32})$")):
        return passthrough(primary(src.rca, f"/api/v1/diagnostics/{report_id}", None, request))

    # ------------------------------------------------------------------ dependencies

    @app.get("/api/v1/query/dependencies", tags=["dependencies"], dependencies=guard,
             summary="Dependency graph enriched with per-service health and latency")
    def dependencies(request: Request, f: WindowFilters = Depends()):
        role, user = ctx(request)
        start, end = f.resolve(utcnow())
        window = {"start": to_iso(start), "end": to_iso(end)}
        body = primary(src.traces, "/api/v1/dependencies", {"start": window["start"], "end": window["end"]}, request)
        graph = body["data"]
        warnings: list[dict[str, str]] = []
        metrics, w = src.optional(src.metrics, "/api/v1/metrics/services", window, role, user, "metrics")
        by_service = {m["service_name"]: m for m in metrics} if metrics is not None else {}
        if w:
            warnings.append(w)
        alerts_, w = src.optional(src.metrics, "/api/v1/alerts", {"status": "active", "page_size": 200}, role, user, "alerts")
        if w:
            warnings.append(w)
        active: dict[str, int] = {}
        for a in alerts_ or []:
            active[a["service_name"]] = active.get(a["service_name"], 0) + 1
        for node in graph["nodes"]:
            m = by_service.get(node["service_name"])
            rate = node.get("error_rate") or 0
            node["metrics"] = m
            node["active_alerts"] = active.get(node["service_name"], 0)
            node["health"] = "unknown" if (m is None and not node.get("request_count")) else (
                "critical" if rate >= 0.25 or node["active_alerts"] and any(a["severity"] == "critical" and a["service_name"] == node["service_name"] for a in alerts_ or [])
                else "degraded" if rate > 0 or node["active_alerts"] else "healthy")
        return ok(graph, warnings=warnings or None)

    @app.get("/api/v1/query/dependencies/{service_name}", tags=["dependencies"], dependencies=guard, summary="Dependencies of one service")
    def service_dependencies(request: Request, service_name: str = Path(..., pattern=SERVICE_NAME_PATTERN), f: WindowOnly = Depends()):
        start, end = f.resolve(utcnow())
        params = {"start": to_iso(start), "end": to_iso(end)}
        return passthrough(primary(src.traces, f"/api/v1/dependencies/{service_name.lower()}", params, request))

    # ------------------------------------------------------------------ overview

    @app.get("/api/v1/query/overview", tags=["overview"], dependencies=guard, summary="Dashboard overview (partial results allowed)")
    def overview(request: Request, window_minutes: int = 60):
        window_minutes = max(1, min(window_minutes, 10080))
        role, user = ctx(request)
        end = utcnow()
        w = {"start": to_iso(end - timedelta(minutes=window_minutes)), "end": to_iso(end)}
        jobs = {
            "metrics": (src.metrics, "/api/v1/metrics/summary", w, role, user),
            "services": (src.metrics, "/api/v1/metrics/services", w, role, user),
            "alerts": (src.metrics, "/api/v1/alerts", {"status": "active", "page_size": 5}, role, user),
            "failed_traces": (src.traces, "/api/v1/traces", {"status": "ERROR", "page_size": 5, **w}, role, user),
            "slow_traces": (src.traces, "/api/v1/traces", {"min_duration_ms": 1000, "page_size": 5, **w}, role, user),
            "diagnostics": (src.rca, "/api/v1/diagnostics", {"page_size": 5}, role, user),
        }
        results = src.parallel(jobs)
        warnings = [w_ for _, w_ in results.values() if w_]
        data = {"window": w, **{k: v for k, (v, _) in results.items()}}
        data["degraded_sources"] = sorted({x["code"].removesuffix("_unavailable") for x in warnings})
        return ok(data, warnings=warnings or None)

    return app

# Run with: uvicorn query_service.main:create_app --factory
