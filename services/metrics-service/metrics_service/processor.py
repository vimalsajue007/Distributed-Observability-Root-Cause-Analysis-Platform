"""Metric ingestion from the broker, bucketed aggregation, queries and alert evaluation."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from obs_common.broker import NonRetryableError
from obs_common.db import Database
from obs_common.redact import redact_mapping
from obs_common.schemas import Envelope, MetricPayload, summarize_validation_error
from obs_common.timeutil import floor_to_bucket, parse_timestamp, to_iso, utcnow

from .alerting import ALERT_TYPES, Observation, apply_observation
from .config import Settings
from .models import REQUEST_METRIC, Alert, AlertRule, MetricAggregate, MetricSample
from .stats import METHOD, error_rate, percentile_nearest_rank, rnd, summarize

log = logging.getLogger("metrics-aggregation")

HISTOGRAM_BOUNDS_MS = (10, 25, 50, 100, 250, 500, 1000, 2500, 5000)


class MetricsProcessor:
    def __init__(self, db: Database, settings: Settings, clock=utcnow):
        self.db = db
        self.settings = settings
        self.clock = clock
        self.bucket = settings.aggregation_bucket_seconds
        self._dirty: set[tuple[str, str, datetime]] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_eval = datetime.min
        self._last_purge = datetime.min

    # ------------------------------------------------------------------ consumer

    def handle_event(self, payload: dict[str, Any]) -> str:
        try:
            envelope = Envelope.model_validate(payload)
            if envelope.event_type != "metric":
                raise NonRetryableError(f"unexpected event_type {envelope.event_type!r} on metric queue")
            model = envelope.parse_payload()
            assert isinstance(model, MetricPayload)
        except ValidationError as exc:
            raise NonRetryableError(f"invalid metric event: {summarize_validation_error(exc)}") from exc
        received = parse_timestamp(envelope.received_at) if envelope.received_at else self.clock()
        return self.store_sample(model, envelope.event_id, received)

    def store_sample(self, model: MetricPayload, event_id: str, received_at: datetime) -> str:
        recorded = model.timestamp or received_at
        labels = redact_mapping(model.labels)
        is_error = bool(labels.get("error")) or (isinstance(labels.get("status_code"), (int, float)) and labels["status_code"] >= 500)
        try:
            with self.db.session() as s:
                if s.scalar(select(MetricSample.id).where(MetricSample.event_id == event_id)) is not None:
                    return "duplicate"          # same event delivered twice => counted once
                s.add(
                    MetricSample(
                        event_id=event_id, service_name=model.service_name, metric_name=model.metric_name,
                        metric_value=model.value, recorded_at=recorded,
                        is_error=is_error if model.metric_name == REQUEST_METRIC else False,
                        trace_id=model.trace_id, labels=labels,
                    )
                )
        except IntegrityError:
            return "duplicate"
        with self._lock:
            self._dirty.add((model.service_name, model.metric_name, floor_to_bucket(recorded, self.bucket)))
        return "stored"

    # ------------------------------------------------------------------ aggregation

    def flush_dirty(self) -> int:
        """Re-compute every bucket touched since the last flush (late/out-of-order samples included)."""
        with self._lock:
            dirty, self._dirty = self._dirty, set()
        if not dirty:
            return 0
        try:
            with self.db.session() as s:
                for service, metric, start in sorted(dirty):
                    self._recompute_bucket(s, service, metric, start, self.bucket)
        except Exception:
            with self._lock:
                self._dirty |= dirty  # try again next cycle
            raise
        return len(dirty)

    def _recompute_bucket(self, s: Session, service: str, metric: str, start: datetime, seconds: int) -> None:
        end = start + timedelta(seconds=seconds)
        rows = s.execute(
            select(MetricSample.metric_value, MetricSample.is_error).where(
                MetricSample.service_name == service, MetricSample.metric_name == metric,
                MetricSample.recorded_at >= start, MetricSample.recorded_at < end,
            )
        ).all()
        if not rows:
            return
        stats = summarize(v for v, _ in rows)
        errors = sum(1 for _, e in rows if e)
        percentile_data = {"method": METHOD, **{f"p{p}": rnd(stats[f"p{p}"]) for p in (50, 90, 95, 99)}}
        agg = s.scalar(
            select(MetricAggregate).where(
                MetricAggregate.service_name == service, MetricAggregate.metric_name == metric,
                MetricAggregate.interval_start == start, MetricAggregate.interval_seconds == seconds,
            )
        )
        if agg is None:
            agg = MetricAggregate(service_name=service, metric_name=metric, interval_start=start, interval_end=end, interval_seconds=seconds, sample_count=0, sum_value=0.0)
            s.add(agg)
        agg.sample_count = stats["count"]
        agg.error_count = errors
        agg.sum_value = stats["sum"]
        agg.average_value = stats["avg"]
        agg.minimum_value = stats["min"]
        agg.maximum_value = stats["max"]
        agg.percentile_data = percentile_data

    # ------------------------------------------------------------------ queries (always from raw samples for correct percentiles)

    def _request_rows(self, s: Session, start: datetime, end: datetime, service: str | None = None):
        q = select(
            MetricSample.service_name, MetricSample.metric_value, MetricSample.is_error, MetricSample.trace_id, MetricSample.recorded_at
        ).where(MetricSample.metric_name == REQUEST_METRIC, MetricSample.recorded_at >= start, MetricSample.recorded_at < end)
        if service:
            q = q.where(MetricSample.service_name == service)
        return s.execute(q).all()

    @staticmethod
    def _describe(rows: list[tuple], seconds: float) -> dict[str, Any]:
        values = [r[1] for r in rows]
        errors = sum(1 for r in rows if r[2])
        st = summarize(values)
        rate = error_rate(errors, st["count"])
        return {
            "request_count": st["count"],
            "error_count": errors,
            "error_rate": rnd(rate, 4),
            "avg_latency_ms": rnd(st["avg"]),
            "min_latency_ms": rnd(st["min"]),
            "max_latency_ms": rnd(st["max"]),
            "p50_latency_ms": rnd(st["p50"]),
            "p90_latency_ms": rnd(st["p90"]),
            "p95_latency_ms": rnd(st["p95"]),
            "p99_latency_ms": rnd(st["p99"]),
            "throughput_rps": rnd(st["count"] / seconds, 4) if seconds > 0 else None,
        }

    def services(self, s: Session, start: datetime, end: datetime) -> list[dict[str, Any]]:
        rows = self._request_rows(s, start, end)
        seconds = (end - start).total_seconds()
        by_service: dict[str, list[tuple]] = {}
        for r in rows:
            by_service.setdefault(r[0], []).append(r)
        out = []
        for name, items in sorted(by_service.items()):
            row = {"service_name": name, **self._describe(items, seconds), "last_seen_at": max(r[4] for r in items)}
            row["status"] = "degraded" if (row["error_rate"] or 0) > 0 else "healthy"
            out.append(row)
        return out

    def summary(self, s: Session, start: datetime, end: datetime, service: str | None) -> dict[str, Any]:
        rows = self._request_rows(s, start, end, service)
        seconds = (end - start).total_seconds()
        per_service: dict[str, list[tuple]] = {}
        for r in rows:
            per_service.setdefault(r[0], []).append(r)
        failing = []
        for name, items in sorted(per_service.items()):
            errs = sum(1 for r in items if r[2])
            if errs:
                failing.append({"service_name": name, "error_count": errs, "error_rate": rnd(errs / len(items), 4), "request_count": len(items)})
        failing.sort(key=lambda x: (-x["error_count"], x["service_name"]))
        active = s.scalar(select(func.count()).select_from(Alert).where(Alert.status.in_(("open", "acknowledged")))) or 0
        critical = s.scalar(select(func.count()).select_from(Alert).where(Alert.status.in_(("open", "acknowledged")), Alert.severity == "critical")) or 0
        return {
            "window": {"start": to_iso(start), "end": to_iso(end)},
            **self._describe(rows, seconds),
            "services_observed": len(per_service),
            "services_with_errors": failing,
            "active_alerts": int(active),
            "critical_alerts": int(critical),
            "percentile_method": METHOD,
        }

    def latency(self, s: Session, start: datetime, end: datetime, service: str | None) -> dict[str, Any]:
        rows = self._request_rows(s, start, end, service)
        per_service: dict[str, list[float]] = {}
        for r in rows:
            per_service.setdefault(r[0], []).append(r[1])
        services = []
        for name, values in sorted(per_service.items()):
            st = summarize(values)
            services.append({"service_name": name, "count": st["count"], "avg_ms": rnd(st["avg"]), "min_ms": rnd(st["min"]), "max_ms": rnd(st["max"]),
                             "p50_ms": rnd(st["p50"]), "p90_ms": rnd(st["p90"]), "p95_ms": rnd(st["p95"]), "p99_ms": rnd(st["p99"])})
        values = sorted(r[1] for r in rows)
        histogram, prev = [], 0
        for bound in HISTOGRAM_BOUNDS_MS:
            count = sum(1 for v in values if v <= bound)
            histogram.append({"le_ms": bound, "count": count - prev, "cumulative": count})
            prev = count
        histogram.append({"le_ms": None, "count": len(values) - prev, "cumulative": len(values)})
        return {"window": {"start": to_iso(start), "end": to_iso(end)}, "percentile_method": METHOD, "services": services, "histogram": histogram}

    def timeseries(self, s: Session, service: str | None, metric: str, start: datetime, end: datetime, bucket_seconds: int | None) -> dict[str, Any]:
        bucket = bucket_seconds or self.bucket
        first = floor_to_bucket(start, bucket)
        slots = int((end - first).total_seconds() // bucket) + 1
        if slots > 1000:
            from obs_common.api import AppError

            raise AppError(422, "too_many_points", f"The range would return {slots} points (max 1000); use a larger bucket_seconds or a shorter range")
        points: dict[datetime, dict[str, Any]] = {}
        if service and bucket == self.bucket:
            # served from the pre-computed aggregate table
            for a in s.scalars(
                select(MetricAggregate).where(
                    MetricAggregate.service_name == service, MetricAggregate.metric_name == metric,
                    MetricAggregate.interval_seconds == bucket, MetricAggregate.interval_start >= first, MetricAggregate.interval_start < end,
                )
            ):
                pd = a.percentile_data or {}
                points[a.interval_start] = {
                    "sample_count": a.sample_count, "error_count": a.error_count, "average": rnd(a.average_value), "min": rnd(a.minimum_value),
                    "max": rnd(a.maximum_value), "p50": pd.get("p50"), "p95": pd.get("p95"), "p99": pd.get("p99"),
                }
            source = "aggregates"
        else:
            q = select(MetricSample.metric_value, MetricSample.is_error, MetricSample.recorded_at).where(
                MetricSample.metric_name == metric, MetricSample.recorded_at >= first, MetricSample.recorded_at < end
            )
            if service:
                q = q.where(MetricSample.service_name == service)
            grouped: dict[datetime, list[tuple[float, bool]]] = {}
            for value, is_err, ts in s.execute(q):
                grouped.setdefault(floor_to_bucket(ts, bucket), []).append((value, is_err))
            for slot, items in grouped.items():
                st = summarize(v for v, _ in items)
                points[slot] = {
                    "sample_count": st["count"], "error_count": sum(1 for _, e in items if e), "average": rnd(st["avg"]), "min": rnd(st["min"]),
                    "max": rnd(st["max"]), "p50": rnd(st["p50"]), "p95": rnd(st["p95"]), "p99": rnd(st["p99"]),
                }
            source = "raw_samples"
        series = []
        for i in range(slots):
            slot = first + timedelta(seconds=i * bucket)
            p = points.get(slot)
            if p is None:  # missing measurement interval: explicit gap, not a fake zero
                series.append({"interval_start": slot, "interval_end": slot + timedelta(seconds=bucket), "sample_count": 0, "error_count": 0,
                               "error_rate": None, "average": None, "min": None, "max": None, "p50": None, "p95": None, "p99": None,
                               "throughput_rps": None, "missing": True})
                continue
            count = p["sample_count"]
            errors = p["error_count"] if metric == REQUEST_METRIC else 0
            series.append({"interval_start": slot, "interval_end": slot + timedelta(seconds=bucket), **p, "error_count": errors,
                           "error_rate": rnd(error_rate(errors, count), 4) if metric == REQUEST_METRIC else None,
                           "throughput_rps": rnd(count / bucket, 4), "missing": False})
        return {"service_name": service, "metric_name": metric, "bucket_seconds": bucket, "source": source, "percentile_method": METHOD, "points": series}

    # ------------------------------------------------------------------ alert rules & evaluation

    def seed_rules(self) -> None:
        cfg = self.settings
        defaults = [
            ("error_rate", "gt", cfg.default_error_rate_threshold, "critical", cfg.default_min_samples),
            ("avg_latency", "gt", cfg.default_avg_latency_threshold_ms, "warning", cfg.default_min_samples),
            ("p95_latency", "gt", cfg.default_p95_latency_threshold_ms, "warning", cfg.default_min_samples),
            ("request_volume", "gt", cfg.default_request_volume_threshold, "warning", 1),
        ]
        with self.db.session() as s:
            for alert_type, op, threshold, severity, min_samples in defaults:
                exists = s.scalar(select(AlertRule.id).where(AlertRule.service_name == "*", AlertRule.alert_type == alert_type))
                if exists is None:
                    s.add(AlertRule(service_name="*", alert_type=alert_type, operator=op, threshold=threshold, window_seconds=cfg.default_window_seconds,
                                    min_samples=min_samples, severity=severity, enabled=True))

    def evaluate_alerts(self, now: datetime | None = None) -> dict[str, int]:
        now = now or self.clock()
        counts = {"created": 0, "updated": 0, "resolved": 0, "none": 0}
        with self.db.session() as s:
            rules = s.scalars(select(AlertRule).where(AlertRule.enabled.is_(True))).all()
            by_key = {(r.service_name, r.alert_type): r for r in rules}
            if not rules:
                return counts
            max_window = max(r.window_seconds for r in rules)
            rows = self._request_rows(s, now - timedelta(seconds=max_window), now + timedelta(seconds=1))
            services = {r[0] for r in rows} | {a for (a,) in s.execute(select(Alert.service_name).where(Alert.status.in_(("open", "acknowledged"))))}
            for service in sorted(services):
                for alert_type in ALERT_TYPES:
                    rule = by_key.get((service, alert_type)) or by_key.get(("*", alert_type))
                    if rule is None:
                        continue
                    window_rows = [r for r in rows if r[0] == service and r[4] >= now - timedelta(seconds=rule.window_seconds)]
                    st = summarize(r[1] for r in window_rows)
                    errors = sum(1 for r in window_rows if r[2])
                    value: float | None
                    if alert_type == "request_volume":
                        value = float(st["count"])
                    elif st["count"] < rule.min_samples:
                        value = None  # not enough data to judge
                    elif alert_type == "error_rate":
                        value = errors / st["count"]
                    elif alert_type == "avg_latency":
                        value = st["avg"]
                    else:
                        value = percentile_nearest_rank(sorted(r[1] for r in window_rows), 95)
                    evidence = self._evidence(window_rows, alert_type, rule, errors)
                    outcome = apply_observation(s, Observation(service, rule, value, st["count"], evidence), now, self.settings.alert_resolve_after_seconds)
                    counts[outcome] += 1
        return counts

    @staticmethod
    def _evidence(rows: list[tuple], alert_type: str, rule: AlertRule, errors: int) -> dict[str, Any]:
        if alert_type == "error_rate":
            candidates = [r for r in rows if r[2] and r[3]]
            candidates.sort(key=lambda r: r[4], reverse=True)
        else:
            candidates = [r for r in rows if r[3]]
            candidates.sort(key=lambda r: r[1], reverse=True)
        trace_ids = list(dict.fromkeys(r[3] for r in candidates))[:5]
        return {
            "window_seconds": rule.window_seconds,
            "sample_count": len(rows),
            "error_count": errors,
            "trace_ids": trace_ids,
            "measurement": {"metric": REQUEST_METRIC, "percentile_method": METHOD},
        }

    # ------------------------------------------------------------------ retention

    def purge(self, now: datetime | None = None) -> int:
        now = now or self.clock()
        cutoff = now - timedelta(hours=self.settings.raw_retention_hours)
        with self.db.session() as s:
            result = s.execute(delete(MetricSample).where(MetricSample.recorded_at < cutoff))
            return int(result.rowcount or 0)

    # ------------------------------------------------------------------ background worker

    def start_background(self) -> None:
        if self._thread:
            return
        self.seed_rules()

        def loop() -> None:
            while not self._stop.wait(self.settings.flush_interval_seconds):
                try:
                    self.flush_dirty()
                    now = self.clock()
                    if (now - self._last_eval).total_seconds() >= self.settings.alert_eval_interval_seconds:
                        self._last_eval = now
                        self.evaluate_alerts(now)
                    if (now - self._last_purge).total_seconds() >= 600:
                        self._last_purge = now
                        self.purge(now)
                except Exception as exc:  # noqa: BLE001
                    log.warning("metrics worker cycle failed", extra={"error": repr(exc)})

        self._thread = threading.Thread(target=loop, name="metrics-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
