"""Validation helpers & shared regexes used for every user-supplied identifier/filter."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator

SERVICE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,99}$"
TRACE_ID_PATTERN = r"^[0-9a-fA-F]{32}$"
SPAN_ID_PATTERN = r"^[0-9a-fA-F]{16}$"
CORRELATION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$"
METRIC_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.\-/]{0,99}$"

SERVICE_RE = re.compile(SERVICE_NAME_PATTERN)
TRACE_ID_RE = re.compile(TRACE_ID_PATTERN)
SPAN_ID_RE = re.compile(SPAN_ID_PATTERN)
CORRELATION_RE = re.compile(CORRELATION_ID_PATTERN)
METRIC_NAME_RE = re.compile(METRIC_NAME_PATTERN)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_control_chars(value: str) -> str:
    """Remove NULs and other control characters (keeps \\n and \\t)."""
    return _CONTROL_CHARS.sub("", value)


def clean_text(value: str, max_length: int) -> str:
    value = strip_control_chars(value)
    if len(value) > max_length:
        value = value[: max_length - 1] + "…"
    return value


def normalize_service_name(value: str) -> str:
    value = value.strip().lower()
    if not SERVICE_RE.match(value):
        raise ValueError("service_name must match ^[a-z0-9][a-z0-9._-]{0,99}$ (letters, digits, '.', '_' or '-')")
    return value


def normalize_trace_id(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("0x"):
        value = value[2:]
    if not TRACE_ID_RE.match(value) or set(value) == {"0"}:
        raise ValueError("trace_id must be 32 hexadecimal characters and not all zeros")
    return value


def normalize_span_id(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("0x"):
        value = value[2:]
    if not SPAN_ID_RE.match(value) or set(value) == {"0"}:
        raise ValueError("span_id must be 16 hexadecimal characters and not all zeros")
    return value


def normalize_correlation_id(value: str) -> str:
    value = value.strip()
    if not CORRELATION_RE.match(value):
        raise ValueError("correlation_id may only contain letters, digits and . _ : - (max 128 chars)")
    return value


ServiceName = Annotated[str, AfterValidator(normalize_service_name)]
TraceId = Annotated[str, AfterValidator(normalize_trace_id)]
SpanId = Annotated[str, AfterValidator(normalize_span_id)]
CorrelationId = Annotated[str, AfterValidator(normalize_correlation_id)]
