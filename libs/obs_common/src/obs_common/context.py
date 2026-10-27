"""Request-scoped context shared by logging, HTTP clients and telemetry."""

from __future__ import annotations

import contextvars
import re
import uuid

CORRELATION_HEADER = "X-Correlation-ID"
CORRELATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$")

correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def get_correlation_id() -> str | None:
    return correlation_id_var.get()


def sanitize_correlation_id(value: str | None) -> str:
    """Return a safe correlation id; generate one if the supplied value is missing/invalid."""
    if value and CORRELATION_RE.match(value):
        return value
    return new_correlation_id()
