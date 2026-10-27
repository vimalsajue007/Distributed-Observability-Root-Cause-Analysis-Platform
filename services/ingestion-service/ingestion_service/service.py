"""Ingestion pipeline: validate -> normalise -> publish (with bounded retry) -> record statistics."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError

from obs_common.broker import Broker, BrokerUnavailable
from obs_common.db import Database
from obs_common.schemas import PAYLOAD_MODELS, ROUTING_KEYS, SpanPayload, build_envelope
from obs_common.timeutil import utcnow

from .config import Settings
from .models import FAILED, PENDING, PUBLISHED, REJECTED, IngestionEvent

log = logging.getLogger("ingestion")


@dataclass
class IngestResult:
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    queued_for_retry: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def merge(self, other: "IngestResult", offset_label: str | None = None) -> None:
        self.accepted += other.accepted
        self.duplicates += other.duplicates
        self.rejected += other.rejected
        self.queued_for_retry += other.queued_for_retry
        for err in other.errors:
            if offset_label:
                err = {**err, "stream": offset_label}
            self.errors.append(err)

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "queued_for_retry": self.queued_for_retry,
            "errors": self.errors[:100],
        }


def _error_list(exc: ValidationError) -> list[dict[str, str]]:
    # Never echo submitted values back.
    return [
        {"field": ".".join(str(p) for p in e.get("loc", ())) or "(event)", "message": e.get("msg", "invalid")}
        for e in exc.errors()
    ]


class IngestionService:
    def __init__(self, db: Database, broker: Broker, settings: Settings):
        self.db = db
        self.broker = broker
        self.settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ ingestion

    def ingest(self, event_type: str, raw_events: list[Any]) -> IngestResult:
        model_cls = PAYLOAD_MODELS[event_type]
        routing_key = ROUTING_KEYS[event_type]
        result = IngestResult()
        rows: list[IngestionEvent] = []
        seen: set[str] = set()
        now = utcnow()

        for index, raw in enumerate(raw_events):
            if not isinstance(raw, dict):
                result.rejected += 1
                result.errors.append({"index": index, "event_type": event_type, "errors": [{"field": "(event)", "message": "event must be a JSON object"}]})
                rows.append(self._rejected_row(event_type, None, None, "event must be a JSON object", now))
                continue
            try:
                model = model_cls.model_validate(raw)
            except ValidationError as exc:
                errors = _error_list(exc)
                result.rejected += 1
                result.errors.append({"index": index, "event_type": event_type, "errors": errors})
                svc = raw.get("service_name") if isinstance(raw.get("service_name"), str) else None
                rows.append(self._rejected_row(event_type, svc[:100] if svc else None, None, "; ".join(f"{e['field']}: {e['message']}" for e in errors)[:1000], now))
                continue

            event_id = model.event_id or (model.default_event_id() if isinstance(model, SpanPayload) else f"{event_type}-{uuid.uuid4().hex}")
            if event_id in seen:
                result.duplicates += 1
                continue
            seen.add(event_id)
            if self._already_ingested(event_id):
                result.duplicates += 1
                continue

            envelope = build_envelope(event_type, model, event_id, now)
            status, error = self._publish(routing_key, envelope, event_id)
            row = IngestionEvent(
                event_id=event_id,
                event_type=event_type,
                service_name=model.service_name,
                trace_id=getattr(model, "trace_id", None),
                received_at=now,
                processing_status=status,
                error_message=error,
                routing_key=routing_key,
                attempts=1 if status != PUBLISHED else 0,
                payload=envelope if status == PENDING else None,
            )
            rows.append(row)
            result.accepted += 1
            if status != PUBLISHED:
                result.queued_for_retry += 1

        self._record(rows, require_durable=result.queued_for_retry > 0)
        return result

    def _already_ingested(self, event_id: str) -> bool:
        try:
            with self.db.session() as s:
                return s.scalar(select(IngestionEvent.id).where(IngestionEvent.event_id == event_id, IngestionEvent.processing_status.in_((PUBLISHED, PENDING)))) is not None
        except SQLAlchemyError:
            log.warning("duplicate check skipped (database unavailable)")
            return False  # consumers are idempotent, so this is safe

    def _publish(self, routing_key: str, envelope: dict[str, Any], event_id: str) -> tuple[str, str | None]:
        last: Exception | None = None
        for attempt in range(self.settings.publish_retries + 1):
            try:
                self.broker.publish(routing_key, envelope, message_id=event_id)
                return PUBLISHED, None
            except BrokerUnavailable as exc:
                last = exc
                if attempt < self.settings.publish_retries:
                    time.sleep(self.settings.publish_backoff_seconds * (2**attempt))
        log.warning("broker unavailable, event stored for re-publication", extra={"event_id": event_id})
        return PENDING, f"broker unavailable: {last}"[:1000]

    def _rejected_row(self, event_type: str, service: str | None, trace_id: str | None, message: str, now) -> IngestionEvent:
        return IngestionEvent(
            event_id=f"rejected-{uuid.uuid4().hex}",
            event_type=event_type,
            service_name=service,
            trace_id=trace_id,
            received_at=now,
            processing_status=REJECTED,
            error_message=message,
        )

    def _record(self, rows: list[IngestionEvent], *, require_durable: bool) -> None:
        """Persist statistics. If events are only held in the DB (broker down) a DB failure must surface."""
        if not rows:
            return
        try:
            with self.db.session() as s:
                s.add_all(rows)
        except SQLAlchemyError as exc:
            if require_durable:
                raise
            log.error("could not record ingestion statistics", extra={"error": repr(exc)})

    # ------------------------------------------------------------------ re-publication of buffered events

    def republish_pending(self) -> int:
        """Publish events that were buffered while the broker was down. Returns number published."""
        published = 0
        with self.db.session() as s:
            rows = s.scalars(
                select(IngestionEvent)
                .where(IngestionEvent.processing_status == PENDING)
                .order_by(IngestionEvent.id)
                .limit(self.settings.republish_batch_size)
            ).all()
            for row in rows:
                try:
                    self.broker.publish(row.routing_key or ROUTING_KEYS.get(row.event_type, ""), row.payload or {}, message_id=row.event_id)
                except BrokerUnavailable as exc:
                    row.attempts += 1
                    row.error_message = f"broker unavailable: {exc}"[:1000]
                    if row.attempts >= self.settings.republish_max_attempts:
                        row.processing_status = FAILED
                    break  # broker is down: stop this cycle
                row.processing_status = PUBLISHED
                row.payload = None
                row.error_message = None
                published += 1
        if published:
            log.info("re-published buffered events", extra={"count": published})
        return published

    def start_background(self) -> None:
        if self._thread:
            return

        def loop() -> None:
            while not self._stop.wait(self.settings.republish_interval_seconds):
                try:
                    self.republish_pending()
                except Exception as exc:  # noqa: BLE001
                    log.warning("republish cycle failed", extra={"error": repr(exc)})

        self._thread = threading.Thread(target=loop, name="republisher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ statistics

    def stats(self, since) -> dict[str, Any]:
        with self.db.session() as s:
            rows = s.execute(
                select(IngestionEvent.event_type, IngestionEvent.processing_status, func.count())
                .where(IngestionEvent.received_at >= since)
                .group_by(IngestionEvent.event_type, IngestionEvent.processing_status)
            ).all()
        by_type: dict[str, dict[str, int]] = {}
        totals = {"published": 0, "pending_publish": 0, "rejected": 0, "failed": 0}
        for event_type, status, count in rows:
            by_type.setdefault(event_type, {})[status] = count
            totals[status] = totals.get(status, 0) + count
        total = sum(totals.values())
        success = totals["published"]
        return {
            "since": since,
            "totals": {**totals, "received": total},
            "success_rate": round(success / total, 4) if total else None,
            "by_event_type": by_type,
        }
