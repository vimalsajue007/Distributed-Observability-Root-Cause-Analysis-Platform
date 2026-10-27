"""Structured event contracts shared by ingestion (producer) and consumers.

Envelope (schema_version 1) published to the broker:

    {
      "schema_version": 1,
      "event_id": "...",
      "event_type": "trace" | "log" | "metric",
      "service_name": "order-service",
      "trace_id": "<32 hex>" | null,
      "span_id": "<16 hex>" | null,
      "correlation_id": "..." | null,
      "received_at": "2026-10-16T12:00:00.000000Z",
      "payload": { ...type specific, see SpanPayload / LogPayload / MetricPayload... }
    }
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .timeutil import duration_ms, parse_timestamp, to_iso
from .validation import (
    METRIC_NAME_PATTERN,
    clean_text,
    normalize_correlation_id,
    normalize_service_name,
    normalize_span_id,
    normalize_trace_id,
)

SCHEMA_VERSION = 1
EventType = Literal["trace", "log", "metric"]
MAX_ATTRIBUTES = 64
MAX_ATTR_VALUE_LENGTH = 2000

ROUTING_KEYS = {
    "trace": "telemetry.traces",
    "log": "telemetry.logs",
    "metric": "telemetry.metrics",
}
TRACE_COMPLETED_ROUTING_KEY = "events.trace.completed"


def _timestamp(value: Any) -> datetime:
    return parse_timestamp(value)


def _clean_attributes(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("attributes must be an object")
    if len(value) > MAX_ATTRIBUTES:
        raise ValueError(f"attributes may contain at most {MAX_ATTRIBUTES} keys")
    cleaned: dict[str, Any] = {}
    for key, val in value.items():
        key = clean_text(str(key), 120)
        if isinstance(val, (bool, int, float)) or val is None:
            if isinstance(val, float) and not math.isfinite(val):
                val = None
            cleaned[key] = val
        elif isinstance(val, str):
            cleaned[key] = clean_text(val, MAX_ATTR_VALUE_LENGTH)
        else:  # nested structures are stored as JSON text, never interpreted
            cleaned[key] = clean_text(json.dumps(val, default=str), MAX_ATTR_VALUE_LENGTH)
    return cleaned


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    event_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$")
    service_name: str
    correlation_id: str | None = None

    @field_validator("service_name")
    @classmethod
    def _service(cls, v: str) -> str:
        return normalize_service_name(v)

    @field_validator("correlation_id")
    @classmethod
    def _corr(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        return normalize_correlation_id(v)


class SpanPayload(_EventBase):
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    operation_name: str = Field(min_length=1, max_length=255)
    kind: Literal["INTERNAL", "SERVER", "CLIENT", "PRODUCER", "CONSUMER"] = "INTERNAL"
    start_time: datetime
    end_time: datetime
    status: Literal["OK", "ERROR", "UNSET"] = "OK"
    error_message: str | None = Field(default=None, max_length=4000)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("trace_id")
    @classmethod
    def _t(cls, v: str) -> str:
        return normalize_trace_id(v)

    @field_validator("span_id")
    @classmethod
    def _s(cls, v: str) -> str:
        return normalize_span_id(v)

    @field_validator("parent_span_id", mode="before")
    @classmethod
    def _p(cls, v: Any) -> str | None:
        if v in (None, "", "0000000000000000"):
            return None
        return normalize_span_id(str(v))

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _ts(cls, v: Any) -> datetime:
        return _timestamp(v)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, v: Any) -> Any:
        return v.upper() if isinstance(v, str) else v

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v: Any) -> Any:
        return v.upper() if isinstance(v, str) else v

    @field_validator("error_message")
    @classmethod
    def _err(cls, v: str | None) -> str | None:
        return clean_text(v, 4000) if v else None

    @field_validator("attributes", mode="before")
    @classmethod
    def _attrs(cls, v: Any) -> dict[str, Any]:
        return _clean_attributes(v)

    @model_validator(mode="after")
    def _order(self) -> "SpanPayload":
        if self.end_time < self.start_time:
            raise ValueError("end_time must not be earlier than start_time")
        if self.parent_span_id == self.span_id:
            raise ValueError("parent_span_id must differ from span_id")
        return self

    @property
    def duration_ms(self) -> float:
        return duration_ms(self.start_time, self.end_time)

    def default_event_id(self) -> str:
        # deterministic => retries of the same span are naturally idempotent
        return f"span:{self.trace_id}:{self.span_id}"


class LogPayload(_EventBase):
    timestamp: datetime | None = None
    severity: str | None = Field(default="INFO", max_length=32)
    message: Any
    logger: str | None = Field(default=None, max_length=200)
    trace_id: str | None = None
    span_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: Any) -> datetime | None:
        if v in (None, ""):
            return None
        return _timestamp(v)

    @field_validator("severity", mode="before")
    @classmethod
    def _sev(cls, v: Any) -> Any:
        if v is None:
            return "INFO"
        return str(v)

    @field_validator("trace_id", mode="before")
    @classmethod
    def _t(cls, v: Any) -> str | None:
        if v in (None, ""):
            return None
        return normalize_trace_id(str(v))

    @field_validator("span_id", mode="before")
    @classmethod
    def _s(cls, v: Any) -> str | None:
        if v in (None, ""):
            return None
        return normalize_span_id(str(v))

    @field_validator("message")
    @classmethod
    def _message(cls, v: Any) -> Any:
        if v is None:
            raise ValueError("message is required")
        return v

    @field_validator("attributes", mode="before")
    @classmethod
    def _attrs(cls, v: Any) -> dict[str, Any]:
        return _clean_attributes(v)


class MetricPayload(_EventBase):
    metric_name: str = Field(pattern=METRIC_NAME_PATTERN)
    value: float
    timestamp: datetime | None = None
    unit: str | None = Field(default=None, max_length=32)
    labels: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("value must be a finite number")
        return v

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: Any) -> datetime | None:
        if v in (None, ""):
            return None
        return _timestamp(v)

    @field_validator("trace_id", mode="before")
    @classmethod
    def _t(cls, v: Any) -> str | None:
        if v in (None, ""):
            return None
        return normalize_trace_id(str(v))

    @field_validator("labels", mode="before")
    @classmethod
    def _labels(cls, v: Any) -> dict[str, Any]:
        cleaned = _clean_attributes(v)
        if len(cleaned) > 20:
            raise ValueError("labels may contain at most 20 keys")
        return cleaned


PAYLOAD_MODELS: dict[str, type[_EventBase]] = {
    "trace": SpanPayload,
    "log": LogPayload,
    "metric": MetricPayload,
}


class Envelope(BaseModel):
    """Message as it travels through the broker (re-validated by every consumer)."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int
    event_id: str = Field(min_length=1, max_length=128)
    event_type: EventType
    service_name: str
    trace_id: str | None = None
    span_id: str | None = None
    correlation_id: str | None = None
    received_at: str
    payload: dict[str, Any]

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {v}")
        return v

    def parse_payload(self) -> _EventBase:
        return PAYLOAD_MODELS[self.event_type].model_validate(self.payload)


def build_envelope(event_type: EventType, model: _EventBase, event_id: str, received_at: datetime) -> dict[str, Any]:
    """Create the broker message from a validated payload model."""
    payload = model.model_dump(mode="json", exclude_none=False)
    for key, value in list(payload.items()):
        # model_dump(mode='json') renders naive datetimes without 'Z'; normalise to explicit UTC.
        original = getattr(model, key, None)
        if isinstance(original, datetime):
            payload[key] = to_iso(original)
    payload["event_id"] = event_id
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "event_type": event_type,
        "service_name": model.service_name,
        "trace_id": getattr(model, "trace_id", None),
        "span_id": getattr(model, "span_id", None),
        "correlation_id": model.correlation_id,
        "received_at": to_iso(received_at),
        "payload": payload,
    }


def summarize_validation_error(exc: Exception) -> str:
    """Compact, input-free description of a pydantic ValidationError (safe for logs / DLQ headers)."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            return "; ".join(
                f"{'.'.join(str(p) for p in e.get('loc', ())) or '(event)'}: {e.get('msg', 'invalid')}" for e in errors()
            )[:500]
        except Exception:  # noqa: BLE001
            pass
    return str(exc)[:500]
