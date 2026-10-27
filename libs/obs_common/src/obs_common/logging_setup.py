"""Structured JSON application logging with secret redaction."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from .context import get_correlation_id
from .redact import redact_mapping, redact_text

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        cid = get_correlation_id()
        if cid:
            payload["correlation_id"] = cid
        extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS and not k.startswith("_")}
        if extras:
            payload.update(redact_mapping(extras))
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = redact_text(
                f"{record.exc_info[0].__name__}: {record.exc_info[1]}"
            )
            payload["stack"] = redact_text(self.formatException(record.exc_info))[-4000:]
        return json.dumps(payload, default=str)


def configure_logging(service_name: str, level: str = "INFO") -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_obs_json", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name))
    handler._obs_json = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("pika").setLevel(logging.WARNING)
