"""Pure functions that normalise raw log fields (unit tested)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from obs_common.redact import redact_text
from obs_common.timeutil import parse_timestamp, to_iso
from obs_common.validation import clean_text

CANONICAL = ("INFO", "WARNING", "ERROR", "CRITICAL")

_SEVERITY_ALIASES = {
    "TRACE": "INFO", "DEBUG": "INFO", "VERBOSE": "INFO", "INFO": "INFO", "INFORMATION": "INFO", "INFORMATIONAL": "INFO",
    "NOTICE": "INFO", "LOG": "INFO", "OK": "INFO", "SUCCESS": "INFO",
    "WARN": "WARNING", "WARNING": "WARNING",
    "ERR": "ERROR", "ERROR": "ERROR", "SEVERE": "ERROR", "FAIL": "ERROR", "FAILURE": "ERROR",
    "CRIT": "CRITICAL", "CRITICAL": "CRITICAL", "FATAL": "CRITICAL", "EMERG": "CRITICAL", "EMERGENCY": "CRITICAL",
    "ALERT": "CRITICAL", "PANIC": "CRITICAL",
}


def normalize_severity(raw: Any) -> str:
    """Map arbitrary severities onto INFO / WARNING / ERROR / CRITICAL (unknown => INFO)."""
    if raw is None:
        return "INFO"
    if isinstance(raw, bool):
        return "INFO"
    if isinstance(raw, (int, float)):
        return _numeric(float(raw))
    text = str(raw).strip().upper()
    if not text:
        return "INFO"
    if text in _SEVERITY_ALIASES:
        return _SEVERITY_ALIASES[text]
    try:
        return _numeric(float(text))
    except ValueError:
        return "INFO"


def _numeric(value: float) -> str:
    """Numeric levels. Heuristic (documented): 10/20 are Python DEBUG/INFO; values >= 25 use the
    Python/bunyan scale (30 WARNING, 40 ERROR, 50+ CRITICAL); 1-24 use the OpenTelemetry severity numbers."""
    if value in (10.0, 20.0):
        return "INFO"
    if value >= 25:
        if value >= 50:
            return "CRITICAL"
        if value >= 40:
            return "ERROR"
        if value >= 30:
            return "WARNING"
        return "INFO"
    if value >= 21:
        return "CRITICAL"
    if value >= 17:
        return "ERROR"
    if value >= 13:
        return "WARNING"
    return "INFO"


def normalize_message(raw: Any, max_length: int = 8000) -> str:
    """Unstructured / structured messages -> safe text (control chars removed, secrets redacted, truncated)."""
    if raw is None:
        text = ""
    elif isinstance(raw, str):
        text = raw
    else:
        try:
            text = json.dumps(raw, sort_keys=True, default=str)
        except (TypeError, ValueError):
            text = str(raw)
    return redact_text(clean_text(text, max_length)).strip()


def normalize_timestamp(raw: Any, fallback: datetime) -> datetime:
    """Return a naive UTC timestamp; missing/unparseable values fall back to the receive time."""
    if raw in (None, ""):
        return fallback
    try:
        return parse_timestamp(raw)
    except ValueError:
        return fallback


def normalize_service(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    text = re.sub(r"[^a-z0-9._\-]", "-", text)[:100].strip("-._")
    return text or "unknown-service"


_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX = re.compile(r"\b(?:0x)?[0-9a-fA-F]{12,}\b")
_NUM = re.compile(r"\b\d+(?:\.\d+)?\b")
_QUOTED = re.compile(r"(['\"])(?:(?!\1).){1,80}\1")
_WS = re.compile(r"\s+")


def error_pattern(message: str) -> str:
    """Collapse variable parts so repeated failures share one pattern."""
    text = _UUID.sub("<uuid>", message)
    text = _HEX.sub("<hex>", text)
    text = _QUOTED.sub("<str>", text)
    text = _NUM.sub("<n>", text)
    return _WS.sub(" ", text).strip()[:300]


def pattern_hash(service: str, pattern: str) -> str:
    return hashlib.sha1(f"{service}|{pattern}".encode()).hexdigest()


def fingerprint(service: str, severity: str, timestamp: datetime, trace_id: str | None, span_id: str | None, message: str) -> str:
    """Content hash used to avoid storing the same log record twice (even under different event ids)."""
    raw = "|".join([service, severity, to_iso(timestamp) or "", trace_id or "", span_id or "", message])
    return hashlib.sha256(raw.encode()).hexdigest()
