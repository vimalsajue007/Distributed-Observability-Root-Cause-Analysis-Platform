"""Alert rule evaluation + de-duplication.

Strategy (documented in README):
  * Rules are evaluated on a fixed cadence (``alert_eval_interval_seconds``), never per incoming event.
  * At most ONE active (open/acknowledged) alert exists per (service, alert_type) - enforced by a unique key.
    While the condition keeps being violated the existing alert is updated (last_seen_at, measured value,
    occurrence_count += 1 per evaluation cycle, evidence refreshed) instead of creating new alerts.
  * An active alert resolves automatically after the condition has been clear for ``alert_resolve_after_seconds``.
    A later violation opens a *new* alert.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Alert, AlertRule

ALERT_TYPES = ("error_rate", "avg_latency", "p95_latency", "request_volume")
LABELS = {
    "error_rate": "error rate",
    "avg_latency": "average latency",
    "p95_latency": "p95 latency",
    "request_volume": "request volume",
}


def violated(value: float | None, operator: str, threshold: float) -> bool:
    if value is None:
        return False
    return value > threshold if operator == "gt" else value < threshold


def format_value(alert_type: str, value: float) -> str:
    if alert_type == "error_rate":
        return f"{value * 100:.1f}%"
    if alert_type == "request_volume":
        return f"{value:.0f} requests"
    return f"{value:.0f} ms"


def build_message(service: str, alert_type: str, value: float, rule: AlertRule, sample_count: int) -> str:
    verb = "exceeds" if rule.operator == "gt" else "is below"
    return (
        f"{service}: {LABELS[alert_type]} {format_value(alert_type, value)} {verb} threshold "
        f"{format_value(alert_type, rule.threshold)} (last {rule.window_seconds}s, {sample_count} requests)"
    )


@dataclass
class Observation:
    service: str
    rule: AlertRule
    value: float | None
    sample_count: int
    evidence: dict[str, Any]


def dedup_key(service: str, alert_type: str) -> str:
    return f"{service}|{alert_type}"


def apply_observation(s: Session, obs: Observation, now: datetime, resolve_after: float) -> str:
    """Create / update / resolve alerts for one (service, rule). Returns 'created'|'updated'|'resolved'|'none'."""
    rule = obs.rule
    key = dedup_key(obs.service, rule.alert_type)
    active = s.scalar(select(Alert).where(Alert.active_key == key))
    is_violated = obs.value is not None and violated(obs.value, rule.operator, rule.threshold)

    if is_violated:
        assert obs.value is not None
        message = build_message(obs.service, rule.alert_type, obs.value, rule, obs.sample_count)
        if active is None:
            s.add(
                Alert(
                    service_name=obs.service, alert_type=rule.alert_type, measured_value=obs.value, threshold_value=rule.threshold,
                    status="open", first_seen_at=now, last_seen_at=now, occurrence_count=1, severity=rule.severity,
                    operator=rule.operator, message=message, evidence=obs.evidence, active_key=key,
                )
            )
            s.flush()  # make the new active alert visible to the next observation in this session
            return "created"
        active.measured_value = obs.value
        active.threshold_value = rule.threshold
        active.last_seen_at = now
        active.occurrence_count += 1
        active.message = message
        active.severity = rule.severity
        merged = list(dict.fromkeys([*obs.evidence.get("trace_ids", []), *((active.evidence or {}).get("trace_ids", []))]))[:10]
        active.evidence = {**obs.evidence, "trace_ids": merged}
        return "updated"

    if active is not None and (now - active.last_seen_at) >= timedelta(seconds=resolve_after):
        active.status = "resolved"
        active.resolved_at = now
        active.resolution = "auto"
        active.active_key = None
        return "resolved"
    return "none"


# Allowed manual transitions
TRANSITIONS = {
    "open": {"acknowledged", "resolved"},
    "acknowledged": {"open", "resolved"},
    "resolved": set(),
}
