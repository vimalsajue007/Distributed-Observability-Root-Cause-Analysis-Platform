"""Metrics Aggregation Service - raw samples, time-bucketed aggregates, percentiles and alerting."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal

from fastapi import Body, Depends, FastAPI, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from obs_common.api import AppError, current_user, install_common, ok, page_meta, require_internal, require_role
from obs_common.broker import ALL_SPECS, METRIC_SPEC, Broker, RabbitBroker
from obs_common.db import Database
from obs_common.filters import AlertFilters, TimeseriesFilters, WindowFilters
from obs_common.logging_setup import configure_logging
from obs_common.timeutil import utcnow
from obs_common.validation import SERVICE_NAME_PATTERN
from fastapi import Request

from .alerting import ALERT_TYPES, TRANSITIONS
from .config import Settings
from .models import Alert, AlertRule
from .processor import MetricsProcessor

log = logging.getLogger("metrics-aggregation")


def alert_dict(a: Alert) -> dict[str, Any]:
    return {
        "id": a.id, "service_name": a.service_name, "alert_type": a.alert_type, "severity": a.severity, "status": a.status,
        "measured_value": a.measured_value, "threshold_value": a.threshold_value, "operator": a.operator, "message": a.message,
        "first_seen_at": a.first_seen_at, "last_seen_at": a.last_seen_at, "occurrence_count": a.occurrence_count,
        "evidence": a.evidence or {}, "acknowledged_by": a.acknowledged_by, "acknowledged_at": a.acknowledged_at,
        "resolved_at": a.resolved_at, "resolution": a.resolution,
    }


def rule_dict(r: AlertRule) -> dict[str, Any]:
    return {"id": r.id, "service_name": r.service_name, "alert_type": r.alert_type, "operator": r.operator, "threshold": r.threshold,
            "window_seconds": r.window_seconds, "min_samples": r.min_samples, "severity": r.severity, "enabled": r.enabled, "updated_at": r.updated_at}


class AlertPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["open", "acknowledged", "resolved"]


class RuleUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_name: str = Field("*", pattern=r"^(\*|[A-Za-z0-9][A-Za-z0-9._\-]{0,99})$")
    alert_type: Literal["error_rate", "avg_latency", "p95_latency", "request_volume"]
    operator: Literal["gt", "lt"] = "gt"
    threshold: float = Field(ge=0)
    window_seconds: int = Field(300, ge=30, le=86400)
    min_samples: int = Field(5, ge=1, le=100000)
    severity: Literal["warning", "critical"] = "warning"
    enabled: bool = True


def create_app(settings: Settings | None = None, *, broker: Broker | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(settings.service_name, settings.log_level)
    db = db or Database(settings.database_url)
    broker = broker or RabbitBroker(settings.broker_url)
    processor = MetricsProcessor(db, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            broker.declare_topology(ALL_SPECS)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not declare topology yet", extra={"error": repr(exc)})
        if settings.run_background_workers:
            processor.start_background()
        else:
            processor.seed_rules()
        broker.start_consumer(METRIC_SPEC, processor.handle_event)
        yield
        processor.stop()
        broker.stop()

    app = FastAPI(
        title="Metrics Aggregation Service",
        version="1.0.0",
        description="Aggregates request/error/latency measurements into time buckets, computes percentiles and raises de-duplicated alerts.",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    install_common(app, settings=settings, readiness_checks={"database": db.ping, "broker_consumer": broker.consumers_running})
    app.state.db, app.state.processor = db, processor
    guard = [Depends(require_internal)]

    @app.get("/api/v1/metrics/services", tags=["metrics"], dependencies=guard, summary="Per-service request/error/latency statistics")
    def metrics_services(f: WindowFilters = Depends()):
        start, end = f.resolve(utcnow())
        with db.session() as s:
            rows = processor.services(s, start, end)
        if f.service_name:
            rows = [r for r in rows if r["service_name"] == f.service_name]
        return ok(rows, meta={"window": {"start": start, "end": end}, "percentile_method": "nearest-rank"})

    @app.get("/api/v1/metrics/summary", tags=["metrics"], dependencies=guard, summary="Overall summary (requests, error rate, latency, alerts)")
    def metrics_summary(f: WindowFilters = Depends()):
        start, end = f.resolve(utcnow())
        with db.session() as s:
            return ok(processor.summary(s, start, end, f.service_name))

    @app.get("/api/v1/metrics/timeseries", tags=["metrics"], dependencies=guard, summary="Time-bucketed series (gaps are explicit)")
    def metrics_timeseries(f: TimeseriesFilters = Depends()):
        start, end = f.resolve(utcnow())
        with db.session() as s:
            return ok(processor.timeseries(s, f.service_name, f.metric_name, start, end, f.bucket_seconds))

    @app.get("/api/v1/metrics/latency", tags=["metrics"], dependencies=guard, summary="Latency percentiles and histogram")
    def metrics_latency(f: WindowFilters = Depends()):
        start, end = f.resolve(utcnow())
        with db.session() as s:
            return ok(processor.latency(s, start, end, f.service_name))

    @app.post("/api/v1/metrics/_flush", tags=["admin"], dependencies=[*guard, require_role("admin")], summary="Flush aggregation + evaluate alerts now (admin)")
    def flush():
        buckets = processor.flush_dirty()
        return ok({"buckets_recomputed": buckets, "alerts": processor.evaluate_alerts()})

    # ---------------------------------------------------------------- alerts
    @app.get("/api/v1/alerts", tags=["alerts"], dependencies=guard, summary="List alerts")
    def list_alerts(f: AlertFilters = Depends()):
        cond = []
        if f.status == "active":
            cond.append(Alert.status.in_(("open", "acknowledged")))
        elif f.status:
            cond.append(Alert.status == f.status)
        if f.service_name:
            cond.append(Alert.service_name == f.service_name)
        if f.alert_type:
            cond.append(Alert.alert_type == f.alert_type)
        if f.severity:
            cond.append(Alert.severity == f.severity)
        if f.start:
            cond.append(Alert.last_seen_at >= f.start)
        if f.end:
            cond.append(Alert.last_seen_at < f.end)
        with db.session() as s:
            total = s.scalar(select(func.count()).select_from(Alert).where(*cond)) or 0
            rows = s.scalars(select(Alert).where(*cond).order_by(Alert.last_seen_at.desc(), Alert.id.desc()).offset((f.page - 1) * f.page_size).limit(f.page_size)).all()
            data = [alert_dict(a) for a in rows]
        return ok(data, meta=page_meta(f.page, f.page_size, total))

    # defined before /alerts/{alert_id}
    @app.get("/api/v1/alerts/rules", tags=["alerts"], dependencies=guard, summary="Configured alert thresholds")
    def list_rules():
        with db.session() as s:
            return ok([rule_dict(r) for r in s.scalars(select(AlertRule).order_by(AlertRule.service_name, AlertRule.alert_type))])

    @app.put("/api/v1/alerts/rules", tags=["alerts"], dependencies=[*guard, require_role("admin")], summary="Create/update an alert threshold (admin)")
    def upsert_rule(body: RuleUpsert):
        name = body.service_name.lower() if body.service_name != "*" else "*"
        with db.session() as s:
            rule = s.scalar(select(AlertRule).where(AlertRule.service_name == name, AlertRule.alert_type == body.alert_type))
            if rule is None:
                rule = AlertRule(service_name=name, alert_type=body.alert_type, threshold=body.threshold)
                s.add(rule)
            rule.operator, rule.threshold, rule.window_seconds = body.operator, body.threshold, body.window_seconds
            rule.min_samples, rule.severity, rule.enabled = body.min_samples, body.severity, body.enabled
            s.flush()
            return ok(rule_dict(rule))

    @app.get("/api/v1/alerts/{alert_id}", tags=["alerts"], dependencies=guard, summary="Get one alert with evidence")
    def get_alert(alert_id: int = Path(..., ge=1)):
        with db.session() as s:
            alert = s.get(Alert, alert_id)
            if alert is None:
                raise AppError(404, "alert_not_found", f"Alert {alert_id} was not found")
            return ok(alert_dict(alert))

    @app.patch("/api/v1/alerts/{alert_id}", tags=["alerts"], dependencies=[*guard, require_role("operator")], summary="Acknowledge / resolve / re-open an alert")
    def patch_alert(request: Request, body: AlertPatch, alert_id: int = Path(..., ge=1)):
        now = utcnow()
        with db.session() as s:
            alert = s.get(Alert, alert_id)
            if alert is None:
                raise AppError(404, "alert_not_found", f"Alert {alert_id} was not found")
            if body.status == alert.status:
                return ok(alert_dict(alert))
            if body.status not in TRANSITIONS[alert.status]:
                raise AppError(409, "invalid_transition", f"Cannot change an alert from '{alert.status}' to '{body.status}'")
            alert.status = body.status
            if body.status == "acknowledged":
                alert.acknowledged_by, alert.acknowledged_at = current_user(request), now
            elif body.status == "open":
                alert.acknowledged_by = alert.acknowledged_at = None
            elif body.status == "resolved":
                alert.resolved_at, alert.resolution, alert.active_key = now, "manual", None
            s.flush()
            return ok(alert_dict(alert))

    return app

# Run with: uvicorn metrics_service.main:create_app --factory
