"""Processor + API tests: idempotency, out-of-order, completion policy, queries, dependency graph."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from obs_common.broker import NonRetryableError, TRACE_SPEC
from obs_common.testing import BASE_TIME, INTERNAL_HEADERS, tid, span_event
from obs_common.timeutil import utcnow

from trace_service.models import Trace, TraceSpan

H = INTERNAL_HEADERS


def payment_failure_trace(n=1, base=BASE_TIME):
    return [
        span_event(n, 1, None, "api-gateway", "POST /api/v1/sample/orders", 0, 2100, kind="SERVER", status="ERROR", error="HTTP 504", correlation_id=f"corr-{n}", base=base),
        span_event(n, 2, 1, "api-gateway", "POST", 5, 2080, kind="CLIENT", peer="order-service", status="ERROR", error="HTTP 504", base=base),
        span_event(n, 3, 2, "order-service", "POST /api/v1/orders", 15, 2060, kind="SERVER", status="ERROR", error="HTTP 504", base=base),
        span_event(n, 4, 3, "order-service", "POST", 30, 2000, kind="CLIENT", peer="payment-service", status="ERROR", error="ReadTimeout: timed out", base=base),
        span_event(n, 5, 4, "payment-service", "POST /api/v1/payments", 40, 4000, kind="SERVER", base=base),
    ]


def feed(processor, envelope, events):
    for e in events:
        processor.handle_event(envelope(e))


def test_spans_are_persisted_and_trace_summary_built(client, processor, envelope, db):
    feed(processor, envelope, payment_failure_trace())
    r = client.get(f"/api/v1/traces/{tid(1)}", headers=H)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["span_count"] == 5 and d["root_service"] == "api-gateway"
    assert d["status"] == "ERROR" and d["correlation_id"] == "corr-1"
    assert [s["depth"] for s in d["spans"]] == [0, 1, 2, 3, 4]
    assert d["spans"][3]["peer_service"] == "payment-service"
    assert d["spans"][3]["is_error"] and d["spans"][3]["error_message"].startswith("ReadTimeout")
    assert d["spans"][4]["is_slow"]  # 4000ms >= 1000ms threshold
    assert {s["service_name"] for s in d["services"]} == {"api-gateway", "order-service", "payment-service"}


def test_duplicate_delivery_is_idempotent(processor, envelope, db):
    events = payment_failure_trace()
    feed(processor, envelope, events)
    feed(processor, envelope, events)           # whole trace delivered twice
    assert processor.handle_event(envelope(events[0])) == "duplicate"
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(TraceSpan)) == 5
        assert s.scalar(select(func.count()).select_from(Trace)) == 1


def test_out_of_order_arrival_reconstructs_the_same_trace(client, processor, envelope):
    events = payment_failure_trace()
    feed(processor, envelope, list(reversed(events)))    # children first, root last
    d = client.get(f"/api/v1/traces/{tid(1)}", headers=H).json()["data"]
    assert [s["span_id"] for s in d["spans"]] == [e["span_id"] for e in events]
    assert d["missing_spans"] == [] and d["has_root_span"]


def test_missing_span_is_reported_and_trace_marked_incomplete(client, processor, envelope):
    events = payment_failure_trace()
    del events[2]                                          # the order-service server span never arrives
    feed(processor, envelope, events)
    resp = client.get(f"/api/v1/traces/{tid(1)}", headers=H)
    d = resp.json()["data"]
    assert d["missing_spans"][0]["referenced_by"] == [events[2]["span_id"]]
    assert any(w["code"] == "missing_spans" for w in resp.json()["warnings"])
    processor.finalize_due(utcnow() + timedelta(seconds=60))
    assert client.get(f"/api/v1/traces/{tid(1)}", headers=H).json()["data"]["completion_state"] == "incomplete"


def test_completion_policy_waits_then_completes_and_announces_failed_trace(client, processor, envelope, broker):
    feed(processor, envelope, payment_failure_trace())
    assert processor.finalize_due(utcnow()) == []                       # still inside the waiting period
    assert client.get(f"/api/v1/traces/{tid(1)}", headers=H).json()["data"]["completion_state"] == "in_progress"
    done = processor.finalize_due(utcnow() + timedelta(seconds=30))
    assert done == [tid(1)]
    d = client.get(f"/api/v1/traces/{tid(1)}", headers=H).json()["data"]
    assert d["completion_state"] == "complete" and d["completed_at"]
    events = [m for k, m in broker.published if k == "events.trace.completed"]
    assert len(events) == 1 and events[0]["trace_id"] == tid(1) and events[0]["status"] == "ERROR"


def test_healthy_fast_trace_is_not_announced_to_rca(processor, envelope, broker):
    feed(processor, envelope, [span_event(2, 1, None, "svc", "GET /", 0, 20, kind="SERVER")])
    processor.finalize_due(utcnow() + timedelta(seconds=60))
    assert [k for k, _ in broker.published if k == "events.trace.completed"] == []


def test_late_span_reopens_a_completed_trace(client, processor, envelope):
    events = payment_failure_trace()
    feed(processor, envelope, [events[0], events[1], events[3], events[4]])       # order-service server span is late
    processor.finalize_due(utcnow() + timedelta(seconds=60))
    d = client.get(f"/api/v1/traces/{tid(1)}", headers=H).json()["data"]
    assert d["completion_state"] == "incomplete" and d["missing_parent_count"] == 1
    feed(processor, envelope, [events[2]])                                         # straggler arrives
    d = client.get(f"/api/v1/traces/{tid(1)}", headers=H).json()["data"]
    assert d["completion_state"] == "in_progress" and d["completed_at"] is None and d["span_count"] == 5
    assert d["missing_spans"] == [] and d["missing_parent_count"] == 0
    processor.finalize_due(utcnow() + timedelta(seconds=60))
    assert client.get(f"/api/v1/traces/{tid(1)}", headers=H).json()["data"]["completion_state"] == "complete"


def test_original_timestamps_are_preserved(client, processor, envelope):
    feed(processor, envelope, payment_failure_trace())
    spans = client.get(f"/api/v1/traces/{tid(1)}/spans", headers=H).json()["data"]
    assert spans[0]["start_time"] == "2026-10-16T10:00:00.000000Z"
    assert spans[4]["end_time"] == "2026-10-16T10:00:04.040000Z"


def test_poison_message_is_non_retryable(processor):
    with pytest.raises(NonRetryableError):
        processor.handle_event({"schema_version": 1, "event_id": "x", "event_type": "trace", "service_name": "a", "received_at": "now", "payload": {"foo": 1}})
    with pytest.raises(NonRetryableError):
        processor.handle_event({"not": "an envelope"})


def test_broker_consumption_end_to_end_with_retry_and_dead_letter(app, broker, processor, envelope, monkeypatch, db):
    # processor is registered as the consumer by the app lifespan (TestClient context)
    from fastapi.testclient import TestClient

    with TestClient(app):
        good = envelope(span_event(3, 1, None, "svc", "GET /", 0, 5, kind="SERVER"))
        broker.publish("telemetry.traces", good)
        broker.publish("telemetry.traces", {"garbage": True})            # poison => dead letter immediately
        calls = {"n": 0}
        original = processor.store_span

        def flaky(span, event_id):
            calls["n"] += 1
            if span.trace_id == tid(4) and calls["n"] < 3:
                raise RuntimeError("database connection lost")           # transient => retried
            return original(span, event_id)

        monkeypatch.setattr(processor, "store_span", flaky)
        broker.publish("telemetry.traces", envelope(span_event(4, 1, None, "svc", "GET /", 0, 5, kind="SERVER")))
        broker.drain()
    with db.session() as s:
        assert {t for (t,) in s.execute(select(Trace.trace_id))} == {tid(3), tid(4)}
    assert len(broker.dead_letters[TRACE_SPEC.queue]) == 1
    assert "non-retryable" in broker.dead_letters[TRACE_SPEC.queue][0]["reason"]


def test_trace_listing_filters_and_pagination(client, processor, envelope):
    for n in range(1, 6):
        feed(processor, envelope, [span_event(n, 1, None, "svc-a" if n % 2 else "svc-b", "GET /", n * 1000, 10 * n, kind="SERVER", status="ERROR" if n == 5 else "OK", correlation_id=f"c-{n}")])
    page1 = client.get("/api/v1/traces?page_size=2", headers=H).json()
    assert page1["meta"]["total"] == 5 and page1["meta"]["total_pages"] == 3 and len(page1["data"]) == 2
    assert page1["data"][0]["trace_id"] == tid(5)                          # newest first
    assert [t["trace_id"] for t in client.get("/api/v1/traces?status=ERROR", headers=H).json()["data"]] == [tid(5)]
    assert len(client.get("/api/v1/traces?service_name=svc-b", headers=H).json()["data"]) == 2
    assert client.get("/api/v1/traces?correlation_id=c-3", headers=H).json()["data"][0]["trace_id"] == tid(3)
    feed(processor, envelope, [span_event("feedface" + "0" * 24, 1, None, "svc-a", "GET /", 9000, 5, kind="SERVER")])
    assert client.get("/api/v1/traces?trace_id=feedface", headers=H).json()["meta"]["total"] == 1   # prefix search
    assert client.get("/api/v1/traces?min_duration_ms=35", headers=H).json()["meta"]["total"] == 2  # traces 4 and 5


def test_api_validation_and_error_codes(client):
    assert client.get("/api/v1/traces").status_code == 401                                   # no token
    assert client.get("/api/v1/traces/not-a-trace-id", headers=H).status_code == 422          # invalid id
    r = client.get(f"/api/v1/traces/{tid(99)}", headers=H)                                   # unknown id
    assert r.status_code == 404 and r.json()["error"]["code"] == "trace_not_found"
    assert client.get(f"/api/v1/traces/{tid(99)}/spans", headers=H).status_code == 404
    bad = client.get("/api/v1/traces?start=2026-10-16T12:00:00Z&end=2026-10-16T11:00:00Z", headers=H)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_time_range"
    assert client.get("/api/v1/traces?page=0", headers=H).status_code == 422
    assert client.get("/api/v1/traces?page_size=500", headers=H).status_code == 422
    assert client.get("/api/v1/traces?status=BROKEN", headers=H).status_code == 422
    assert "Traceback" not in client.get("/api/v1/traces?page=0", headers=H).text


def test_dependency_graph_is_derived_from_spans(client, processor, envelope):
    base = utcnow() - timedelta(minutes=5)
    for n in (1, 2):
        feed(processor, envelope, payment_failure_trace(n, base=base))
    g = client.get("/api/v1/dependencies?window_minutes=60", headers=H).json()["data"]
    edges = {(e["source"], e["target"]): e for e in g["edges"]}
    assert ("api-gateway", "order-service") in edges and ("order-service", "payment-service") in edges
    pay = edges[("order-service", "payment-service")]
    assert pay["request_count"] == 2 and pay["error_count"] == 2 and pay["failure_observed"] and pay["error_rate"] == 1.0
    nodes = {n["service_name"]: n for n in g["nodes"]}
    assert nodes["payment-service"]["request_count"] == 2 and not nodes["payment-service"]["observed_as_peer_only"]
    detail = client.get("/api/v1/dependencies/order-service?window_minutes=60", headers=H).json()["data"]
    assert [e["source"] for e in detail["incoming"]] == ["api-gateway"] and [e["target"] for e in detail["outgoing"]] == ["payment-service"]
    assert client.get("/api/v1/dependencies/ghost-service?window_minutes=60", headers=H).status_code == 404
    # nothing observed outside the window
    assert client.get("/api/v1/dependencies?start=2020-01-01T00:00:00Z&end=2020-01-02T00:00:00Z", headers=H).json()["data"]["edges"] == []


def test_unanswered_call_creates_edge_to_peer_without_spans(client, processor, envelope):
    base = utcnow() - timedelta(minutes=2)
    ev = [
        span_event(7, 1, None, "order-service", "POST /orders", 0, 100, kind="SERVER", base=base, status="ERROR", error="x"),
        span_event(7, 2, 1, "order-service", "POST", 5, 90, kind="CLIENT", peer="notification-service", status="ERROR", error="ConnectError", base=base),
    ]
    feed(processor, envelope, ev)
    g = client.get("/api/v1/dependencies", headers=H).json()["data"]
    edge = next(e for e in g["edges"] if e["target"] == "notification-service")
    assert edge["unanswered_calls"] == 1 and edge["error_count"] == 1
    assert next(n for n in g["nodes"] if n["service_name"] == "notification-service")["observed_as_peer_only"]


def test_health_ready(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
