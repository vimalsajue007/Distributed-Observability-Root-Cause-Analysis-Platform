"""Deterministic root-cause rules engine (no AI service involved).

Inputs are plain dictionaries obtained from the trace / log / metrics services. The engine is pure so every
rule can be unit tested. Principles:

* **Confirmed vs potential.**  A failure is *confirmed* only when an ERROR span originates inside the
  service's own telemetry (a leaf error span: an error span with no failing child). When the failure is only
  observed by the caller (timeout / connection error on a CLIENT span and the callee recorded no error) the callee is
  merely a *potential* cause.
* **No guessing.**  If a trace has no spans, or the evidence needed to point at a service is missing, the report
  says "insufficient evidence" and names the gaps instead of claiming a root cause.
* **Evidence-backed.**  Every conclusion lists trace ids, span ids, log ids, timestamps and metric values.
* **Ranking.**  Each service gets a score = sum of configurable rule weights (see ``RulesConfig``); the
  reasons behind every point are returned.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, fields
from typing import Any

RULES_VERSION = "1.0"
LEVELS = ["insufficient", "low", "medium", "high"]


@dataclass
class RulesConfig:
    w_leaf_error: float = 50.0            # confirmed error span inside the service
    w_caller_observed: float = 35.0       # caller saw timeout/unavailable/error calling this service
    w_peer_contributor: float = 20.0      # failing span names this service as the peer it was working with
    w_error_log: float = 10.0             # per ERROR/CRITICAL log correlated by trace id (capped)
    w_error_log_cap: float = 20.0
    w_log_on_origin_span: float = 5.0     # error log attached to the origin span itself
    w_metric_error_rate: float = 15.0
    w_metric_latency: float = 10.0
    w_active_alert: float = 10.0
    w_slow_span: float = 15.0             # per slow span (capped at 2 spans)
    w_latency_share: float = 20.0         # * share of trace self-time
    metric_error_rate_threshold: float = 0.10
    metric_min_requests: int = 5
    metric_p95_threshold_ms: float = 1000.0
    slow_trace_ms: float = 3000.0
    max_log_evidence: int = 12
    max_span_evidence: int = 12

    @classmethod
    def from_overrides(cls, overrides: dict[str, Any] | None) -> "RulesConfig":
        cfg = cls()
        valid = {f.name for f in fields(cls)}
        for key, value in (overrides or {}).items():
            if key in valid and isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(cfg, key, type(getattr(cfg, key))(value))
        return cfg


@dataclass
class Score:
    service: str
    points: float = 0.0
    confirmed: bool = False
    reasons: list[dict[str, Any]] = field(default_factory=list)
    evidence_types: set[str] = field(default_factory=set)


def _level_down(level: str) -> str:
    return LEVELS[max(LEVELS.index(level) - 1, 0)]


def _failure_mode(message: str | None) -> str:
    text = (message or "").lower()
    if "timeout" in text or "timed out" in text or "deadline" in text:
        return "timeout"
    if "connect" in text or "unavailable" in text or "refused" in text or "503" in text or "reset" in text:
        return "unavailable"
    return "error"


def _span_ref(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "span_id": s["span_id"], "service_name": s["service_name"], "operation_name": s["operation_name"], "kind": s.get("kind"),
        "start_time": s["start_time"], "end_time": s["end_time"], "duration_ms": s["duration_ms"], "status": s["status"],
        "error_message": s.get("error_message"), "peer_service": s.get("peer_service"),
    }


def analyze(
    trace: dict[str, Any],
    *,
    logs: list[dict[str, Any]] | None,
    service_metrics: dict[str, dict[str, Any]] | None,
    alerts: list[dict[str, Any]] | None,
    related_traces: list[dict[str, Any]] | None = None,
    cfg: RulesConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or RulesConfig()
    trace_id = trace["trace_id"]
    spans: list[dict[str, Any]] = trace.get("spans", [])
    warnings: list[dict[str, str]] = []
    evidence: list[dict[str, Any]] = []
    unavailable = {"logs": logs is None, "metrics": service_metrics is None, "related_traces": related_traces is None}
    for name, missing in unavailable.items():
        if missing:
            warnings.append({"code": f"{name}_unavailable", "message": f"{name.replace('_', ' ').capitalize()} could not be retrieved; conclusions rely on the remaining evidence only."})
    logs = logs or []
    service_metrics = service_metrics or {}
    alerts = alerts or []
    related_traces = related_traces or []

    base = {
        "rules_version": RULES_VERSION,
        "trace_id": trace_id,
        "related_trace_ids": [],
        "warnings": warnings,
    }
    if not spans:
        return {
            **base, "classification": "insufficient_evidence", "severity": "low", "affected_service": None, "confidence_level": "insufficient",
            "diagnosis": "Telemetry is insufficient: the trace contains no spans, so no cause can be determined.",
            "summary": "No spans available - no root cause claimed.", "ranking": [], "failure_timeline": [], "latency_contributions": [],
            "request_path": [], "service_path": [], "first_failing_span": None, "origin_span": None,
            "evidence": [{"evidence_type": "gap", "reference_id": trace_id, "description": "Trace has no spans.", "observed_at": None, "service_name": None, "data": {}}],
        }

    by_id = {s["span_id"]: s for s in spans}
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in spans:
        if s.get("parent_span_id") in by_id:
            children[s["parent_span_id"]].append(s)
    root = next((s for s in spans if s["depth"] == 0 and not s.get("missing_parent")), None)
    scores: dict[str, Score] = {}

    def score(service: str) -> Score:
        return scores.setdefault(service, Score(service))

    def add(service: str, points: float, rule: str, detail: str, etype: str, *, confirmed: bool = False) -> None:
        sc = score(service)
        sc.points += points
        sc.confirmed = sc.confirmed or confirmed
        sc.evidence_types.add(etype)
        sc.reasons.append({"rule": rule, "points": round(points, 2), "detail": detail})

    gaps = list(trace.get("missing_spans") or [])
    if gaps:
        warnings.append({"code": "missing_spans", "message": f"{len(gaps)} parent span(s) were never received; the trace is incomplete and conclusions are limited accordingly."})
        evidence.append({"evidence_type": "gap", "reference_id": gaps[0]["span_id"], "observed_at": None, "service_name": None,
                         "description": f"Missing span(s): {', '.join(g['span_id'] for g in gaps[:5])} referenced by {sum(len(g['referenced_by']) for g in gaps)} child span(s).", "data": {"missing_spans": gaps}})
    if trace.get("completion_state") == "in_progress":
        warnings.append({"code": "trace_in_progress", "message": "The trace is still within its completion waiting period; later spans may change the diagnosis."})

    # ------------------------------------------------------------ rule 1: error spans -> confirmed / potential
    errors = [s for s in spans if s["is_error"]]
    leaf_errors = [s for s in errors if not any(c["is_error"] for c in children.get(s["span_id"], []))]
    order_key = lambda s: (s["end_time"], -s["depth"])  # noqa: E731
    first_failing = min(errors, key=order_key) if errors else None
    leaf_errors.sort(key=order_key)
    origin = leaf_errors[0] if leaf_errors else None
    confirmed_leaves: list[dict[str, Any]] = []
    for leaf in leaf_errors:
        mode = _failure_mode(leaf.get("error_message"))
        peer = leaf.get("peer_service")
        if leaf.get("kind") == "CLIENT" and peer and peer != leaf["service_name"]:
            callee_spans = children.get(leaf["span_id"], [])
            note = f"callee recorded {len(callee_spans)} span(s), none failing" if callee_spans else "callee recorded no span for this call"
            add(peer, cfg.w_caller_observed, "caller_observed_failure",
                f"{leaf['service_name']} observed a {mode} calling {peer} (span {leaf['span_id']}, {leaf['duration_ms']:.0f} ms); {note}", "span")
        else:
            confirmed_leaves.append(leaf)
            add(leaf["service_name"], cfg.w_leaf_error, "leaf_error_span",
                f"span '{leaf['operation_name']}' ({leaf['span_id']}) ended with ERROR and none of its child spans failed", "span", confirmed=True)
            attr_peer = (leaf.get("attributes") or {}).get("peer.service") or leaf.get("peer_service")
            if attr_peer and attr_peer != leaf["service_name"]:
                add(attr_peer, cfg.w_peer_contributor, "peer_contributor",
                    f"failing span {leaf['span_id']} was working with {attr_peer}", "span")

    # ------------------------------------------------------------ rule 2: correlated logs
    error_logs = [l for l in logs if l.get("severity") in ("ERROR", "CRITICAL")]
    by_service_logs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for l in error_logs:
        by_service_logs[l["service_name"]].append(l)
    for service, items in by_service_logs.items():
        add(service, min(len(items) * cfg.w_error_log, cfg.w_error_log_cap), "error_logs", f"{len(items)} ERROR/CRITICAL log(s) correlated by trace id", "log")
        if origin and any(l.get("span_id") == origin["span_id"] for l in items):
            add(service, cfg.w_log_on_origin_span, "log_on_origin_span", "an error log is attached to the origin span itself", "log")

    # ------------------------------------------------------------ rule 3: metric evidence
    involved = {s["service_name"] for s in spans} | {s.get("peer_service") for s in spans if s.get("peer_service")}
    metric_notes: dict[str, list[str]] = defaultdict(list)
    for service in sorted(x for x in involved if x):
        m = service_metrics.get(service)
        if m and (m.get("request_count") or 0) >= cfg.metric_min_requests:
            rate = m.get("error_rate") or 0.0
            if rate >= cfg.metric_error_rate_threshold:
                add(service, cfg.w_metric_error_rate, "metric_error_rate", f"error rate {rate * 100:.1f}% over {m['request_count']} requests (threshold {cfg.metric_error_rate_threshold * 100:.0f}%)", "metric")
                metric_notes[service].append(f"error rate {rate * 100:.1f}% over {m['request_count']} requests")
            p95 = m.get("p95_latency_ms")
            if p95 is not None and p95 >= cfg.metric_p95_threshold_ms:
                add(service, cfg.w_metric_latency, "metric_latency", f"p95 latency {p95:.0f} ms (threshold {cfg.metric_p95_threshold_ms:.0f} ms)", "metric")
                metric_notes[service].append(f"p95 latency {p95:.0f} ms")
        for a in alerts:
            if a.get("service_name") == service:
                add(service, cfg.w_active_alert, "active_alert", f"active {a.get('alert_type')} alert #{a.get('id')}: {a.get('message')}", "metric")

    # ------------------------------------------------------------ rule 4: latency
    slow = [s for s in spans if s["is_slow"]]
    per_service_slow: dict[str, int] = defaultdict(int)
    for s in slow:
        if per_service_slow[s["service_name"]] < 2:
            add(s["service_name"], cfg.w_slow_span, "slow_span", f"span '{s['operation_name']}' ({s['span_id']}) took {s['duration_ms']:.0f} ms", "span")
        per_service_slow[s["service_name"]] += 1
    contributions = trace.get("services", [])
    for c in contributions:
        if c["latency_share"] > 0 and (slow or trace.get("duration_ms", 0) >= cfg.slow_trace_ms):
            add(c["service_name"], c["latency_share"] * cfg.w_latency_share, "latency_contribution", f"{c['latency_share'] * 100:.0f}% of the trace's self-time ({c['self_time_ms']:.0f} ms)", "span")

    ranking = sorted(scores.values(), key=lambda x: (-x.points, x.service))
    ranking_out = [
        {"service_name": r.service, "score": round(r.points, 2), "classification": "confirmed" if r.confirmed else "potential",
         "evidence_types": sorted(r.evidence_types), "reasons": r.reasons}
        for r in ranking if r.points > 0
    ]

    # ------------------------------------------------------------ classification
    root_failed = bool(root and root["is_error"])
    trace_slow = bool(slow) or trace.get("duration_ms", 0) >= cfg.slow_trace_ms
    if errors:
        if confirmed_leaves:
            primary = confirmed_leaves[0]["service_name"]
            classification = "confirmed_failure"
        else:
            primary = ranking[0].service if ranking else None
            classification = "potential_cause" if primary else "insufficient_evidence"
        severity = ("critical" if root_failed else "high") if classification == "confirmed_failure" else ("high" if root_failed else "medium")
    elif trace_slow:
        classification = "latency_bottleneck"
        if slow:
            primary = max(slow, key=lambda s: s["self_time_ms"])["service_name"]
        else:
            primary = contributions[0]["service_name"] if contributions else None
        severity = "medium"
    elif gaps and not root:
        classification, primary, severity = "insufficient_evidence", None, "low"
    else:
        classification, primary, severity = "healthy", None, "info"

    # ------------------------------------------------------------ confidence
    confidence = "insufficient"
    if classification != "insufficient_evidence" and classification != "healthy":
        sc = scores.get(primary) if primary else None
        corroboration = 0
        if sc:
            corroboration += 1 if "log" in sc.evidence_types else 0
            corroboration += 1 if "metric" in sc.evidence_types else 0
        recurring = [t for t in related_traces if t.get("trace_id") != trace_id]
        if recurring:
            corroboration += 1
        if classification == "confirmed_failure":
            confidence = "high" if corroboration >= 1 else "medium"
        elif classification == "potential_cause":
            confidence = "medium" if corroboration >= 2 else "low"
        else:
            confidence = "medium"
        if gaps:
            confidence = _level_down(confidence)
        if unavailable["logs"] and unavailable["metrics"]:
            confidence = min(confidence, "medium" if classification == "confirmed_failure" else "low", key=LEVELS.index)
    elif classification == "healthy":
        confidence = "high" if not gaps else "medium"

    # ------------------------------------------------------------ paths
    chain: list[dict[str, Any]] = []
    target = origin or first_failing
    cursor = target
    guard = 0
    while cursor is not None and guard < 100:
        chain.append({"span_id": cursor["span_id"], "service_name": cursor["service_name"], "operation_name": cursor["operation_name"], "status": cursor["status"], "duration_ms": cursor["duration_ms"]})
        cursor = by_id.get(cursor.get("parent_span_id"))
        guard += 1
    request_path = list(reversed(chain))
    service_path: list[str] = []
    for s in sorted(spans, key=lambda x: (x["start_time"], x["order"] if "order" in x else 0)):
        if not service_path or service_path[-1] != s["service_name"]:
            if s["service_name"] not in service_path:
                service_path.append(s["service_name"])

    # ------------------------------------------------------------ evidence + timeline
    evidence.insert(0, {"evidence_type": "trace", "reference_id": trace_id, "observed_at": trace.get("start_time"), "service_name": trace.get("root_service"),
                        "description": f"Trace {trace_id}: status {trace.get('status')}, {trace.get('duration_ms', 0):.0f} ms, {len(spans)} spans, completion '{trace.get('completion_state')}'.",
                        "data": {"correlation_id": trace.get("correlation_id")}})
    shown = 0
    for s in sorted(errors, key=order_key):
        if shown >= cfg.max_span_evidence:
            break
        tag = "origin" if origin and s["span_id"] == origin["span_id"] else ("first failing" if first_failing and s["span_id"] == first_failing["span_id"] else "failed")
        evidence.append({"evidence_type": "span", "reference_id": s["span_id"], "observed_at": s["end_time"], "service_name": s["service_name"],
                         "description": f"{tag} span '{s['operation_name']}' [{s.get('kind')}] ended with ERROR: {s.get('error_message') or 'no message'} ({s['duration_ms']:.0f} ms).", "data": _span_ref(s)})
        shown += 1
    for s in slow[: cfg.max_span_evidence - shown if cfg.max_span_evidence > shown else 0]:
        if not s["is_error"]:
            evidence.append({"evidence_type": "span", "reference_id": s["span_id"], "observed_at": s["end_time"], "service_name": s["service_name"],
                             "description": f"slow span '{s['operation_name']}' took {s['duration_ms']:.0f} ms (self time {s['self_time_ms']:.0f} ms).", "data": _span_ref(s)})
    prioritized_logs = sorted(logs, key=lambda l: (0 if l.get("severity") in ("ERROR", "CRITICAL") else 1 if l.get("severity") == "WARNING" else 2, l.get("timestamp") or ""))
    for l in prioritized_logs[: cfg.max_log_evidence]:
        if l.get("severity") == "INFO" and errors:
            continue
        evidence.append({"evidence_type": "log", "reference_id": str(l["id"]), "observed_at": l.get("timestamp"), "service_name": l["service_name"],
                         "description": f"[{l['severity']}] {l['service_name']}: {l['message'][:300]}", "data": {"span_id": l.get("span_id"), "trace_id": l.get("trace_id")}})
    for service in sorted(involved):
        m = service_metrics.get(service) if service else None
        if m and m.get("request_count"):
            evidence.append({"evidence_type": "metric", "reference_id": f"{service}:request.duration_ms", "observed_at": None, "service_name": service,
                             "description": f"{service}: {m['request_count']} requests, error rate {((m.get('error_rate') or 0) * 100):.1f}%, avg {m.get('avg_latency_ms')} ms, p95 {m.get('p95_latency_ms')} ms (nearest-rank).",
                             "data": {k: m.get(k) for k in ("request_count", "error_count", "error_rate", "avg_latency_ms", "p95_latency_ms", "p99_latency_ms", "throughput_rps")}})
    for a in alerts:
        if a.get("service_name") in involved:
            evidence.append({"evidence_type": "alert", "reference_id": f"alert:{a.get('id')}", "observed_at": a.get("last_seen_at"), "service_name": a.get("service_name"),
                             "description": f"Active alert #{a.get('id')} ({a.get('alert_type')}): {a.get('message')}", "data": {"measured_value": a.get("measured_value"), "threshold_value": a.get("threshold_value")}})
    related_ids = [t["trace_id"] for t in related_traces if t.get("trace_id") != trace_id][:5]
    for t in related_traces[:5]:
        if t.get("trace_id") != trace_id:
            evidence.append({"evidence_type": "related_trace", "reference_id": t["trace_id"], "observed_at": t.get("start_time"), "service_name": t.get("root_service"),
                             "description": f"Related failing trace {t['trace_id']} ({t.get('duration_ms', 0):.0f} ms) involving {primary or 'the same service'}.", "data": {}})
    if primary and scores.get(primary):
        for r in scores[primary].reasons:
            evidence.append({"evidence_type": "rule", "reference_id": r["rule"], "observed_at": None, "service_name": primary, "description": f"Rule '{r['rule']}' (+{r['points']}): {r['detail']}", "data": {"points": r["points"]}})

    timeline: list[dict[str, Any]] = [{"timestamp": trace.get("start_time"), "type": "trace_start", "service_name": trace.get("root_service"), "message": "Request entered the system", "span_id": root["span_id"] if root else None}]
    for s in errors:
        timeline.append({"timestamp": s["end_time"], "type": "span_error", "service_name": s["service_name"], "span_id": s["span_id"],
                         "message": f"'{s['operation_name']}' failed: {s.get('error_message') or 'error'}" + (" (origin)" if origin and s["span_id"] == origin["span_id"] else "")})
    for s in slow:
        timeline.append({"timestamp": s["end_time"], "type": "slow_span", "service_name": s["service_name"], "span_id": s["span_id"], "message": f"'{s['operation_name']}' was slow ({s['duration_ms']:.0f} ms)"})
    for l in error_logs[:10]:
        timeline.append({"timestamp": l.get("timestamp"), "type": "log", "service_name": l["service_name"], "span_id": l.get("span_id"), "log_id": l["id"], "message": f"[{l['severity']}] {l['message'][:200]}"})
    for g in gaps:
        timeline.append({"timestamp": None, "type": "missing_span", "service_name": None, "span_id": g["span_id"], "message": f"Span {g['span_id']} was never received"})
    timeline.append({"timestamp": trace.get("end_time"), "type": "trace_end", "service_name": trace.get("root_service"), "message": f"Request finished with status {trace.get('status')}", "span_id": None})
    timeline.sort(key=lambda e: (e["timestamp"] is None, e["timestamp"] or ""))

    # ------------------------------------------------------------ narrative
    path_text = " → ".join(service_path)
    if classification == "confirmed_failure" and primary:
        leaf = confirmed_leaves[0]
        propagated = [s["service_name"] for s in request_path[:-1] if s["status"] == "ERROR"]
        diagnosis = (f"Confirmed failure in {primary}: span '{leaf['operation_name']}' ended with ERROR ({leaf.get('error_message') or 'no message'}) "
                     f"at {leaf['end_time']}. It is the first failing span with no failing child, so the error originated in {primary}"
                     + (f" and propagated to {', '.join(dict.fromkeys(propagated))}." if propagated else ".") + f" Request path: {path_text}.")
        if len(confirmed_leaves) > 1:
            diagnosis += f" {len(confirmed_leaves) - 1} additional independent failing span(s) were observed."
    elif classification == "potential_cause" and primary:
        leaf = origin or first_failing
        mode = _failure_mode(leaf.get("error_message")) if leaf else "error"
        diagnosis = (f"Potential cause (not confirmed): {leaf['service_name'] if leaf else 'a caller'} observed a {mode} while calling {primary} "
                     f"(span '{leaf['operation_name'] if leaf else '?'}', {leaf['duration_ms']:.0f} ms). {primary} recorded no failing span of its own, so a root cause is not claimed; "
                     f"it is ranked first because of: {', '.join(dict.fromkeys(r['rule'] for r in scores[primary].reasons))}. Request path: {path_text}.")
    elif classification == "latency_bottleneck" and primary:
        top = next((c for c in contributions if c["service_name"] == primary), None)
        diagnosis = (f"Latency bottleneck: {primary} accounts for {(top['latency_share'] * 100 if top else 0):.0f}% of the trace's self-time"
                     f" ({len(slow)} slow span(s)). No span failed. Request path: {path_text}.")
    elif classification == "healthy":
        diagnosis = f"No failed or slow spans were found in this trace. Request path: {path_text}."
    else:
        diagnosis = "Telemetry is insufficient to determine a cause" + (f": {len(gaps)} span(s) are missing and no root span was received." if gaps else ".")
    if gaps and classification not in ("insufficient_evidence", "healthy"):
        diagnosis += f" Note: {len(gaps)} span(s) were missing, which limits certainty."

    return {
        **base,
        "classification": classification,
        "severity": severity,
        "affected_service": primary,
        "confidence_level": confidence,
        "diagnosis": diagnosis,
        "summary": diagnosis.split(". ")[0][:480],
        "ranking": ranking_out,
        "failure_timeline": timeline,
        "latency_contributions": [{"service_name": c["service_name"], "self_time_ms": c["self_time_ms"], "total_time_ms": c["total_time_ms"], "latency_share": c["latency_share"], "error_count": c["error_count"]} for c in contributions],
        "request_path": request_path,
        "service_path": service_path,
        "first_failing_span": _span_ref(first_failing) if first_failing else None,
        "origin_span": _span_ref(origin) if origin else None,
        "related_trace_ids": related_ids,
        "metric_notes": metric_notes,
        "evidence": evidence,
    }
