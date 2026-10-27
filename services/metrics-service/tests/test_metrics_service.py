"""Aggregation, de-duplication, out-of-order handling, missing intervals, alerting and API."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from obs_common.broker import METRIC_SPEC, NonRetryableError
from obs_common.testing import BASE_TIME, INTERNAL_HEADERS, metric_event, tid

from metrics_service.alerting import Observation, apply_observation, violated
from metrics_service.models import Alert, AlertRule, MetricAggregate, MetricSample

H = INTERNAL_HEADERS
WINDOW = "start=2026-10-16T09:55:00Z&end=2026-10-16T10:05:00Z"


def feed(processor, envelope, events):
    for e in events:
        processor.handle_event(envelope(e))
    processor.flush_dirty()


def test_aggregation_counts_errors_average_and_percentiles(client, processor, envelope, db):
    events = [metric_event("payment-service", float(v), offset_ms=i * 1000, error=(v == 500), trace=i + 1) for i, v in enumerate([100, 200, 300, 400, 500])]
    feed(processor, envelope, events)
    with db.session() as s:
        agg = s.scalar(select(MetricAggregate))
        assert (agg.sample_count, agg.error_count) == (5, 1)
        assert agg.average_value == 300 and agg.minimum_value == 100 and agg.maximum_value == 500
        assert agg.percentile_data["method"] == "nearest-rank" and agg.percentile_data["p95"] == 500 and agg.percentile_data["p50"] == 300
        assert agg.interval_start == BASE_TIME and agg.interval_end == BASE_TIME + timedelta(seconds=60)
    summary = client.get(f"/api/v1/metrics/summary?{WINDOW}", headers=H).json()["data"]
    assert summary["request_count"] == 5 and summary["error_count"] == 1 and summary["error_rate"] == 0.2
    assert summary["avg_latency_ms"] == 300 and summary["p95_latency_ms"] == 500 and summary["percentile_method"] == "nearest-rank"
    assert summary["services_with_errors"][0]["service_name"] == "payment-service"
    assert summary["throughput_rps"] == pytest.approx(5 / 600, abs=1e-4)


def test_raw_samples_and_aggregates_are_separate_tables(processor, envelope, db):
    feed(processor, envelope, [metric_event("svc", 10, offset_ms=i) for i in range(3)])
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(MetricSample)) == 3
        assert s.scalar(select(func.count()).select_from(MetricAggregate)) == 1


def test_duplicate_events_are_counted_once(processor, envelope, db):
    ev = metric_event("svc", 120, event_id="evt-1")
    assert processor.handle_event(envelope(ev)) == "stored"
    assert processor.handle_event(envelope(ev)) == "duplicate"
    processor.flush_dirty()
    processor.flush_dirty()                      # re-flushing never double counts (bucket recomputed from raw samples)
    with db.session() as s:
        assert s.scalar(select(MetricAggregate.sample_count)) == 1


def test_late_out_of_order_sample_is_folded_into_its_original_bucket(client, processor, envelope, db):
    feed(processor, envelope, [metric_event("svc", 100, offset_ms=70_000), metric_event("svc", 100, offset_ms=80_000)])   # minute 2 first
    feed(processor, envelope, [metric_event("svc", 400, offset_ms=5_000)])                                                  # late: minute 1
    with db.session() as s:
        counts = {a.interval_start: a.sample_count for a in s.scalars(select(MetricAggregate))}
    assert counts == {BASE_TIME: 1, BASE_TIME + timedelta(seconds=60): 2}


def test_missing_intervals_are_explicit_gaps(client, processor, envelope):
    feed(processor, envelope, [metric_event("svc", 100, offset_ms=5_000), metric_event("svc", 300, offset_ms=185_000, error=True)])
    r = client.get("/api/v1/metrics/timeseries?service_name=svc&start=2026-10-16T10:00:00Z&end=2026-10-16T10:04:00Z", headers=H).json()["data"]
    assert r["source"] == "aggregates" and r["bucket_seconds"] == 60
    flags = [p["missing"] for p in r["points"]]
    assert flags == [False, True, True, False, True]
    assert r["points"][1]["average"] is None and r["points"][1]["error_rate"] is None       # gap != zero
    assert r["points"][3]["error_rate"] == 1.0 and r["points"][3]["throughput_rps"] == pytest.approx(1 / 60, abs=1e-4)


def test_timeseries_other_bucket_sizes_and_all_services_use_raw_samples(client, processor, envelope):
    feed(processor, envelope, [metric_event("a", 100, offset_ms=1000), metric_event("b", 300, offset_ms=2000), metric_event("a", 200, offset_ms=130_000)])
    r = client.get("/api/v1/metrics/timeseries?start=2026-10-16T10:00:00Z&end=2026-10-16T10:04:00Z&bucket_seconds=120", headers=H).json()["data"]
    assert r["source"] == "raw_samples" and [p["sample_count"] for p in r["points"]] == [2, 1, 0]
    assert r["points"][0]["average"] == 200
    too_many = client.get("/api/v1/metrics/timeseries?start=2026-01-01T00:00:00Z&end=2026-10-01T00:00:00Z", headers=H)
    assert too_many.status_code == 422 and too_many.json()["error"]["code"] == "too_many_points"


def test_services_latency_histogram(client, processor, envelope):
    feed(processor, envelope, [metric_event("a", v, offset_ms=i) for i, v in enumerate([5, 20, 60, 120, 3000, 7000])])
    services = client.get(f"/api/v1/metrics/services?{WINDOW}", headers=H).json()["data"]
    assert services[0]["service_name"] == "a" and services[0]["request_count"] == 6
    lat = client.get(f"/api/v1/metrics/latency?{WINDOW}", headers=H).json()["data"]
    assert lat["services"][0]["p50_ms"] == 60 and lat["services"][0]["p99_ms"] == 7000
    hist = {h["le_ms"]: h["count"] for h in lat["histogram"]}
    assert hist[10] == 1 and hist[25] == 1 and hist[100] == 1 and hist[250] == 1 and hist[5000] == 1 and hist[None] == 1
    assert sum(hist.values()) == 6


def test_non_request_metrics_are_aggregated_without_error_semantics(client, processor, envelope):
    feed(processor, envelope, [metric_event("a", 5, metric="queue.depth", offset_ms=1), metric_event("a", 9, metric="queue.depth", offset_ms=2)])
    r = client.get("/api/v1/metrics/timeseries?service_name=a&metric_name=queue.depth&start=2026-10-16T10:00:00Z&end=2026-10-16T10:01:00Z", headers=H).json()["data"]
    assert r["points"][0]["average"] == 7 and r["points"][0]["error_rate"] is None


def test_poison_and_invalid_events(processor):
    with pytest.raises(NonRetryableError):
        processor.handle_event({"hello": "world"})
    with pytest.raises(NonRetryableError):
        processor.handle_event({"schema_version": 1, "event_id": "x", "event_type": "metric", "service_name": "a", "received_at": "2026-10-16T10:00:00Z", "payload": {"service_name": "a", "metric_name": "m", "value": "abc"}})


def test_consumer_through_broker_is_idempotent_for_duplicate_delivery(app, broker, db, envelope):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        proc = c.app.state.processor
        broker.publish("telemetry.metrics", envelope(metric_event("svc", 100), "evt-dup"))
        broker.drain()
        broker.redeliver_last(METRIC_SPEC.queue)          # broker delivers the same message again
        broker.drain()
        proc.flush_dirty()
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(MetricSample)) == 1
        assert s.scalar(select(MetricAggregate.sample_count)) == 1


# ------------------------------------------------------------------ alerting
def _burst(processor, envelope, *, errors, total, offset_start=0):
    feed(processor, envelope, [metric_event("payment-service", 800.0, offset_ms=offset_start + i * 10, error=i < errors, trace=100 + i) for i in range(total)])


def test_error_rate_alert_is_created_with_evidence(client, processor, envelope):
    _burst(processor, envelope, errors=6, total=10)
    counts = processor.evaluate_alerts()
    assert counts["created"] == 1
    alerts = client.get("/api/v1/alerts?status=active", headers=H).json()["data"]
    a = alerts[0]
    assert (a["service_name"], a["alert_type"], a["status"], a["severity"]) == ("payment-service", "error_rate", "open", "critical")
    assert a["measured_value"] == 0.6 and a["threshold_value"] == 0.2 and a["occurrence_count"] == 1
    assert a["first_seen_at"] and a["last_seen_at"]
    assert a["evidence"]["error_count"] == 6 and 1 <= len(a["evidence"]["trace_ids"]) <= 5
    assert a["evidence"]["trace_ids"][0] in {tid(100 + i) for i in range(6)}      # only failing traces are referenced
    assert "exceeds threshold" in a["message"]


def test_repeated_evaluations_deduplicate_instead_of_spamming(client, processor, envelope, db):
    _burst(processor, envelope, errors=6, total=10)
    for _ in range(25):                               # 25 evaluation cycles while the condition persists
        processor.evaluate_alerts()
    with db.session() as s:
        alerts = s.scalars(select(Alert).where(Alert.alert_type == "error_rate")).all()
        assert len(alerts) == 1 and alerts[0].occurrence_count == 25
    # new events do not create new alerts either
    _burst(processor, envelope, errors=8, total=10, offset_start=5000)
    processor.evaluate_alerts()
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Alert).where(Alert.alert_type == "error_rate")) == 1


def test_alert_auto_resolves_after_quiet_period_and_new_violation_opens_new_alert(client, processor, envelope, db):
    _burst(processor, envelope, errors=6, total=10)
    processor.evaluate_alerts()
    later = BASE_TIME + timedelta(seconds=120 + 400)           # window has slid past the bad samples, > resolve_after
    processor.evaluate_alerts(later)
    with db.session() as s:
        a = s.scalar(select(Alert))
        assert a.status == "resolved" and a.resolution == "auto" and a.active_key is None
    _burst(processor, envelope, errors=7, total=10, offset_start=600_000)
    processor.evaluate_alerts(BASE_TIME + timedelta(seconds=620))
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Alert)) == 2


def test_not_enough_samples_does_not_alert(processor, envelope):
    feed(processor, envelope, [metric_event("tiny", 5000, offset_ms=i, error=True) for i in range(3)])     # 100 % errors but only 3 samples
    counts = processor.evaluate_alerts()
    assert counts["created"] == 0


def test_latency_and_volume_rules_and_custom_thresholds(client, processor, envelope):
    feed(processor, envelope, [metric_event("slow-svc", 3000.0, offset_ms=i * 10, trace=200 + i) for i in range(6)])
    created = processor.evaluate_alerts()["created"]
    types = {a["alert_type"] for a in client.get("/api/v1/alerts", headers=H).json()["data"]}
    assert created == 2 and types == {"avg_latency", "p95_latency"}
    rule = {"service_name": "slow-svc", "alert_type": "request_volume", "operator": "gt", "threshold": 5, "window_seconds": 300, "min_samples": 1, "severity": "warning"}
    assert client.put("/api/v1/alerts/rules", json=rule, headers=H).status_code == 200
    processor.evaluate_alerts()
    assert "request_volume" in {a["alert_type"] for a in client.get("/api/v1/alerts", headers=H).json()["data"]}
    rules = client.get("/api/v1/alerts/rules", headers=H).json()["data"]
    assert {r["alert_type"] for r in rules if r["service_name"] == "*"} == {"error_rate", "avg_latency", "p95_latency", "request_volume"}


def test_acknowledge_resolve_and_transition_rules(client, processor, envelope):
    _burst(processor, envelope, errors=6, total=10)
    processor.evaluate_alerts()
    alert_id = client.get("/api/v1/alerts", headers=H).json()["data"][0]["id"]
    ack = client.patch(f"/api/v1/alerts/{alert_id}", json={"status": "acknowledged"}, headers={**H, "X-User-Name": "olivia", "X-User-Role": "operator"})
    assert ack.status_code == 200 and ack.json()["data"]["acknowledged_by"] == "olivia" and ack.json()["data"]["status"] == "acknowledged"
    processor.evaluate_alerts()                                    # acknowledged alert stays acknowledged while still violated
    assert client.get(f"/api/v1/alerts/{alert_id}", headers=H).json()["data"]["status"] == "acknowledged"
    assert client.patch(f"/api/v1/alerts/{alert_id}", json={"status": "resolved"}, headers=H).json()["data"]["status"] == "resolved"
    again = client.patch(f"/api/v1/alerts/{alert_id}", json={"status": "acknowledged"}, headers=H)
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_transition"
    assert client.get("/api/v1/alerts?status=active", headers=H).json()["data"] == []


def test_alert_api_validation_and_permissions(client):
    assert client.get("/api/v1/alerts/999", headers=H).status_code == 404
    assert client.get("/api/v1/alerts/abc", headers=H).status_code == 422
    assert client.patch("/api/v1/alerts/1", json={"status": "bogus"}, headers=H).status_code == 422
    assert client.patch("/api/v1/alerts/1", json={"status": "resolved", "extra": 1}, headers=H).status_code == 422
    assert client.patch("/api/v1/alerts/1", json={"status": "resolved"}, headers={**H, "X-User-Role": "viewer"}).status_code == 403
    assert client.put("/api/v1/alerts/rules", json={"alert_type": "error_rate", "threshold": 1}, headers={**H, "X-User-Role": "operator"}).status_code == 403
    assert client.get("/api/v1/alerts?status=weird", headers=H).status_code == 422
    assert client.get("/api/v1/alerts").status_code == 401
    assert client.get("/api/v1/metrics/summary?start=2026-10-16T12:00:00Z&end=2026-10-16T11:00:00Z", headers=H).status_code == 422


def test_alert_deduplication_unit():
    """Pure unit test of the dedup decision logic against an in-memory DB."""
    from obs_common.db import Database
    from metrics_service.models import Base

    database = Database("sqlite://")
    database.create_all(Base)
    rule = AlertRule(service_name="*", alert_type="avg_latency", operator="gt", threshold=100, window_seconds=300, min_samples=1, severity="warning")
    now = BASE_TIME
    with database.session() as s:
        outcomes = [apply_observation(s, Observation("svc", rule, 500.0, 10, {"trace_ids": [f"{i:032x}"]}), now + timedelta(seconds=i), 300) for i in range(5)]
        assert outcomes == ["created", "updated", "updated", "updated", "updated"]
        alert = s.scalar(select(Alert))
        assert alert.occurrence_count == 5 and len(alert.evidence["trace_ids"]) == 5 and alert.measured_value == 500.0
        assert apply_observation(s, Observation("svc", rule, 50.0, 10, {}), now + timedelta(seconds=100), 300) == "none"        # clear, but not long enough
        assert apply_observation(s, Observation("svc", rule, 50.0, 10, {}), now + timedelta(seconds=1000), 300) == "resolved"


def test_violation_operators():
    assert violated(5, "gt", 3) and not violated(3, "gt", 3) and violated(1, "lt", 3) and not violated(None, "gt", 1)
