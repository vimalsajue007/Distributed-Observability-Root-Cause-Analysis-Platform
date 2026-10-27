"""Pure trace-reconstruction logic (no I/O) - heavily unit tested.

Approach
--------
1. De-duplicate spans by ``span_id`` (retries / duplicate deliveries never create duplicate nodes).
2. Index spans by id and build a parent -> children map. Arrival order is irrelevant, so
   out-of-order delivery produces exactly the same tree.
3. Roots are spans without a parent. Spans whose ``parent_span_id`` is not present in the trace are
   *orphans*: they are treated as roots of a sub-tree, flagged ``missing_parent`` and the absent parent
   is reported in ``missing_spans`` (=> the trace is *incomplete*; nothing is invented).
4. A depth-first walk ordered by (start_time, span_id) yields the display order and depth; cycles
   (corrupt parent links) are broken and flagged ``invalid_parent``.
5. ``self_time`` = span duration - the union of the (clipped) time covered by its children, so
   concurrent children are not double-subtracted. A service's *latency contribution* is the sum of
   the self-times of its spans divided by the sum of all self-times in the trace.
Original timestamps and parent links are never modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


@dataclass
class SpanRec:
    span_id: str
    parent_span_id: str | None
    service_name: str
    operation_name: str
    start_time: datetime
    end_time: datetime
    duration_ms: float
    status: str = "OK"
    error_message: str | None = None
    kind: str = "INTERNAL"
    peer_service: str | None = None
    correlation_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class SlowPolicy:
    threshold_ms: float = 1000.0
    baseline_factor: float = 3.0
    baseline_min_samples: int = 20
    baseline_min_ms: float = 100.0

    def is_slow(self, span: SpanRec, baseline: tuple[float, int] | None = None) -> bool:
        """Slow if above the absolute threshold, or well above the historical average of the same operation."""
        if span.duration_ms >= self.threshold_ms:
            return True
        if baseline:
            avg, count = baseline
            if count >= self.baseline_min_samples and avg > 0:
                return span.duration_ms >= max(self.baseline_factor * avg, self.baseline_min_ms)
        return False


@dataclass
class RSpan:
    rec: SpanRec
    depth: int = 0
    order: int = 0
    offset_ms: float = 0.0
    self_time_ms: float = 0.0
    is_error: bool = False
    is_slow: bool = False
    missing_parent: bool = False
    invalid_parent: bool = False
    children_count: int = 0


@dataclass
class ServiceBreakdown:
    service_name: str
    span_count: int = 0
    error_count: int = 0
    self_time_ms: float = 0.0
    total_time_ms: float = 0.0
    latency_share: float = 0.0


@dataclass
class Reconstruction:
    spans: list[RSpan]
    start_time: datetime | None
    end_time: datetime | None
    duration_ms: float
    root_service: str | None
    root_span_id: str | None
    has_root: bool
    status: str
    missing_spans: list[dict[str, Any]]
    services: list[ServiceBreakdown]
    correlation_id: str | None

    @property
    def missing_parent_count(self) -> int:
        return sum(1 for s in self.spans if s.missing_parent)

    @property
    def error_span_count(self) -> int:
        return sum(1 for s in self.spans if s.is_error)

    @property
    def is_complete(self) -> bool:
        return self.has_root and not self.missing_spans


def _union_ms(intervals: list[tuple[datetime, datetime]]) -> float:
    if not intervals:
        return 0.0
    intervals = sorted(intervals)
    total = 0.0
    cur_start, cur_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= cur_end:
            if end > cur_end:
                cur_end = end
        else:
            total += (cur_end - cur_start).total_seconds() * 1000.0
            cur_start, cur_end = start, end
    total += (cur_end - cur_start).total_seconds() * 1000.0
    return total


def reconstruct(
    spans: list[SpanRec],
    *,
    slow: Callable[[SpanRec], bool] | None = None,
) -> Reconstruction:
    unique: dict[str, SpanRec] = {}
    for rec in spans:
        unique.setdefault(rec.span_id, rec)  # first one wins: duplicates are ignored
    if not unique:
        return Reconstruction([], None, None, 0.0, None, None, False, "OK", [], [], None)

    sort_key = lambda r: (r.start_time, r.span_id)  # noqa: E731
    children: dict[str, list[SpanRec]] = {}
    roots: list[SpanRec] = []
    orphans: list[SpanRec] = []
    for rec in sorted(unique.values(), key=sort_key):
        if rec.parent_span_id is None:
            roots.append(rec)
        elif rec.parent_span_id in unique:
            children.setdefault(rec.parent_span_id, []).append(rec)
        else:
            orphans.append(rec)

    trace_start = min(r.start_time for r in unique.values())
    trace_end = max(r.end_time for r in unique.values())

    ordered: list[RSpan] = []
    visited: set[str] = set()

    def walk(rec: SpanRec, depth: int, *, missing_parent: bool = False, invalid_parent: bool = False) -> None:
        if rec.span_id in visited:
            return
        visited.add(rec.span_id)
        kids = children.get(rec.span_id, [])
        node = RSpan(
            rec=rec,
            depth=depth,
            order=len(ordered),
            offset_ms=round((rec.start_time - trace_start).total_seconds() * 1000.0, 3),
            is_error=rec.status == "ERROR",
            is_slow=bool(slow(rec)) if slow else False,
            missing_parent=missing_parent,
            invalid_parent=invalid_parent,
            children_count=len(kids),
        )
        clipped = [(max(k.start_time, rec.start_time), min(k.end_time, rec.end_time)) for k in kids]
        clipped = [(s, e) for s, e in clipped if e > s]
        node.self_time_ms = round(max(rec.duration_ms - _union_ms(clipped), 0.0), 3)
        ordered.append(node)
        for kid in kids:
            walk(kid, depth + 1)

    for root in roots:
        walk(root, 0)
    for orphan in orphans:
        walk(orphan, 0, missing_parent=True)
    # spans only reachable through a cycle: break it and flag the break
    for rec in sorted(unique.values(), key=sort_key):
        if rec.span_id not in visited:
            walk(rec, 0, invalid_parent=True)

    missing: dict[str, list[str]] = {}
    for orphan in orphans:
        missing.setdefault(orphan.parent_span_id or "", []).append(orphan.span_id)
    missing_spans = [{"span_id": pid, "referenced_by": sorted(refs)} for pid, refs in sorted(missing.items())]

    per_service: dict[str, ServiceBreakdown] = {}
    intervals: dict[str, list[tuple[datetime, datetime]]] = {}
    for node in ordered:
        b = per_service.setdefault(node.rec.service_name, ServiceBreakdown(node.rec.service_name))
        b.span_count += 1
        b.error_count += 1 if node.is_error else 0
        b.self_time_ms += node.self_time_ms
        intervals.setdefault(node.rec.service_name, []).append((node.rec.start_time, node.rec.end_time))
    total_self = sum(b.self_time_ms for b in per_service.values()) or 0.0
    for name, b in per_service.items():
        b.self_time_ms = round(b.self_time_ms, 3)
        b.total_time_ms = round(_union_ms(intervals[name]), 3)
        b.latency_share = round(b.self_time_ms / total_self, 4) if total_self > 0 else 0.0
    services = sorted(per_service.values(), key=lambda b: (-b.self_time_ms, b.service_name))

    true_roots = [r for r in roots]
    root_rec = true_roots[0] if true_roots else min(unique.values(), key=sort_key)
    correlation = next((r.correlation_id for r in sorted(unique.values(), key=sort_key) if r.correlation_id), None)
    status = "ERROR" if any(n.is_error for n in ordered) else "OK"
    return Reconstruction(
        spans=ordered,
        start_time=trace_start,
        end_time=trace_end,
        duration_ms=round((trace_end - trace_start).total_seconds() * 1000.0, 3),
        root_service=root_rec.service_name,
        root_span_id=root_rec.span_id if true_roots else None,
        has_root=bool(true_roots),
        status=status,
        missing_spans=missing_spans,
        services=services,
        correlation_id=correlation,
    )
