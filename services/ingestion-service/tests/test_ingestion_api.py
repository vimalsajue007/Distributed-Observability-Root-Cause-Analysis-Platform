from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from obs_common.broker import TRACE_SPEC
from obs_common.testing import INTERNAL_HEADERS, log_event, metric_event, span_event

from ingestion_service.main import create_app
from ingestion_service.models import IngestionEvent


def test_ingest_traces_normalizes_and_publishes(client, broker, auth):
    ev = span_event(1, 1, None, "Order-Service", "POST /orders", 0, 120, kind="SERVER", correlation_id="corr-1")
    ev["trace_id"] = ev["trace_id"].upper()
    resp = client.post("/api/v1/telemetry/traces", json={"events": [ev]}, headers=auth)
    assert resp.status_code == 202
    body = resp.json()
    assert body["data"]["accepted"] == 1 and body["error"] is None
    key, message = broker.published[0]
    assert key == "telemetry.traces"
    assert message["service_name"] == "order-service"  # normalised
    assert message["trace_id"] == "0" * 31 + "1"  # lower-cased, preserved
    assert message["correlation_id"] == "corr-1"
    assert message["event_id"].startswith("span:")  # deterministic => retry-safe
    assert message["payload"]["start_time"].endswith("Z")


def test_single_event_object_is_accepted(client, broker, auth):
    resp = client.post("/api/v1/telemetry/logs", json=log_event("svc", "INFO", "hello"), headers=auth)
    assert resp.status_code == 202
    assert broker.published[0][0] == "telemetry.logs"


def test_metrics_and_batch_endpoint_use_separate_routing_keys(client, broker, auth):
    payload = {
        "traces": [span_event(1, 1, None, "a", "op", 0, 10)],
        "logs": [log_event("a", "ERROR", "boom", trace=1, span=1)],
        "metrics": [metric_event("a", 12.5)],
    }
    resp = client.post("/api/v1/telemetry/batch", json=payload, headers=auth)
    assert resp.status_code == 202
    assert resp.json()["data"]["accepted"] == 3
    assert sorted(k for k, _ in broker.published) == ["telemetry.logs", "telemetry.metrics", "telemetry.traces"]


def test_partial_batch_rejects_malformed_events_with_meaningful_errors(client, broker, auth):
    good = span_event(1, 1, None, "a", "op", 0, 10)
    bad = {"trace_id": "xyz", "span_id": "1", "service_name": "a", "operation_name": "x", "start_time": "nope", "end_time": "nope"}
    resp = client.post("/api/v1/telemetry/traces", json={"events": [good, bad]}, headers=auth)
    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["accepted"] == 1 and data["rejected"] == 1
    fields = {e["field"] for e in data["errors"][0]["errors"]}
    assert {"trace_id", "span_id", "start_time"} <= fields
    assert len(broker.published) == 1  # the malformed event never entered the pipeline
    assert "xyz" not in resp.text  # submitted values are not echoed


def test_all_malformed_returns_422(client, broker, auth):
    resp = client.post("/api/v1/telemetry/metrics", json={"events": [{"service_name": "a", "metric_name": "m", "value": "NaN"}]}, headers=auth)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "telemetry_rejected"
    assert broker.published == []


def test_end_before_start_is_rejected(client, auth):
    ev = span_event(1, 1, None, "a", "op", 100, 10)
    ev["end_time"], ev["start_time"] = ev["start_time"], ev["end_time"]
    resp = client.post("/api/v1/telemetry/traces", json=ev, headers=auth)
    assert resp.status_code == 422


def test_authentication_required(client):
    assert client.post("/api/v1/telemetry/logs", json=log_event("a", "INFO", "x")).status_code == 401
    assert client.post("/api/v1/telemetry/logs", json=log_event("a", "INFO", "x"), headers={"X-API-Key": "wrong"}).status_code == 401
    # internal token (gateway) is accepted as well
    assert client.post("/api/v1/telemetry/logs", json=log_event("a", "INFO", "x"), headers=INTERNAL_HEADERS).status_code == 202


def test_payload_size_limit(broker, db, auth, make_settings):
    app = create_app(make_settings(max_body_bytes=500), broker=broker, db=db)
    with TestClient(app) as small:
        resp = small.post("/api/v1/telemetry/logs", json={"events": [log_event("a", "INFO", "x" * 2000)]}, headers=auth)
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


def test_batch_size_limit(broker, db, auth, make_settings):
    app = create_app(make_settings(max_batch_events=2), broker=broker, db=db)
    with TestClient(app) as small:
        events = [log_event("a", "INFO", f"m{i}") for i in range(3)]
        assert small.post("/api/v1/telemetry/logs", json={"events": events}, headers=auth).status_code == 413


def test_duplicate_event_ids_are_not_republished(client, broker, auth):
    ev = span_event(1, 1, None, "a", "op", 0, 10)
    assert client.post("/api/v1/telemetry/traces", json=ev, headers=auth).json()["data"]["accepted"] == 1
    again = client.post("/api/v1/telemetry/traces", json=ev, headers=auth).json()["data"]
    assert again["duplicates"] == 1 and again["accepted"] == 0
    assert len(broker.published) == 1


def test_broker_down_events_are_buffered_then_republished(client, broker, db, auth):
    broker.available = False
    resp = client.post("/api/v1/telemetry/traces", json=span_event(1, 1, None, "a", "op", 0, 10), headers=auth)
    assert resp.status_code == 202  # not lost
    body = resp.json()
    assert body["data"]["queued_for_retry"] == 1
    assert body["warnings"][0]["code"] == "broker_unavailable"
    with db.session() as s:
        row = s.scalar(select(IngestionEvent))
        assert row.processing_status == "pending_publish" and row.payload is not None

    broker.available = True
    assert client.app.state.service.republish_pending() == 1
    assert len(broker.published) == 1
    with db.session() as s:
        row = s.scalar(select(IngestionEvent))
        assert row.processing_status == "published" and row.payload is None


def test_republish_stops_while_broker_still_down(client, broker, auth):
    broker.available = False
    client.post("/api/v1/telemetry/logs", json=log_event("a", "INFO", "x"), headers=auth)
    assert client.app.state.service.republish_pending() == 0


def test_database_failure_surfaces_503_only_when_events_would_be_lost(client, broker, auth, monkeypatch):
    def boom(rows, require_durable):
        if require_durable:
            raise OperationalError("insert", {}, Exception("db down"))

    monkeypatch.setattr(client.app.state.service, "_record", boom)
    # broker healthy: telemetry flows even though statistics cannot be stored
    assert client.post("/api/v1/telemetry/logs", json=log_event("a", "INFO", "ok"), headers=auth).status_code == 202
    # broker down AND database down: cannot guarantee durability => 503
    broker.available = False
    resp = client.post("/api/v1/telemetry/logs", json=log_event("a", "INFO", "lost?"), headers=auth)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "storage_unavailable"
    assert "db down" not in resp.text  # no internals leaked


def test_statistics_record_success_and_failure(client, auth):
    client.post("/api/v1/telemetry/logs", json={"events": [log_event("a", "INFO", "x"), {"service_name": "a"}]}, headers=auth)
    stats = client.get("/api/v1/telemetry/stats", headers=INTERNAL_HEADERS).json()["data"]
    assert stats["totals"]["published"] == 1 and stats["totals"]["rejected"] == 1
    assert stats["by_event_type"]["log"]["rejected"] == 1
    assert client.get("/api/v1/telemetry/stats").status_code == 401


def test_health_and_ready(client, broker):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").status_code == 200
    broker.available = False
    assert client.get("/ready").status_code == 503


def test_queue_binding_retains_messages_when_consumer_is_down(client, broker, auth):
    broker.pause_consumer(TRACE_SPEC.queue)
    client.post("/api/v1/telemetry/traces", json=span_event(1, 1, None, "a", "op", 0, 10), headers=auth)
    assert broker.pending(TRACE_SPEC.queue) == 1
