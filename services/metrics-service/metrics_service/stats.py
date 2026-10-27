"""Pure statistics helpers.

Percentile definition (used everywhere in the platform):
    **nearest-rank**: for N sorted samples, the p-th percentile is the sample at 1-based rank
    ``ceil(p / 100 * N)`` (rank is at least 1). It always returns an observed value (no interpolation),
    so p95 of 20 samples is the 19th smallest sample.
Percentiles of a window are always computed from the *raw samples* of that window - percentiles of
buckets are never averaged (which would be statistically wrong).
"""

from __future__ import annotations

import math
from typing import Iterable

PERCENTILES = (50, 90, 95, 99)
METHOD = "nearest-rank"


def percentile_nearest_rank(sorted_values: list[float], p: float) -> float | None:
    n = len(sorted_values)
    if n == 0:
        return None
    rank = max(math.ceil(p / 100.0 * n), 1)
    return sorted_values[min(rank, n) - 1]


def error_rate(errors: int, total: int) -> float | None:
    """errors / total; ``None`` when nothing was measured (an empty interval is not 'healthy')."""
    if total <= 0:
        return None
    return errors / total


def summarize(values: Iterable[float]) -> dict:
    data = sorted(values)
    n = len(data)
    if n == 0:
        return {"count": 0, "sum": 0.0, "avg": None, "min": None, "max": None, **{f"p{p}": None for p in PERCENTILES}}
    total = math.fsum(data)
    return {
        "count": n,
        "sum": total,
        "avg": total / n,
        "min": data[0],
        "max": data[-1],
        **{f"p{p}": percentile_nearest_rank(data, p) for p in PERCENTILES},
    }


def rnd(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)
