"""Consumes span events, persists them idempotently and maintains the trace summary rows."""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from obs_common.broker import Broker, BrokerUnavailable, NonRetryableError
from obs_common.db import Database
from obs_common.redact import redact_mapping, redact_text
from obs_common.schemas import SCHEMA_VERSION, TRACE_COMPLETED_ROUTING_KEY, Envelope, SpanPayload, summarize_validation_error
from obs_common.timeutil import to_iso, utcnow

from .config import Settings
from .models import Trace, TraceSpan
from .reconstruction import Reconstruction, SlowPolicy, SpanRec, reconstruct

log = logging.getLogger("trace-processing")


def to_rec(row: TraceSpan) -> SpanRec:
    return SpanRec(
        span_id=row.span_id,
        parent_span_id=row.parent_span_id,
        service_name=row.service_name,
        operation_name=row.operation_name,
        start_time=row.start_time,
        end_time=row.end_time,
        duration_ms=row.duration_ms,
        status=row.status,
        error_message=row.error_message,
        kind=row.kind,
        peer_service=row.peer_service,
        correlation_id=row.correlation_id,
        attributes=row.attributes or {},
    )


class TraceProcessor:
    def __init__(self, db: Database, broker: Broker | None, settings: Settings):
        self.db = db
        self.broker = broker
        self.settings = settings
        self.policy = SlowPolicy(
            settings.slow_span_ms, settings.slow_baseline_factor, settings.slow_baseline_min_samples, settings.slow_baseline_min_ms
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ consumer

    def handle_event(self, payload: dict[str, Any]) -> str:
        """Broker handler. Idempotent: returns 'stored' or 'duplicate'."""
        try:
            envelope = Envelope.model_validate(payload)
            if envelope.event_type != "trace":
                raise NonRetryableError(f"unexpected event_type {envelope.event_type!r} on trace queue")
            span = envelope.parse_payload()
            assert isinstance(span, SpanPayload)
        except ValidationError as exc:
            raise NonRetryableError(f"invalid trace event: {summarize_validation_error(exc)}") from exc
        return self.store_span(span, envelope.event_id)

    def store_span(self, span: SpanPayload, event_id: str) -> str:
        now = utcnow()
        with self.db.session() as s:
            trace = s.scalar(select(Trace).where(Trace.trace_id == span.trace_id))
            if trace is None:
                trace = Trace(
                    trace_id=span.trace_id,
                    start_time=span.start_time,
                    end_time=span.end_time,
                    duration_ms=span.duration_ms,
                    status="OK",
                    completion_state="in_progress",
                    last_event_at=now,
                )
                s.add(trace)
                s.flush()
            exists = s.scalar(
                select(TraceSpan.id).where(TraceSpan.trace_id == span.trace_id, TraceSpan.span_id == span.span_id)
            )
            if exists is not None:
                log.debug("duplicate span ignored", extra={"trace_id": span.trace_id, "span_id": span.span_id})
                return "duplicate"
            attrs = redact_mapping(span.attributes)
            s.add(
                TraceSpan(
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    parent_span_id=span.parent_span_id,
                    service_name=span.service_name,
                    operation_name=span.operation_name,
                    start_time=span.start_time,
                    end_time=span.end_time,
                    duration_ms=span.duration_ms,
                    status="ERROR" if span.status == "ERROR" else "OK",
                    error_message=redact_text(span.error_message) if span.error_message else None,
                    kind=span.kind,
                    peer_service=str(attrs.get("peer.service"))[:100] if attrs.get("peer.service") else None,
                    correlation_id=span.correlation_id or (str(attrs["correlation_id"])[:128] if attrs.get("correlation_id") else None),
                    event_id=event_id,
                    attributes=attrs,
                    received_at=now,
                )
            )
            s.flush()
            self._recompute(s, trace, now)
        return "stored"

    def _recompute(self, s: Session, trace: Trace, now) -> Reconstruction:
        rows = s.scalars(select(TraceSpan).where(TraceSpan.trace_id == trace.trace_id)).all()
        recon = reconstruct([to_rec(r) for r in rows])
        trace.start_time = recon.start_time or trace.start_time
        trace.end_time = recon.end_time or trace.end_time
        trace.duration_ms = recon.duration_ms
        trace.root_service = recon.root_service
        trace.status = recon.status
        trace.span_count = len(recon.spans)
        trace.error_span_count = recon.error_span_count
        trace.missing_parent_count = recon.missing_parent_count
        trace.correlation_id = recon.correlation_id
        trace.last_event_at = now
        if trace.completion_state != "in_progress":
            # a late span re-opens the trace so that the completion policy is evaluated again
            trace.completion_state = "in_progress"
            trace.completed_at = None
        return recon

    # ------------------------------------------------------------------ completion policy

    def finalize_due(self, now=None) -> list[str]:
        """Finalise traces that were quiet for ``trace_completion_wait_seconds``. Returns finalised trace ids."""
        now = now or utcnow()
        cutoff = now - timedelta(seconds=self.settings.trace_completion_wait_seconds)
        finalized: list[tuple[str, str, bool]] = []
        with self.db.session() as s:
            due = s.scalars(
                select(Trace).where(Trace.completion_state == "in_progress", Trace.last_event_at <= cutoff).order_by(Trace.id).limit(200)
            ).all()
            for trace in due:
                rows = s.scalars(select(TraceSpan).where(TraceSpan.trace_id == trace.trace_id)).all()
                recon = reconstruct([to_rec(r) for r in rows], slow=self.policy.is_slow)
                trace.completion_state = "complete" if recon.is_complete else "incomplete"
                trace.completed_at = now
                has_slow = any(n.is_slow for n in recon.spans)
                finalized.append((trace.trace_id, trace.status, has_slow))
        for trace_id, status, has_slow in finalized:
            if status == "ERROR" or has_slow:
                self._announce(trace_id, status, has_slow, now)
        return [t for t, _, _ in finalized]

    def _announce(self, trace_id: str, status: str, has_slow: bool, now) -> None:
        """Tell the RCA service that a failed/slow trace completed (async, best effort)."""
        if self.broker is None:
            return
        event_id = f"trace-completed:{trace_id}:{int(now.timestamp())}"
        message = {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "event_type": "trace_completed",
            "trace_id": trace_id,
            "status": status,
            "has_slow_span": has_slow,
            "occurred_at": to_iso(now),
        }
        try:
            self.broker.publish(TRACE_COMPLETED_ROUTING_KEY, message, message_id=event_id)
        except BrokerUnavailable:
            log.warning("could not announce completed trace (broker unavailable)", extra={"trace_id": trace_id})

    def start_background(self) -> None:
        if self._thread:
            return

        def loop() -> None:
            while not self._stop.wait(self.settings.finalizer_interval_seconds):
                try:
                    self.finalize_due()
                except Exception as exc:  # noqa: BLE001
                    log.warning("finalizer cycle failed", extra={"error": repr(exc)})

        self._thread = threading.Thread(target=loop, name="trace-finalizer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ baselines for slow detection

    def baselines(self, s: Session, spans: list[SpanRec], exclude_trace_id: str) -> dict[tuple[str, str], tuple[float, int]]:
        since = utcnow() - timedelta(hours=self.settings.baseline_window_hours)
        out: dict[tuple[str, str], tuple[float, int]] = {}
        for service, operation in {(sp.service_name, sp.operation_name) for sp in spans}:
            avg, count = s.execute(
                select(func.avg(TraceSpan.duration_ms), func.count()).where(
                    TraceSpan.service_name == service,
                    TraceSpan.operation_name == operation,
                    TraceSpan.trace_id != exclude_trace_id,
                    TraceSpan.start_time >= since,
                    TraceSpan.status == "OK",
                )
            ).one()
            if count:
                out[(service, operation)] = (float(avg or 0.0), int(count))
        return out
