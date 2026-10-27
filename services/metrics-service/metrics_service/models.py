from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from obs_common.db import BigPK, UTCDateTime, make_base
from obs_common.timeutil import utcnow

Base = make_base()

REQUEST_METRIC = "request.duration_ms"


class MetricSample(Base):
    """Raw measurements (one row per event; idempotent on event_id)."""

    __tablename__ = "metric_samples"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_metric_samples_event_id"),
        Index("ix_metric_samples_lookup", "service_name", "metric_name", "recorded_at"),
        Index("ix_metric_samples_recorded", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # additional columns
    is_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    labels: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class MetricAggregate(Base):
    """Time-bucketed summaries derived from the raw samples (re-computed => never double counted)."""

    __tablename__ = "metric_aggregates"
    __table_args__ = (
        UniqueConstraint("service_name", "metric_name", "interval_start", "interval_seconds", name="uq_metric_aggregate_bucket"),
        Index("ix_metric_aggregates_start", "interval_start"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    interval_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    interval_end: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    average_value: Mapped[float | None] = mapped_column(Float)
    minimum_value: Mapped[float | None] = mapped_column(Float)
    maximum_value: Mapped[float | None] = mapped_column(Float)
    percentile_data: Mapped[dict | None] = mapped_column(JSON)
    # additional columns
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sum_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (UniqueConstraint("service_name", "alert_type", name="uq_alert_rule_service_type"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)  # '*' = every service
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)  # error_rate|avg_latency|p95_latency|request_volume
    operator: Mapped[str] = mapped_column(String(4), nullable=False, default="gt")
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    min_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        # While an alert is active, active_key = "<service>|<type>" (unique => at most one active alert per
        # service+type). It is NULL once resolved, so history rows do not collide.
        UniqueConstraint("active_key", name="uq_alerts_active_key"),
        Index("ix_alerts_status", "status"),
        Index("ix_alerts_service", "service_name"),
        Index("ix_alerts_last_seen", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    measured_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")  # open|acknowledged|resolved
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # additional columns
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    operator: Mapped[str] = mapped_column(String(4), nullable=False, default="gt")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[dict | None] = mapped_column(JSON)
    active_key: Mapped[str | None] = mapped_column(String(160))
    acknowledged_by: Mapped[str | None] = mapped_column(String(100))
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolution: Mapped[str | None] = mapped_column(String(16))  # auto|manual
