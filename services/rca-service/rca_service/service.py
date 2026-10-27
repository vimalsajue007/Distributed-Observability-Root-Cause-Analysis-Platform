"""Collects evidence from the trace / log / metrics services and runs the deterministic rules."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from obs_common.broker import NonRetryableError
from obs_common.db import Database
from obs_common.filters import DiagnosticFilters
from obs_common.http import DownstreamError, SyncDownstreamClient
from obs_common.timeutil import parse_timestamp, to_iso, utcnow

from .config import Settings
from .models import DiagnosticEvidence, DiagnosticReport
from .rules import RulesConfig, analyze

log = logging.getLogger("root-cause-analysis")


class RcaService:
    def __init__(self, db: Database, settings: Settings, *, traces: SyncDownstreamClient, logs: SyncDownstreamClient, metrics: SyncDownstreamClient):
        self.db, self.settings = db, settings
        self.traces, self.logs, self.metrics = traces, logs, metrics
        self.cfg = RulesConfig.from_overrides(settings.rule_overrides)

    # ------------------------------------------------------------------ evidence collection

    def _optional(self, client: SyncDownstreamClient, path: str, params: dict[str, Any]) -> list[Any] | None:
        """Best-effort fetch: returns None (-> warning in report) instead of failing the whole analysis."""
        try:
            return client.get(path, params=params).get("data")
        except DownstreamError as exc:
            log.warning("evidence source unavailable", extra={"downstream": exc.service, "kind": exc.kind})
            return None

    def collect_and_analyze(self, trace_id: str) -> dict[str, Any]:
        try:
            trace = self.traces.get(f"/api/v1/traces/{trace_id}").get("data")
        except DownstreamError as exc:
            if exc.status_code == 404:
                raise NonRetryableError(f"trace {trace_id} not found") from exc
            raise
        logs = self._optional(self.logs, "/api/v1/logs", {"trace_id": trace_id, "page_size": 200, "order": "asc"})
        start = parse_timestamp(trace["start_time"]) - timedelta(minutes=self.settings.metrics_window_before_minutes)
        end = parse_timestamp(trace["end_time"]) + timedelta(minutes=self.settings.metrics_window_after_minutes)
        rows = self._optional(self.metrics, "/api/v1/metrics/services", {"start": to_iso(start), "end": to_iso(end)})
        service_metrics = {r["service_name"]: r for r in rows} if rows is not None else None
        alerts = self._optional(self.metrics, "/api/v1/alerts", {"status": "active", "page_size": 100})

        report = analyze(trace, logs=logs, service_metrics=service_metrics, alerts=alerts, related_traces=[], cfg=self.cfg)
        primary = report.get("affected_service")
        if primary and report["classification"] in ("confirmed_failure", "potential_cause"):
            since = utcnow() - timedelta(hours=self.settings.related_traces_lookback_hours)
            related = self._optional(self.traces, "/api/v1/traces", {"service_name": primary, "status": "ERROR", "start": to_iso(since), "page_size": 10})
            if related:
                report = analyze(trace, logs=logs, service_metrics=service_metrics, alerts=alerts, related_traces=related, cfg=self.cfg)
        return report

    # ------------------------------------------------------------------ persistence

    def analyze_trace(self, trace_id: str, trigger: str = "manual") -> dict[str, Any]:
        report = self.collect_and_analyze(trace_id)
        with self.db.session() as s:
            row = s.scalar(select(DiagnosticReport).where(DiagnosticReport.trace_id == trace_id))
            details = {k: report.get(k) for k in (
                "ranking", "failure_timeline", "latency_contributions", "request_path", "service_path", "first_failing_span",
                "origin_span", "related_trace_ids", "warnings", "metric_notes")}
            if row is None:
                row = DiagnosticReport(trace_id=trace_id, trigger=trigger, analysis_count=1, diagnosis="", severity="low", classification="", confidence_level="", summary="", rules_version="")
                s.add(row)
            else:
                row.analysis_count += 1
                row.trigger = trigger
                row.evidence.clear()
                s.flush()
            row.affected_service = report["affected_service"]
            row.diagnosis = report["diagnosis"]
            row.severity = report["severity"]
            row.classification = report["classification"]
            row.confidence_level = report["confidence_level"]
            row.summary = report["summary"]
            row.rules_version = report["rules_version"]
            row.details = details
            row.status = "complete"
            for ev in report["evidence"]:
                row.evidence.append(DiagnosticEvidence(
                    evidence_type=ev["evidence_type"], reference_id=(str(ev["reference_id"])[:128] if ev.get("reference_id") is not None else None),
                    description=ev["description"], service_name=ev.get("service_name"),
                    observed_at=parse_timestamp(ev["observed_at"]) if ev.get("observed_at") else None, data=ev.get("data")))
            s.flush()
            return report_dict(row, include_evidence=True)

    def handle_event(self, payload: dict[str, Any]) -> str:
        """Consumer for `events.trace.completed`. Re-delivery re-runs the analysis, which is idempotent (one report per trace)."""
        trace_id = payload.get("trace_id")
        if payload.get("event_type") != "trace_completed" or not isinstance(trace_id, str) or len(trace_id) != 32:
            raise NonRetryableError("invalid trace_completed event")
        with self.db.session() as s:
            existing = s.scalar(select(DiagnosticReport).where(DiagnosticReport.trace_id == trace_id.lower()))
            if existing is not None and existing.trigger == "event" and payload.get("event_id") and (existing.details or {}).get("last_event_id") == payload["event_id"]:
                return "duplicate"
        self.analyze_trace(trace_id.lower(), trigger="event")
        with self.db.session() as s:
            row = s.scalar(select(DiagnosticReport).where(DiagnosticReport.trace_id == trace_id.lower()))
            if row is not None:
                row.details = {**(row.details or {}), "last_event_id": payload.get("event_id")}
        return "analyzed"

    # ------------------------------------------------------------------ queries

    def list_reports(self, s: Session, f: DiagnosticFilters) -> tuple[list[dict[str, Any]], int]:
        cond = []
        if f.trace_id:
            cond.append(DiagnosticReport.trace_id == f.trace_id)
        if f.affected_service:
            cond.append(DiagnosticReport.affected_service == f.affected_service)
        if f.severity:
            cond.append(DiagnosticReport.severity == f.severity)
        if f.classification:
            cond.append(DiagnosticReport.classification == f.classification)
        if f.start:
            cond.append(DiagnosticReport.created_at >= f.start)
        if f.end:
            cond.append(DiagnosticReport.created_at < f.end)
        total = s.scalar(select(func.count()).select_from(DiagnosticReport).where(*cond)) or 0
        rows = s.scalars(select(DiagnosticReport).where(*cond).order_by(DiagnosticReport.created_at.desc(), DiagnosticReport.id.desc())
                         .offset((f.page - 1) * f.page_size).limit(f.page_size)).all()
        return [report_dict(r) for r in rows], total


def report_dict(r: DiagnosticReport, *, include_evidence: bool = False) -> dict[str, Any]:
    details = dict(r.details or {})
    details.pop("last_event_id", None)
    out = {
        "id": r.id, "trace_id": r.trace_id, "affected_service": r.affected_service, "classification": r.classification,
        "severity": r.severity, "confidence_level": r.confidence_level, "summary": r.summary, "diagnosis": r.diagnosis,
        "status": r.status, "created_at": r.created_at, "updated_at": r.updated_at, "analysis_count": r.analysis_count,
        "trigger": r.trigger, "rules_version": r.rules_version,
    }
    if include_evidence:
        out.update(details)
        out["evidence"] = [
            {"id": e.id, "evidence_type": e.evidence_type, "reference_id": e.reference_id, "description": e.description,
             "service_name": e.service_name, "observed_at": e.observed_at, "data": e.data or {}} for e in r.evidence]
    return out
