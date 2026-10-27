"""Log consumer: normalise, de-duplicate, correlate with traces, track repeated error patterns."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from obs_common.broker import NonRetryableError
from obs_common.db import Database
from obs_common.redact import redact_mapping
from obs_common.schemas import Envelope, LogPayload, summarize_validation_error
from obs_common.timeutil import parse_timestamp, utcnow

from .config import Settings
from .models import ErrorPattern, ProcessedLog
from .normalization import (
    error_pattern, fingerprint, normalize_message, normalize_service, normalize_severity, normalize_timestamp, pattern_hash,
)

log = logging.getLogger("log-processing")


@dataclass
class BatchResult:
    stored: int = 0
    duplicates: int = 0
    failed: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


class LogProcessor:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings

    # ------------------------------------------------------------------ broker handler

    def handle_event(self, payload: dict[str, Any]) -> str:
        try:
            envelope = Envelope.model_validate(payload)
            if envelope.event_type != "log":
                raise NonRetryableError(f"unexpected event_type {envelope.event_type!r} on log queue")
            model = envelope.parse_payload()
            assert isinstance(model, LogPayload)
        except ValidationError as exc:
            raise NonRetryableError(f"invalid log event: {summarize_validation_error(exc)}") from exc
        received = parse_timestamp(envelope.received_at) if envelope.received_at else utcnow()
        return self.store(model, envelope.event_id, received)

    # ------------------------------------------------------------------ batch API (one bad event never stops the batch)

    def process_batch(self, events: list[dict[str, Any]]) -> BatchResult:
        result = BatchResult()
        for index, raw in enumerate(events):
            try:
                outcome = self.handle_event(raw)
                if outcome == "duplicate":
                    result.duplicates += 1
                else:
                    result.stored += 1
            except NonRetryableError as exc:
                result.failed += 1
                result.errors.append({"index": index, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - isolate the failure, continue with the rest
                result.failed += 1
                result.errors.append({"index": index, "error": repr(exc)})
                log.warning("log event failed inside batch", extra={"index": index, "error": repr(exc)})
        return result

    # ------------------------------------------------------------------ storage

    def store(self, model: LogPayload, event_id: str, received_at) -> str:
        service = normalize_service(model.service_name)
        severity = normalize_severity(model.severity)
        message = normalize_message(model.message, self.settings.max_message_length)
        ts = normalize_timestamp(model.timestamp, received_at)
        fp = fingerprint(service, severity, ts, model.trace_id, model.span_id, message)

        try:
            with self.db.session() as s:
                duplicate = s.scalar(
                    select(ProcessedLog.id).where((ProcessedLog.event_id == event_id) | (ProcessedLog.fingerprint == fp))
                )
                if duplicate is not None:
                    return "duplicate"
                s.add(
                    ProcessedLog(
                        event_id=event_id,
                        trace_id=model.trace_id,
                        span_id=model.span_id,
                        service_name=service,
                        severity=severity,
                        message=message,
                        timestamp=ts,
                        correlation_id=model.correlation_id,
                        fingerprint=fp,
                        raw_severity=(str(model.severity)[:32] if model.severity is not None else None),
                        logger=model.logger,
                        attributes=redact_mapping(model.attributes),
                    )
                )
                s.flush()
                if severity in ("ERROR", "CRITICAL"):
                    self._track_pattern(s, service, severity, message, ts, model.trace_id)
        except IntegrityError:
            # concurrent insert of the same event: treated as duplicate delivery
            return "duplicate"
        return "stored"

    def _track_pattern(self, s, service: str, severity: str, message: str, ts, trace_id: str | None) -> None:
        pattern = error_pattern(message)
        digest = pattern_hash(service, pattern)
        row = s.scalar(select(ErrorPattern).where(ErrorPattern.service_name == service, ErrorPattern.pattern_hash == digest))
        if row is None:
            s.add(
                ErrorPattern(
                    service_name=service, pattern_hash=digest, pattern=pattern, sample_message=message, severity=severity,
                    occurrence_count=1, first_seen=ts, last_seen=ts, last_trace_id=trace_id,
                )
            )
        else:
            row.occurrence_count += 1  # only reached for a newly stored (non-duplicate) log
            if ts > row.last_seen:
                row.last_seen = ts
                row.last_trace_id = trace_id or row.last_trace_id
            if severity == "CRITICAL":
                row.severity = "CRITICAL"
