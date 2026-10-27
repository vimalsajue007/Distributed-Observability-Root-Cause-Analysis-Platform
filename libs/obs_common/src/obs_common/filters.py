"""Shared, validated query-parameter sets.

The same classes are used by the owning service *and* by the Observability Query Service /
gateway, so filter validation and sanitisation is identical everywhere (defence in depth).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Query

from .api import AppError
from .timeutil import to_iso, to_naive_utc
from .validation import (
    CORRELATION_ID_PATTERN,
    METRIC_NAME_PATTERN,
    SERVICE_NAME_PATTERN,
    TRACE_ID_PATTERN,
    strip_control_chars,
)

SEVERITIES = ("INFO", "WARNING", "ERROR", "CRITICAL")


def _range(start: datetime | None, end: datetime | None) -> tuple[datetime | None, datetime | None]:
    s = to_naive_utc(start) if start else None
    e = to_naive_utc(end) if end else None
    if s and e and e <= s:
        raise AppError(422, "invalid_time_range", "'end' must be later than 'start'", {"start": to_iso(s), "end": to_iso(e)})
    return s, e


def _compact(**kwargs: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        if isinstance(value, datetime):
            value = to_iso(value)
        out[key] = value
    return out


class TraceListFilters:
    def __init__(
        self,
        trace_id: str | None = Query(None, pattern=r"^[0-9a-fA-F]{8,32}$", description="Exact trace id or hex prefix (>= 8 chars)"),
        correlation_id: str | None = Query(None, pattern=CORRELATION_ID_PATTERN),
        service_name: str | None = Query(None, pattern=SERVICE_NAME_PATTERN, description="Trace contains a span from this service"),
        status: str | None = Query(None, pattern="^(OK|ERROR)$"),
        completion_state: str | None = Query(None, pattern="^(in_progress|complete|incomplete)$"),
        min_duration_ms: float | None = Query(None, ge=0),
        start: datetime | None = Query(None, description="Trace start >= (ISO-8601)"),
        end: datetime | None = Query(None, description="Trace start < (ISO-8601)"),
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(25, ge=1, le=200),
    ):
        self.trace_id = trace_id.lower() if trace_id else None
        self.correlation_id = correlation_id
        self.service_name = service_name.lower() if service_name else None
        self.status = status
        self.completion_state = completion_state
        self.min_duration_ms = min_duration_ms
        self.start, self.end = _range(start, end)
        self.page, self.page_size = page, page_size

    def params(self) -> dict[str, Any]:
        return _compact(
            trace_id=self.trace_id, correlation_id=self.correlation_id, service_name=self.service_name, status=self.status,
            completion_state=self.completion_state, min_duration_ms=self.min_duration_ms, start=self.start, end=self.end,
            page=self.page, page_size=self.page_size,
        )


class LogFilters:
    def __init__(
        self,
        severity: list[str] | None = Query(None, description="Repeatable: INFO, WARNING, ERROR, CRITICAL"),
        service_name: str | None = Query(None, pattern=SERVICE_NAME_PATTERN),
        trace_id: str | None = Query(None, pattern=TRACE_ID_PATTERN),
        span_id: str | None = Query(None, pattern=r"^[0-9a-fA-F]{16}$"),
        correlation_id: str | None = Query(None, pattern=CORRELATION_ID_PATTERN),
        q: str | None = Query(None, max_length=200, description="Case-insensitive substring search in the message"),
        start: datetime | None = Query(None),
        end: datetime | None = Query(None),
        order: str = Query("desc", pattern="^(asc|desc)$"),
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(25, ge=1, le=200),
    ):
        sev: list[str] | None = None
        if severity:
            flat = [part.strip().upper() for item in severity for part in item.split(",") if part.strip()]
            bad = [s for s in flat if s not in SEVERITIES]
            if bad:
                raise AppError(422, "validation_error", "Invalid severity filter", [{"field": "severity", "message": f"must be one of {', '.join(SEVERITIES)}"}])
            sev = flat
        self.severity = sev
        self.service_name = service_name.lower() if service_name else None
        self.trace_id = trace_id.lower() if trace_id else None
        self.span_id = span_id.lower() if span_id else None
        self.correlation_id = correlation_id
        self.q = strip_control_chars(q).strip() if q else None
        self.start, self.end = _range(start, end)
        self.order = order
        self.page, self.page_size = page, page_size

    def params(self) -> dict[str, Any]:
        out = _compact(
            service_name=self.service_name, trace_id=self.trace_id, span_id=self.span_id, correlation_id=self.correlation_id,
            q=self.q, start=self.start, end=self.end, order=self.order, page=self.page, page_size=self.page_size,
        )
        if self.severity:
            out["severity"] = self.severity
        return out


class AlertFilters:
    def __init__(
        self,
        status: str | None = Query(None, pattern="^(open|acknowledged|resolved|active)$", description="'active' = open + acknowledged"),
        service_name: str | None = Query(None, pattern=SERVICE_NAME_PATTERN),
        alert_type: str | None = Query(None, pattern="^(error_rate|avg_latency|p95_latency|request_volume)$"),
        severity: str | None = Query(None, pattern="^(warning|critical)$"),
        start: datetime | None = Query(None, description="last_seen_at >="),
        end: datetime | None = Query(None, description="last_seen_at <"),
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(25, ge=1, le=200),
    ):
        self.status, self.alert_type, self.severity = status, alert_type, severity
        self.service_name = service_name.lower() if service_name else None
        self.start, self.end = _range(start, end)
        self.page, self.page_size = page, page_size

    def params(self) -> dict[str, Any]:
        return _compact(
            status=self.status, service_name=self.service_name, alert_type=self.alert_type, severity=self.severity,
            start=self.start, end=self.end, page=self.page, page_size=self.page_size,
        )


class DiagnosticFilters:
    def __init__(
        self,
        trace_id: str | None = Query(None, pattern=TRACE_ID_PATTERN),
        affected_service: str | None = Query(None, pattern=SERVICE_NAME_PATTERN),
        severity: str | None = Query(None, pattern="^(info|low|medium|high|critical)$"),
        classification: str | None = Query(
            None, pattern="^(confirmed_failure|potential_cause|latency_bottleneck|insufficient_evidence|healthy)$"
        ),
        start: datetime | None = Query(None),
        end: datetime | None = Query(None),
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(25, ge=1, le=200),
    ):
        self.trace_id = trace_id.lower() if trace_id else None
        self.affected_service = affected_service.lower() if affected_service else None
        self.severity, self.classification = severity, classification
        self.start, self.end = _range(start, end)
        self.page, self.page_size = page, page_size

    def params(self) -> dict[str, Any]:
        return _compact(
            trace_id=self.trace_id, affected_service=self.affected_service, severity=self.severity,
            classification=self.classification, start=self.start, end=self.end, page=self.page, page_size=self.page_size,
        )


class WindowOnly:
    """Time window used by metrics / dependency endpoints: either start+end or window_minutes."""

    def __init__(
        self,
        window_minutes: int = Query(60, ge=1, le=10080, description="Used when start/end are not supplied"),
        start: datetime | None = Query(None),
        end: datetime | None = Query(None),
    ):
        self.service_name: str | None = None
        self.window_minutes = window_minutes
        self.start, self.end = _range(start, end)

    def params(self) -> dict[str, Any]:
        return _compact(service_name=self.service_name, window_minutes=self.window_minutes, start=self.start, end=self.end)

    def resolve(self, now: datetime) -> tuple[datetime, datetime]:
        from datetime import timedelta

        end = self.end or now
        start = self.start or (end - timedelta(minutes=self.window_minutes))
        return start, end


class WindowFilters(WindowOnly):
    def __init__(
        self,
        service_name: str | None = Query(None, pattern=SERVICE_NAME_PATTERN),
        window_minutes: int = Query(60, ge=1, le=10080, description="Used when start/end are not supplied"),
        start: datetime | None = Query(None),
        end: datetime | None = Query(None),
    ):
        super().__init__(window_minutes, start, end)
        self.service_name = service_name.lower() if service_name else None


class TimeseriesFilters(WindowFilters):
    def __init__(
        self,
        service_name: str | None = Query(None, pattern=SERVICE_NAME_PATTERN),
        metric_name: str = Query("request.duration_ms", pattern=METRIC_NAME_PATTERN),
        window_minutes: int = Query(60, ge=1, le=10080),
        start: datetime | None = Query(None),
        end: datetime | None = Query(None),
        bucket_seconds: int | None = Query(None, ge=10, le=86400),
    ):
        super().__init__(service_name, window_minutes, start, end)
        self.metric_name = metric_name
        self.bucket_seconds = bucket_seconds

    def params(self) -> dict[str, Any]:
        out = super().params()
        out["metric_name"] = self.metric_name
        if self.bucket_seconds:
            out["bucket_seconds"] = self.bucket_seconds
        return out
