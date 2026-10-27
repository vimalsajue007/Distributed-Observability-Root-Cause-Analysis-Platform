import httpx
import pytest

from obs_common.http import SyncDownstreamClient
from obs_common.testing import FailingTransport, INTERNAL_HEADERS, log_event, metric_event, span_event, tid

H = {**INTERNAL_HEADERS, "X-User-Role": "operator", "X-User-Name": "tester"}


def failing_scenario(seed):
    seed.spans([
        span_event(1, 1, None, "order-service", "POST /orders", 0, 400, status="ERROR", error="500", kind="SERVER"),
        span_event(1, 2, 1, "order-service", "POST payment", 10, 350, status="ERROR", error="502", kind="CLIENT", peer="payment-service"),
        span_event(1, 3, 2, "payment-service", "charge", 20, 300, status="ERROR", error="card processor failed", kind="SERVER"),
    ])
    seed.logs([log_event("payment-service", "ERROR", "charge failed for order", trace=1, span=3, offset_ms=320)])
    seed.metrics([metric_event("payment-service", 300 + i, error=i % 2 == 0, offset_ms=i * 100) for i in range(10)])


def test_analyze_confirmed_failure_end_to_end(stack, seed):
    failing_scenario(seed)
    r = stack.rca.post(f"/api/v1/diagnostics/analyze/{tid(1)}", headers=H)
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["classification"] == "confirmed_failure" and d["affected_service"] == "payment-service"
    assert d["confidence_level"] == "high"
    types = {e["evidence_type"] for e in d["evidence"]}
    assert {"trace", "span", "log", "metric", "rule"} <= types
    assert d["origin_span"]["service_name"] == "payment-service"


def test_reanalysis_is_idempotent(stack, seed):
    failing_scenario(seed)
    stack.rca.post(f"/api/v1/diagnostics/analyze/{tid(1)}", headers=H)
    r = stack.rca.post(f"/api/v1/diagnostics/analyze/{tid(1)}", headers=H)
    assert r.status_code == 200 and r.json()["data"]["analysis_count"] == 2
    lst = stack.rca.get("/api/v1/diagnostics", headers=INTERNAL_HEADERS).json()
    assert lst["meta"]["total"] == 1
    got = stack.rca.get(f"/api/v1/diagnostics/{tid(1)}", headers=INTERNAL_HEADERS).json()["data"]
    assert len(got["evidence"]) == len({e["id"] for e in got["evidence"]})


def test_filters_and_get_by_id(stack, seed):
    failing_scenario(seed)
    rid = stack.rca.post(f"/api/v1/diagnostics/analyze/{tid(1)}", headers=H).json()["data"]["id"]
    assert stack.rca.get(f"/api/v1/diagnostics/{rid}", headers=INTERNAL_HEADERS).status_code == 200
    q = stack.rca.get("/api/v1/diagnostics?affected_service=payment-service&classification=confirmed_failure", headers=INTERNAL_HEADERS).json()
    assert q["meta"]["total"] == 1
    assert stack.rca.get("/api/v1/diagnostics?affected_service=order-service", headers=INTERNAL_HEADERS).json()["meta"]["total"] == 0
    assert stack.rca.get("/api/v1/diagnostics/999999", headers=INTERNAL_HEADERS).status_code == 404
    assert stack.rca.get("/api/v1/diagnostics?severity=bogus", headers=INTERNAL_HEADERS).status_code == 422
    assert stack.rca.get("/api/v1/diagnostics?start=2026-10-02T00:00:00Z&end=2026-10-01T00:00:00Z", headers=INTERNAL_HEADERS).json()["error"]["code"] == "invalid_time_range"


def test_unknown_trace_404_and_requires_auth_and_role(stack):
    assert stack.rca.post(f"/api/v1/diagnostics/analyze/{tid(99)}", headers=H).status_code == 404
    assert stack.rca.post(f"/api/v1/diagnostics/analyze/{tid(1)}").status_code == 401
    assert stack.rca.post(f"/api/v1/diagnostics/analyze/{tid(1)}", headers={**INTERNAL_HEADERS, "X-User-Role": "viewer"}).status_code == 403
    assert stack.rca.post("/api/v1/diagnostics/analyze/not-a-trace", headers=H).status_code == 422


def test_log_service_down_gives_partial_report_with_warning(stack, seed):
    failing_scenario(seed)
    svc = stack.rca.app.state.service
    svc.logs = SyncDownstreamClient("log-service", "http://log", retries=0, internal_token="x", transport=FailingTransport(httpx.ConnectError("down")))
    d = stack.rca.post(f"/api/v1/diagnostics/analyze/{tid(1)}", headers=H).json()
    assert d["data"]["classification"] == "confirmed_failure"
    assert any(w["code"] == "logs_unavailable" for w in d["warnings"])


def test_trace_service_down_returns_503(stack):
    svc = stack.rca.app.state.service
    svc.traces = SyncDownstreamClient("trace-service", "http://trace", retries=0, internal_token="x", transport=FailingTransport(httpx.ConnectError("down")))
    r = stack.rca.post(f"/api/v1/diagnostics/analyze/{tid(1)}", headers=H)
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["source"] == "downstream" and err["service"] == "trace-service"


def test_trace_completed_event_triggers_analysis_once(stack, seed, broker):
    failing_scenario(seed)
    event = {"event_id": "trace-completed:x:1", "event_type": "trace_completed", "trace_id": tid(1), "status": "ERROR"}
    broker.publish("events.trace.completed", event, message_id=event["event_id"])
    broker.drain()
    assert stack.rca.get("/api/v1/diagnostics", headers=INTERNAL_HEADERS).json()["meta"]["total"] == 1
    broker.publish("events.trace.completed", event, message_id=event["event_id"])
    broker.drain()
    d = stack.rca.get(f"/api/v1/diagnostics/{tid(1)}", headers=INTERNAL_HEADERS).json()["data"]
    assert d["analysis_count"] == 1 and d["trigger"] == "event"


def test_malformed_event_goes_to_dlq(stack, broker):
    broker.publish("events.trace.completed", {"event_type": "trace_completed", "trace_id": "bad"}, message_id="m1")
    broker.drain()
    assert len(broker.dead_letters["rca.trace-completed"]) == 1


def test_healthy_trace_reported_as_healthy(stack, seed):
    seed.spans([span_event(2, 1, None, "order-service", "GET /orders", 0, 30, kind="SERVER")])
    d = stack.rca.post(f"/api/v1/diagnostics/analyze/{tid(2)}", headers=H).json()["data"]
    assert d["classification"] == "healthy" and d["affected_service"] is None
