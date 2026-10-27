"""Unit tests: percentile calculation (nearest-rank), averages, error rates."""
import pytest

from metrics_service.stats import error_rate, percentile_nearest_rank, summarize


def test_nearest_rank_percentile_definition():
    data = list(range(1, 101))  # 1..100
    assert percentile_nearest_rank(data, 50) == 50
    assert percentile_nearest_rank(data, 95) == 95
    assert percentile_nearest_rank(data, 99) == 99
    assert percentile_nearest_rank(data, 100) == 100
    twenty = list(range(1, 21))
    assert percentile_nearest_rank(twenty, 95) == 19        # ceil(0.95 * 20) = 19th smallest
    assert percentile_nearest_rank(twenty, 90) == 18
    assert percentile_nearest_rank([7], 99) == 7
    assert percentile_nearest_rank([], 95) is None
    assert percentile_nearest_rank([1, 2, 3, 4], 0) == 1      # rank is at least 1


def test_percentile_always_returns_an_observed_value():
    data = [10.0, 20.0, 250.0, 900.0]
    for p in (1, 25, 50, 75, 90, 99):
        assert percentile_nearest_rank(data, p) in data


def test_summarize():
    s = summarize([300, 100, 200])
    assert s["count"] == 3 and s["avg"] == 200 and s["min"] == 100 and s["max"] == 300
    assert s["p50"] == 200 and s["p95"] == 300 and s["sum"] == 600
    empty = summarize([])
    assert empty["count"] == 0 and empty["avg"] is None and empty["p95"] is None


def test_error_rate():
    assert error_rate(1, 4) == 0.25
    assert error_rate(0, 10) == 0.0
    assert error_rate(0, 0) is None            # no data is not 'healthy'
    assert error_rate(5, 5) == 1.0
