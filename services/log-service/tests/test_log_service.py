"""Unit + integration tests: normalisation, trace correlation, dedup, patterns, search API."""
from datetime import datetime

import pytest
from sqlalchemy import func, select

from obs_common.broker import LOG_SPEC, NonRetryableError
from obs_common.testing import BASE_TIME, INTERNAL_HEADERS, log_event, tid

from log_service.models import ErrorPattern, ProcessedLog
from log_service.normalization import error_pattern, normalize_message, normalize_service, normalize_severity, normalize_timestamp

H = INTERNAL_HEADERS


# ------------------------------------------------------------------ unit: normalisation
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("info", "INFO"), ("WARN", "WARNING"), ("warning", "WARNING"), ("Err", "ERROR"), ("error", "ERROR"),
        ("FATAL", "CRITICAL"), ("crit", "CRITICAL"), ("debug", "INFO"), ("trace", "INFO"), (None, "INFO"), ("", "INFO"),
        ("something-weird", "INFO"), (10, "INFO"), (20, "INFO"), (30, "WARNING"), (40, "ERROR"), (50, "CRITICAL"),
        (9, "INFO"), (13, "WARNING"), (17, "ERROR"), (21, "CRITICAL"), ("40", "ERROR"), (True, "INFO"),
    ],
)
def test_severity_normalisation(raw, expected):
    assert normalize_severity(raw) == expected


def test_message_normalisation_handles_unstructured_input_and_redacts_secrets():
    assert normalize_message({"b": 1, "a": [1, 2]}) == '{"a": [1, 2], "b": 1}'
    assert normalize_message(None) == ""
    assert normalize_message(12345) == "12345"
    assert normalize_message("bad\x00char\x07s") == "badchars"
    assert "hunter2" not in normalize_message("login failed password=hunter2 for user bob")
    assert "abc.def" not in normalize_message("Authorization: Bearer abc.def.ghi")
    assert len(normalize_message("x" * 20000, 100)) == 100


def test_timestamp_normalisation_and_fallback():
    fallback = datetime(2026, 1, 1)
    assert normalize_timestamp("2026-10-16T10:00:00+05:30", fallback) == datetime(2026, 10, 16, 4, 30)
    assert normalize_timestamp(1792144800, fallback) == datetime(2026, 10, 16, 10, 0)
    assert normalize_timestamp(1792144800123, fallback).microsecond == 123000      # epoch milliseconds
    assert normalize_timestamp("garbage", fallback) == fallback
    assert normalize_timestamp(None, fallback) == fallback


def test_service_and_pattern_normalisation():
    assert normalize_service("  Payment Service!! ") == "payment-service"
    assert normalize_service(None) == "unknown-service"
    a = error_pattern("Order 1234 failed for user 'bob' trace 0123456789abcdef0123456789abcdef after 15.5 ms")
    b = error_pattern("Order 99 failed for user 'alice' trace fedcba9876543210fedcba9876543210 after 2 ms")
    assert a == b and "<n>" in a and "<hex>" in a


# ------------------------------------------------------------------ processing
def test_log_is_stored_normalised_and_linked_to_trace(client, processor, envelope):
    processor.handle_event(envelope(log_event("Payment-Service", "err", "charge failed", trace=7, span=3, correlation_id="corr-7")))
    rows = client.get(f"/api/v1/logs?trace_id={tid(7)}", headers=H).json()["data"]
    assert len(rows) == 1
    row = rows[0]
    assert (row["service_name"], row["severity"], row["span_id"], row["correlation_id"]) == ("payment-service", "ERROR", f"{3:016x}", "corr-7")
    assert row["timestamp"] == "2026-10-16T10:00:00.000000Z"


def test_missing_optional_fields_and_unstructured_message(client, processor, envelope):
    processor.handle_event(envelope({"service_name": "svc", "message": {"event": "started", "port": 8000}}))
    row = client.get("/api/v1/logs", headers=H).json()["data"][0]
    assert row["severity"] == "INFO" and row["trace_id"] is None and row["correlation_id"] is None
    assert '"event": "started"' in row["message"]


def test_duplicate_event_id_and_identical_content_are_stored_once(processor, envelope, db):
    ev = log_event("svc", "INFO", "same thing", trace=1, span=1, event_id="evt-1")
    assert processor.handle_event(envelope(ev)) == "stored"
    assert processor.handle_event(envelope(ev)) == "duplicate"                           # redelivery
    clone = {**ev, "event_id": "evt-2"}                                                  # client retry with new event id
    assert processor.handle_event(envelope(clone, "evt-2")) == "duplicate"
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(ProcessedLog)) == 1


def test_batch_processing_survives_a_malformed_event(processor, envelope, db):
    batch = [
        envelope(log_event("svc", "INFO", "one")),
        {"garbage": True},
        envelope(log_event("svc", "WARNING", "two")),
        {"schema_version": 1, "event_id": "x", "event_type": "log", "service_name": "svc", "received_at": "2026-10-16T10:00:00Z", "payload": {"service_name": "svc"}},
        envelope(log_event("svc", "ERROR", "three")),
    ]
    result = processor.process_batch(batch)
    assert (result.stored, result.failed) == (3, 2)
    assert {e["index"] for e in result.errors} == {1, 3}


def test_error_patterns_count_repeats_but_not_duplicates(client, processor, envelope):
    for i in range(3):
        processor.handle_event(envelope(log_event("payment-service", "ERROR", f"Charge for order {1000 + i} failed: upstream 503", trace=i + 1, offset_ms=i)))
    processor.handle_event(envelope(log_event("payment-service", "ERROR", "Charge for order 1000 failed: upstream 503", trace=1, offset_ms=0)))  # duplicate content
    processor.handle_event(envelope(log_event("payment-service", "INFO", "ok")))                                                                   # info: no pattern
    patterns = client.get("/api/v1/logs/patterns", headers=H).json()["data"]
    assert len(patterns) == 1 and patterns[0]["occurrence_count"] == 3
    assert patterns[0]["pattern"] == "Charge for order <n> failed: upstream <n>"
    assert client.get("/api/v1/logs/patterns?min_occurrences=4", headers=H).json()["data"] == []


# ------------------------------------------------------------------ broker integration + failure handling
def test_consumption_through_broker_with_poison_message(app, broker, db):
    from fastapi.testclient import TestClient

    with TestClient(app):
        broker.publish("telemetry.logs", {"not": "valid"})
        from obs_common.schemas import LogPayload, build_envelope
        from obs_common.timeutil import utcnow

        model = LogPayload.model_validate(log_event("svc", "INFO", "via broker"))
        broker.publish("telemetry.logs", build_envelope("log", model, "evt-broker", utcnow()))
        broker.drain()
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(ProcessedLog)) == 1
    assert len(broker.dead_letters[LOG_SPEC.queue]) == 1


def test_transient_database_failure_is_retried_then_succeeds(app, broker, processor, db, monkeypatch):
    from fastapi.testclient import TestClient
    from obs_common.schemas import LogPayload, build_envelope
    from obs_common.timeutil import utcnow

    original = processor.store
    state = {"calls": 0}

    def flaky(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] < 3:
            raise RuntimeError("lost connection to MySQL server")
        return original(*args, **kwargs)

    monkeypatch.setattr(processor, "store", flaky)
    with TestClient(app):
        model = LogPayload.model_validate(log_event("svc", "ERROR", "retry me"))
        broker.publish("telemetry.logs", build_envelope("log", model, "evt-r", utcnow()))
        broker.drain()
    assert state["calls"] == 3
    assert broker.dead_letters[LOG_SPEC.queue] == []
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(ProcessedLog)) == 1


def test_persistent_failure_ends_in_dead_letter_queue(app, broker, processor, monkeypatch):
    from fastapi.testclient import TestClient
    from obs_common.schemas import LogPayload, build_envelope
    from obs_common.timeutil import utcnow

    monkeypatch.setattr(processor, "store", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    with TestClient(app):
        model = LogPayload.model_validate(log_event("svc", "INFO", "doomed"))
        broker.publish("telemetry.logs", build_envelope("log", model, "evt-d", utcnow()))
        broker.drain()
    dead = broker.dead_letters[LOG_SPEC.queue]
    assert len(dead) == 1 and dead[0]["attempt"] == LOG_SPEC.max_attempts and "retries exhausted" in dead[0]["reason"]


# ------------------------------------------------------------------ search API
@pytest.fixture
def populated(processor, envelope):
    events = [
        log_event("order-service", "INFO", "order received", trace=1, span=1, offset_ms=0, correlation_id="c-1"),
        log_event("payment-service", "ERROR", "payment timeout 100% failure_rate", trace=1, span=2, offset_ms=1000, correlation_id="c-1"),
        log_event("payment-service", "CRITICAL", "gateway down", trace=2, span=2, offset_ms=2000, correlation_id="c-2"),
        log_event("notification-service", "WARNING", "slow smtp", trace=2, span=3, offset_ms=3000, correlation_id="c-2"),
    ]
    for ev in events:
        processor.handle_event(envelope(ev))


def test_search_filters(client, populated):
    def q(qs):
        return client.get(f"/api/v1/logs?{qs}", headers=H).json()

    assert q("severity=ERROR&severity=CRITICAL")["meta"]["total"] == 2
    assert q("severity=ERROR,WARNING")["meta"]["total"] == 2
    assert q("service_name=payment-service")["meta"]["total"] == 2
    assert q("correlation_id=c-2")["meta"]["total"] == 2
    assert q(f"trace_id={tid(1)}&order=asc")["data"][0]["message"] == "order received"
    assert q("start=2026-10-16T10:00:02Z&end=2026-10-16T10:00:03.5Z")["meta"]["total"] == 2
    assert q("q=TIMEOUT")["meta"]["total"] == 1                                       # case-insensitive
    assert q("q=100%25")["meta"]["total"] == 1                                         # '%' is literal, not a wildcard
    assert q("q=%25")["meta"]["total"] == 1
    assert q("q=_")["meta"]["total"] == 1                                              # '_' literal (failure_rate)


def test_search_pagination_and_sort(client, populated):
    page = client.get("/api/v1/logs?page_size=3&page=2", headers=H).json()
    assert page["meta"] == {**page["meta"], "page": 2, "page_size": 3, "total": 4, "total_pages": 2} and len(page["data"]) == 1
    newest = client.get("/api/v1/logs?page_size=1", headers=H).json()["data"][0]
    assert newest["message"] == "slow smtp"


def test_get_single_log_and_error_codes(client, populated):
    first = client.get("/api/v1/logs?order=asc&page_size=1", headers=H).json()["data"][0]
    assert client.get(f"/api/v1/logs/{first['id']}", headers=H).json()["data"]["message"] == "order received"
    assert client.get("/api/v1/logs/9999", headers=H).status_code == 404
    assert client.get("/api/v1/logs/abc", headers=H).status_code == 422
    assert client.get("/api/v1/logs?severity=LOUD", headers=H).status_code == 422
    assert client.get("/api/v1/logs?trace_id=nothex", headers=H).status_code == 422
    r = client.get("/api/v1/logs?start=2026-10-16T12:00:00Z&end=2026-10-16T10:00:00Z", headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_time_range"
    assert client.get("/api/v1/logs").status_code == 401


def test_log_data_requires_operator_role(client, populated):
    viewer = {**H, "X-User-Role": "viewer"}
    r = client.get("/api/v1/logs", headers=viewer)
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert client.get("/api/v1/logs", headers={**H, "X-User-Role": "operator"}).status_code == 200


def test_sql_injection_attempt_in_filters_is_harmless(client, populated):
    r = client.get("/api/v1/logs", params={"q": "'; DROP TABLE processed_logs; --"}, headers=H)
    assert r.status_code == 200 and r.json()["meta"]["total"] == 0
    assert client.get("/api/v1/logs", headers=H).json()["meta"]["total"] == 4
    assert client.get("/api/v1/logs", params={"service_name": "x' OR '1'='1"}, headers=H).status_code == 422
