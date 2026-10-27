from datetime import timedelta

import httpx

from obs_common.http import SyncDownstreamClient
from obs_common.testing import FailingTransport, INTERNAL_HEADERS, StatusTransport, log_event, metric_event, span_event, tid
from obs_common.timeutil import to_iso, utcnow

OP = {**INTERNAL_HEADERS, "X-User-Role": "operator", "X-User-Name": "olivia"}
VIEWER = {**INTERNAL_HEADERS, "X-User-Role": "viewer", "X-User-Name": "vic"}


def recent(offset_ms=0):
    return utcnow().replace(microsecond=0) - timedelta(seconds=30) + timedelta(milliseconds=offset_ms)


def scenario(seed):
    base = recent()
    seed.spans([
        span_event(1, 1, None, "order-service", "POST /orders", 0, 300, status="ERROR", error="500", kind="SERVER", base=base),
        span_event(1, 2, 1, "order-service", "POST payment", 10, 250, status="ERROR", error="502", kind="CLIENT", peer="payment-service", base=base),
        span_event(1, 3, 2, "payment-service", "charge", 20, 200, status="ERROR", error="declined", kind="SERVER", base=base),
    ])
    seed.logs([log_event("payment-service", "ERROR", "charge declined", trace=1, span=3, base=base)])
    seed.metrics([metric_event("payment-service", 200 + i, error=i % 2 == 0, offset_ms=i * 10, base=base) for i in range(6)])


def test_requires_internal_token(stack):
    assert stack.query.get("/api/v1/query/traces").status_code == 401


def test_trace_detail_is_enriched_with_logs_and_diagnostic(stack, seed):
    scenario(seed)
    stack.trace.post("/api/v1/traces/_finalize", headers={**INTERNAL_HEADERS, "X-User-Role": "admin"})
    stack.rca.post(f"/api/v1/diagnostics/analyze/{tid(1)}", headers=OP)
    r = stack.query.get(f"/api/v1/query/traces/{tid(1)}", headers=OP)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert len(d["spans"]) == 3 and d["logs_available"] and d["logs"][0]["message"] == "charge declined"
    assert d["diagnostic"]["classification"] == "confirmed_failure"
    assert r.json()["warnings"] is None or r.json()["warnings"] == []


def test_viewer_does_not_see_logs(stack, seed):
    scenario(seed)
    r = stack.query.get(f"/api/v1/query/traces/{tid(1)}", headers=VIEWER)
    assert r.json()["data"]["logs"] == []
    assert any(w["code"] == "logs_restricted" for w in r.json()["warnings"])
    assert stack.query.get("/api/v1/query/logs", headers=VIEWER).status_code == 403


def test_log_outage_returns_partial_trace_with_warning(stack, seed):
    scenario(seed)
    s = stack.query_app.state.sources
    s.logs = SyncDownstreamClient("log-service", "http://l", retries=0, internal_token="x", transport=FailingTransport(httpx.ConnectTimeout("t")))
    r = stack.query.get(f"/api/v1/query/traces/{tid(1)}", headers=OP)
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["logs_available"] is False and len(body["data"]["spans"]) == 3
    assert any(w["code"] == "logs_unavailable" and "log-service" in w["message"] for w in body["warnings"])


def test_trace_service_5xx_is_a_downstream_error(stack):
    s = stack.query_app.state.sources
    s.traces = SyncDownstreamClient("trace-service", "http://t", retries=0, internal_token="x", transport=StatusTransport(500))
    r = stack.query.get("/api/v1/query/traces", headers=OP)
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["source"] == "downstream" and err["service"] == "trace-service"


def test_trace_service_timeout_maps_to_504(stack):
    s = stack.query_app.state.sources
    s.traces = SyncDownstreamClient("trace-service", "http://t", retries=0, internal_token="x", transport=FailingTransport(httpx.ReadTimeout("slow")))
    r = stack.query.get(f"/api/v1/query/traces/{tid(1)}", headers=OP)
    assert r.status_code == 504 and r.json()["error"]["code"] == "downstream_timeout"


def test_unknown_trace_is_404_from_downstream(stack):
    r = stack.query.get(f"/api/v1/query/traces/{tid(42)}", headers=OP)
    assert r.status_code == 404 and r.json()["error"]["code"] == "trace_not_found"


def test_dependency_graph_enriched_with_health(stack, seed):
    scenario(seed)
    r = stack.query.get("/api/v1/query/dependencies?window_minutes=10", headers=OP)
    assert r.status_code == 200, r.text
    nodes = {n["service_name"]: n for n in r.json()["data"]["nodes"]}
    assert {"order-service", "payment-service"} <= set(nodes)
    assert nodes["payment-service"]["health"] in ("degraded", "critical")
    edge = r.json()["data"]["edges"][0]
    assert edge["source"] == "order-service" and edge["target"] == "payment-service" and edge["failure_observed"]


def test_dependency_graph_survives_metrics_outage(stack, seed):
    scenario(seed)
    stack.query_app.state.sources.metrics = SyncDownstreamClient("metrics-service", "http://m", retries=0, internal_token="x", transport=FailingTransport(httpx.ConnectError("x")))
    r = stack.query.get("/api/v1/query/dependencies?window_minutes=10", headers=OP)
    assert r.status_code == 200 and any(w["code"] == "metrics_unavailable" for w in r.json()["warnings"])


def test_overview_reports_degraded_sources(stack, seed):
    scenario(seed)
    stack.query_app.state.sources.rca = SyncDownstreamClient("rca-service", "http://r", retries=0, internal_token="x", transport=StatusTransport(500))
    r = stack.query.get("/api/v1/query/overview?window_minutes=10", headers=OP)
    body = r.json()
    assert r.status_code == 200
    assert body["data"]["degraded_sources"] == ["diagnostics"]
    assert body["data"]["failed_traces"][0]["trace_id"] == tid(1)
    assert body["data"]["metrics"]["request_count"] >= 6


def test_filter_validation_and_time_range(stack):
    assert stack.query.get("/api/v1/query/traces?status=BAD", headers=OP).status_code == 422
    r = stack.query.get("/api/v1/query/traces?start=2026-10-02T00:00:00Z&end=2026-10-01T00:00:00Z", headers=OP)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_time_range"
    assert stack.query.get("/api/v1/query/traces?page_size=500", headers=OP).status_code == 422
    assert stack.query.get("/api/v1/query/logs?q=%27%3B%20DROP%20TABLE%20x%3B--", headers=OP).status_code == 200
    assert stack.query.get("/api/v1/query/metrics/timeseries?service_name=Bad%20Name!", headers=OP).status_code == 422


def test_metrics_and_alert_passthrough(stack, seed):
    scenario(seed)
    assert stack.query.get("/api/v1/query/metrics/services?window_minutes=10", headers=OP).json()["data"][0]["service_name"] == "payment-service"
    assert stack.query.get("/api/v1/query/metrics/latency?window_minutes=10", headers=OP).status_code == 200
    assert stack.query.get("/api/v1/query/alerts", headers=OP).status_code == 200
    assert stack.query.get("/api/v1/query/alerts/rules", headers=OP).status_code == 200
