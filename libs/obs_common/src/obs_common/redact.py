"""Secret redaction for log lines and untrusted telemetry text."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_KEYS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "x-api-key",
    "x-internal-token",
    "credential",
    "private_key",
    "cookie",
)

_AUTH_HEADER = re.compile(r"(?i)(authorization\"?\s*[:=]\s*\"?)(?:bearer|basic|token|digest)\s+[^\s\"',;]+")

_PATTERNS = [
    # key=value / key: value / "key": "value"
    re.compile(
        r"(?i)(\"?(?:password|passwd|secret|token|api[_-]?key|authorization|access[_-]?key)\"?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&}]+)"
    ),
    # Bearer tokens
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"),
    # basic credentials inside URLs  scheme://user:pass@host
    re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:)([^@\s/]+)(@)"),
]


def redact_text(text: str) -> str:
    """Mask obvious secrets in free text."""
    if not text:
        return text
    out = _AUTH_HEADER.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    out = _PATTERNS[0].sub(lambda m: f"{m.group(1)}{REDACTED}", out)
    out = _PATTERNS[1].sub(lambda m: f"{m.group(1)}{REDACTED}", out)
    out = _PATTERNS[2].sub(lambda m: f"{m.group(1)}{REDACTED}{m.group(3)}", out)
    return out


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEYS)


def redact_mapping(data: Any, depth: int = 0) -> Any:
    """Recursively mask values whose keys look sensitive."""
    if depth > 6:
        return data
    if isinstance(data, dict):
        return {
            k: (REDACTED if isinstance(k, str) and is_sensitive_key(k) else redact_mapping(v, depth + 1))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact_mapping(v, depth + 1) for v in data]
    if isinstance(data, str):
        return redact_text(data)
    return data
