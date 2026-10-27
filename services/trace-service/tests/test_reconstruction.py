"""Unit tests: trace reconstruction, parent-child relationships, durations."""
from datetime import datetime, timedelta

import pytest

from trace_service.reconstruction import SlowPolicy, SpanRec, reconstruct

T0 = datetime(2026, 10, 16, 10, 0, 0)


def rec(span_id, parent, service, start_ms, dur_ms, status="OK", op="op", kind="INTERNAL", peer=None):
    start = T0 + timedelta(milliseconds=start_ms)
    return SpanRec(
        span_id=span_id, parent_span_id=parent, service_name=service, operation_name=op,
        start_time=start, end_time=start + timedelta(milliseconds=dur_ms), duration_ms=dur_ms, status=status, kind=kind, peer_service=peer,
    )


def chain():
    return [
        rec("a", None, "api-gateway", 0, 500),
        rec("b", "a", "order-service", 10, 480),
        rec("c", "b", "payment-service", 50, 300),
        rec("d", "b", "notification-service", 360, 100),
    ]


def test_parent_child_relationships_and_depth():
    r = reconstruct(chain())
    by_id = {n.rec.span_id: n for n in r.spans}
    assert [n.rec.span_id for n in r.spans] == ["a", "b", "c", "d"]  # execution order (DFS by start time)
    assert [by_id[i].depth for i in "abcd"] == [0, 1, 2, 2]
    assert by_id["b"].children_count == 2
    assert r.root_service == "api-gateway" and r.root_span_id == "a" and r.has_root


def test_duration_and_offsets():
    r = reconstruct(chain())
    assert r.duration_ms == 500
    assert r.start_time == T0 and r.end_time == T0 + timedelta(milliseconds=500)
    by_id = {n.rec.span_id: n for n in r.spans}
    assert by_id["c"].offset_ms == 50


def test_self_time_and_service_latency_contribution():
    r = reconstruct(chain())
    by_id = {n.rec.span_id: n for n in r.spans}
    assert by_id["a"].self_time_ms == 20          # 500 - 480
    assert by_id["b"].self_time_ms == 80          # 480 - (300 + 100)
    assert by_id["c"].self_time_ms == 300
    shares = {s.service_name: s.latency_share for s in r.services}
    assert shares["payment-service"] == pytest.approx(300 / 500, abs=1e-3)
    assert sum(shares.values()) == pytest.approx(1.0, abs=1e-3)
    assert r.services[0].service_name == "payment-service"  # largest contributor first


def test_concurrent_children_do_not_double_subtract():
    spans = [rec("p", None, "svc", 0, 100), rec("x", "p", "a", 0, 80), rec("y", "p", "b", 20, 80)]
    r = reconstruct(spans)
    p = next(n for n in r.spans if n.rec.span_id == "p")
    assert p.self_time_ms == 0  # children cover [0,100] together


def test_out_of_order_arrival_gives_identical_tree():
    ordered = reconstruct(chain())
    shuffled = reconstruct([chain()[i] for i in (3, 1, 0, 2)])
    assert [(n.rec.span_id, n.depth) for n in ordered.spans] == [(n.rec.span_id, n.depth) for n in shuffled.spans]


def test_duplicate_spans_do_not_create_duplicate_nodes():
    r = reconstruct(chain() + [chain()[2], chain()[2]])
    assert len(r.spans) == 4


def test_missing_parent_marks_trace_incomplete_without_inventing_spans():
    spans = [rec("a", None, "api-gateway", 0, 500), rec("c", "zz", "payment-service", 50, 300)]
    r = reconstruct(spans)
    assert len(r.spans) == 2  # no fake parent
    orphan = next(n for n in r.spans if n.rec.span_id == "c")
    assert orphan.missing_parent and orphan.depth == 0
    assert r.missing_spans == [{"span_id": "zz", "referenced_by": ["c"]}]
    assert not r.is_complete and r.missing_parent_count == 1


def test_trace_without_root_is_incomplete():
    r = reconstruct([rec("c", "zz", "payment-service", 50, 300)])
    assert not r.has_root and not r.is_complete
    assert r.root_span_id is None


def test_cycle_in_parent_links_is_broken_and_flagged():
    spans = [rec("a", "b", "x", 0, 10), rec("b", "a", "y", 1, 5)]
    r = reconstruct(spans)
    assert len(r.spans) == 2
    assert any(n.invalid_parent for n in r.spans)


def test_error_status_propagates_to_trace_and_service_breakdown():
    spans = chain()
    spans[2].status = "ERROR"
    r = reconstruct(spans)
    assert r.status == "ERROR" and r.error_span_count == 1
    assert next(s for s in r.services if s.service_name == "payment-service").error_count == 1


def test_empty_input():
    r = reconstruct([])
    assert r.spans == [] and r.duration_ms == 0 and not r.has_root


def test_slow_detection_absolute_and_baseline():
    policy = SlowPolicy(threshold_ms=1000, baseline_factor=3, baseline_min_samples=20, baseline_min_ms=100)
    fast, slow_abs, mid = rec("1", None, "s", 0, 50), rec("2", None, "s", 0, 1500), rec("3", None, "s", 0, 400)
    assert not policy.is_slow(fast) and policy.is_slow(slow_abs)
    assert not policy.is_slow(mid)                       # 400ms alone is not slow
    assert policy.is_slow(mid, baseline=(50.0, 100))     # 8x its own baseline
    assert not policy.is_slow(mid, baseline=(50.0, 5))   # not enough samples to trust the baseline
    r = reconstruct([slow_abs, fast], slow=policy.is_slow)
    assert [n.is_slow for n in r.spans] == [False, True] or any(n.is_slow for n in r.spans)
