"""Pure rules-engine tests on hand-built reconstructed traces."""
from obs_common.timeutil import to_iso
from obs_common.testing import BASE_TIME, sid, tid
from datetime import timedelta

from rca_service.rules import RulesConfig, analyze


def span(n, parent, service, op, off, dur, *, kind="INTERNAL", status="OK", err=None, peer=None, depth=0, slow=False, self_ms=None):
    start = BASE_TIME + timedelta(milliseconds=off)
    return {
        "span_id": sid(n), "parent_span_id": sid(parent) if parent else None, "service_name": service, "operation_name": op, "kind": kind,
        "start_time": to_iso(start), "end_time": to_iso(start + timedelta(milliseconds=dur)), "duration_ms": dur, "status": status,
        "error_message": err, "peer_service": peer, "attributes": {"peer.service": peer} if peer else {}, "depth": depth, "order": n,
        "self_time_ms": dur if self_ms is None else self_ms, "is_error": status == "ERROR", "is_slow": slow, "missing_parent": False,
    }


def trace(spans, **kw):
    base = {
        "trace_id": tid(1), "root_service": spans[0]["service_name"] if spans else None, "correlation_id": None,
        "start_time": to_iso(BASE_TIME), "end_time": to_iso(BASE_TIME + timedelta(seconds=1)), "duration_ms": 1000.0,
        "status": "ERROR" if any(s["is_error"] for s in spans) else "OK", "completion_state": "complete", "spans": spans,
        "missing_spans": [], "services": [],
    }
    base.update(kw)
    return base


def test_leaf_error_is_confirmed():
    t = trace([
        span(1, None, "api-gateway", "POST /orders", 0, 300, status="ERROR", err="500"),
        span(2, 1, "order-service", "create_order", 10, 280, status="ERROR", err="payment failed", depth=1),
        span(3, 2, "payment-service", "charge", 20, 100, status="ERROR", err="card declined", depth=2),
    ])
    r = analyze(t, logs=[], service_metrics={}, alerts=[])
    assert r["classification"] == "confirmed_failure"
    assert r["affected_service"] == "payment-service"
    assert r["origin_span"]["span_id"] == sid(3)
    assert r["severity"] == "critical"
    assert r["confidence_level"] == "medium"  # no log/metric corroboration
    assert [p["service_name"] for p in r["request_path"]] == ["api-gateway", "order-service", "payment-service"]
    assert any(e["evidence_type"] == "span" and e["reference_id"] == sid(3) for e in r["evidence"])


def test_corroboration_raises_confidence():
    t = trace([span(1, None, "order-service", "o", 0, 100, status="ERROR", err="x"), span(2, 1, "payment-service", "charge", 5, 50, status="ERROR", err="boom", depth=1)])
    logs = [{"id": 1, "service_name": "payment-service", "severity": "ERROR", "message": "charge failed", "span_id": sid(2), "trace_id": tid(1), "timestamp": to_iso(BASE_TIME)}]
    r = analyze(t, logs=logs, service_metrics={"payment-service": {"request_count": 10, "error_rate": 0.5}}, alerts=[])
    assert r["confidence_level"] == "high"
    assert any(e["evidence_type"] == "log" for e in r["evidence"])
    assert any(e["evidence_type"] == "metric" for e in r["evidence"])


def test_caller_observed_timeout_is_only_potential_cause():
    t = trace([
        span(1, None, "order-service", "create", 0, 3100, status="ERROR", err="notification timeout"),
        span(2, 1, "order-service", "POST notify", 10, 3000, kind="CLIENT", status="ERROR", err="ReadTimeout", peer="notification-service", depth=1),
    ])
    r = analyze(t, logs=[], service_metrics={}, alerts=[])
    assert r["classification"] == "potential_cause"
    assert r["affected_service"] == "notification-service"
    assert "not confirmed" in r["diagnosis"]
    assert r["ranking"][0]["classification"] == "potential"
    assert r["confidence_level"] == "low"


def test_empty_trace_is_insufficient_evidence():
    r = analyze(trace([]), logs=None, service_metrics=None, alerts=None)
    assert r["classification"] == "insufficient_evidence"
    assert r["affected_service"] is None
    assert r["confidence_level"] == "insufficient"


def test_missing_parent_lowers_confidence_and_warns():
    t = trace([span(2, 1, "payment-service", "charge", 5, 50, status="ERROR", err="boom", depth=1)],
              missing_spans=[{"span_id": sid(1), "referenced_by": [sid(2)]}])
    r = analyze(t, logs=[], service_metrics={}, alerts=[])
    assert r["classification"] == "confirmed_failure"
    assert r["confidence_level"] == "low"
    assert any(w["code"] == "missing_spans" for w in r["warnings"])
    assert any(e["evidence_type"] == "gap" for e in r["evidence"])


def test_unavailable_sources_produce_warnings_not_errors():
    t = trace([span(1, None, "svc", "op", 0, 10, status="ERROR", err="x")])
    r = analyze(t, logs=None, service_metrics=None, alerts=[])
    codes = {w["code"] for w in r["warnings"]}
    assert {"logs_unavailable", "metrics_unavailable"} <= codes
    assert r["classification"] == "confirmed_failure"


def test_latency_bottleneck_without_errors():
    t = trace([
        span(1, None, "order-service", "create", 0, 2000, self_ms=100),
        span(2, 1, "payment-service", "charge", 10, 1900, depth=1, slow=True, self_ms=1900),
    ], services=[{"service_name": "payment-service", "self_time_ms": 1900, "total_time_ms": 1900, "latency_share": 0.95, "error_count": 0},
                 {"service_name": "order-service", "self_time_ms": 100, "total_time_ms": 2000, "latency_share": 0.05, "error_count": 0}])
    r = analyze(t, logs=[], service_metrics={}, alerts=[])
    assert r["classification"] == "latency_bottleneck"
    assert r["affected_service"] == "payment-service"
    assert r["latency_contributions"][0]["latency_share"] == 0.95


def test_healthy_trace():
    r = analyze(trace([span(1, None, "svc", "op", 0, 20)]), logs=[], service_metrics={}, alerts=[])
    assert r["classification"] == "healthy"
    assert r["affected_service"] is None


def test_rule_overrides_change_ranking():
    t = trace([span(1, None, "a", "o", 0, 10, status="ERROR", err="x")])
    cfg = RulesConfig.from_overrides({"w_leaf_error": 5, "bogus": 1, "w_error_log": "no"})
    assert cfg.w_leaf_error == 5.0
    r = analyze(t, logs=[], service_metrics={}, alerts=[], cfg=cfg)
    assert r["ranking"][0]["score"] == 5.0
