"""Read side of the trace service: trace list/detail and the telemetry-derived dependency graph."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from obs_common.filters import TraceListFilters
from obs_common.timeutil import to_iso

from .models import Trace, TraceSpan
from .processor import TraceProcessor, to_rec
from .reconstruction import Reconstruction, reconstruct


def trace_summary(t: Trace) -> dict[str, Any]:
    return {
        "trace_id": t.trace_id,
        "root_service": t.root_service,
        "correlation_id": t.correlation_id,
        "start_time": t.start_time,
        "end_time": t.end_time,
        "duration_ms": t.duration_ms,
        "status": t.status,
        "completion_state": t.completion_state,
        "completed_at": t.completed_at,
        "span_count": t.span_count,
        "error_span_count": t.error_span_count,
        "missing_parent_count": t.missing_parent_count,
    }


def list_traces(s: Session, f: TraceListFilters) -> tuple[list[dict[str, Any]], int]:
    conditions = []
    if f.trace_id:
        conditions.append(Trace.trace_id == f.trace_id if len(f.trace_id) == 32 else Trace.trace_id.like(f"{f.trace_id}%"))
    if f.correlation_id:
        conditions.append(Trace.correlation_id == f.correlation_id)
    if f.status:
        conditions.append(Trace.status == f.status)
    if f.completion_state:
        conditions.append(Trace.completion_state == f.completion_state)
    if f.min_duration_ms is not None:
        conditions.append(Trace.duration_ms >= f.min_duration_ms)
    if f.start:
        conditions.append(Trace.start_time >= f.start)
    if f.end:
        conditions.append(Trace.start_time < f.end)
    if f.service_name:
        conditions.append(Trace.trace_id.in_(select(TraceSpan.trace_id).where(TraceSpan.service_name == f.service_name)))
    total = s.scalar(select(func.count()).select_from(Trace).where(*conditions)) or 0
    rows = s.scalars(
        select(Trace).where(*conditions).order_by(Trace.start_time.desc(), Trace.id.desc()).offset((f.page - 1) * f.page_size).limit(f.page_size)
    ).all()
    return [trace_summary(r) for r in rows], total


def span_dict(node) -> dict[str, Any]:
    rec = node.rec
    return {
        "span_id": rec.span_id,
        "parent_span_id": rec.parent_span_id,
        "service_name": rec.service_name,
        "operation_name": rec.operation_name,
        "kind": rec.kind,
        "start_time": rec.start_time,
        "end_time": rec.end_time,
        "duration_ms": rec.duration_ms,
        "status": rec.status,
        "error_message": rec.error_message,
        "peer_service": rec.peer_service,
        "attributes": rec.attributes,
        "depth": node.depth,
        "order": node.order,
        "offset_ms": node.offset_ms,
        "self_time_ms": node.self_time_ms,
        "children_count": node.children_count,
        "is_error": node.is_error,
        "is_slow": node.is_slow,
        "missing_parent": node.missing_parent,
        "invalid_parent": node.invalid_parent,
    }


def load_reconstruction(s: Session, processor: TraceProcessor, trace_id: str) -> tuple[Trace | None, Reconstruction | None]:
    trace = s.scalar(select(Trace).where(Trace.trace_id == trace_id))
    if trace is None:
        return None, None
    rows = s.scalars(select(TraceSpan).where(TraceSpan.trace_id == trace_id)).all()
    recs = [to_rec(r) for r in rows]
    baselines = processor.baselines(s, recs, trace_id)

    def slow(rec) -> bool:
        return processor.policy.is_slow(rec, baselines.get((rec.service_name, rec.operation_name)))

    return trace, reconstruct(recs, slow=slow)


def trace_detail(trace: Trace, recon: Reconstruction) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    if recon.missing_spans:
        warnings.append(
            {
                "code": "missing_spans",
                "message": f"{len(recon.missing_spans)} parent span(s) referenced by child spans were never received; the trace is incomplete.",
            }
        )
    if not recon.has_root and recon.spans:
        warnings.append({"code": "no_root_span", "message": "No root span has been received for this trace."})
    if trace.completion_state == "in_progress":
        warnings.append({"code": "trace_in_progress", "message": "The trace is still within its completion waiting period; more spans may arrive."})
    return {
        **trace_summary(trace),
        "has_root_span": recon.has_root,
        "slow_span_count": sum(1 for n in recon.spans if n.is_slow),
        "services": [
            {
                "service_name": b.service_name,
                "span_count": b.span_count,
                "error_count": b.error_count,
                "self_time_ms": b.self_time_ms,
                "total_time_ms": b.total_time_ms,
                "latency_share": b.latency_share,
            }
            for b in recon.services
        ],
        "missing_spans": recon.missing_spans,
        "spans": [span_dict(n) for n in recon.spans],
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- dependency graph


def _num(v: Any) -> float:
    return float(v or 0)


def dependency_graph(s: Session, start: datetime, end: datetime) -> dict[str, Any]:
    """Derive nodes/edges purely from observed spans (nothing is hard-coded)."""
    child = aliased(TraceSpan, name="child")
    parent = aliased(TraceSpan, name="parent")
    is_err = case((child.status == "ERROR", 1), (and_(parent.kind == "CLIENT", parent.status == "ERROR"), 1), else_=0)
    latency = case((parent.kind == "CLIENT", parent.duration_ms), else_=child.duration_ms)

    # (1) observed cross-service parent -> child links
    linked = s.execute(
        select(parent.service_name, child.service_name, func.count(), func.sum(is_err), func.sum(latency), func.max(latency))
        .select_from(child)
        .join(parent, and_(parent.trace_id == child.trace_id, parent.span_id == child.parent_span_id))
        .where(parent.service_name != child.service_name, child.start_time >= start, child.start_time < end)
        .group_by(parent.service_name, child.service_name)
    ).all()

    # (2) outgoing calls whose callee never produced a span (timeouts / unreachable services)
    client = aliased(TraceSpan, name="client")
    callee = aliased(TraceSpan, name="callee")
    unanswered = s.execute(
        select(
            client.service_name,
            client.peer_service,
            func.count(),
            func.sum(case((client.status == "ERROR", 1), else_=0)),
            func.sum(client.duration_ms),
            func.max(client.duration_ms),
        )
        .where(
            client.kind == "CLIENT",
            client.peer_service.is_not(None),
            client.peer_service != client.service_name,
            client.start_time >= start,
            client.start_time < end,
            ~exists().where(and_(callee.trace_id == client.trace_id, callee.parent_span_id == client.span_id)),
        )
        .group_by(client.service_name, client.peer_service)
    ).all()

    edges: dict[tuple[str, str], dict[str, float]] = {}
    for src, dst, count, errors, total_latency, max_latency in list(linked) + list(unanswered):
        e = edges.setdefault((src, dst), {"count": 0, "errors": 0, "latency_sum": 0.0, "latency_max": 0.0, "unanswered": 0})
        e["count"] += int(count)
        e["errors"] += int(_num(errors))
        e["latency_sum"] += _num(total_latency)
        e["latency_max"] = max(e["latency_max"], _num(max_latency))
    for src, dst, count, *_ in unanswered:
        edges[(src, dst)]["unanswered"] += int(count)

    # nodes: "entry" spans = requests a service handled (root span or called by another service)
    parent_alias = aliased(TraceSpan, name="p")
    node_rows = s.execute(
        select(
            TraceSpan.service_name,
            func.count(),
            func.sum(case((TraceSpan.status == "ERROR", 1), else_=0)),
            func.sum(TraceSpan.duration_ms),
        )
        .select_from(TraceSpan)
        .outerjoin(parent_alias, and_(parent_alias.trace_id == TraceSpan.trace_id, parent_alias.span_id == TraceSpan.parent_span_id))
        .where(
            TraceSpan.start_time >= start,
            TraceSpan.start_time < end,
            or_(parent_alias.id.is_(None), parent_alias.service_name != TraceSpan.service_name),
        )
        .group_by(TraceSpan.service_name)
    ).all()
    nodes: dict[str, dict[str, Any]] = {}
    for name, count, errors, total in node_rows:
        count = int(count)
        nodes[name] = {
            "service_name": name,
            "request_count": count,
            "error_count": int(_num(errors)),
            "error_rate": round(_num(errors) / count, 4) if count else None,
            "avg_latency_ms": round(_num(total) / count, 2) if count else None,
            "observed_as_peer_only": False,
        }
    for src, dst in edges:
        for name in (src, dst):
            if name not in nodes:
                nodes[name] = {
                    "service_name": name, "request_count": 0, "error_count": 0, "error_rate": None,
                    "avg_latency_ms": None, "observed_as_peer_only": True,
                }

    edge_list = []
    for (src, dst), e in sorted(edges.items()):
        count = int(e["count"])
        edge_list.append(
            {
                "source": src,
                "target": dst,
                "request_count": count,
                "error_count": int(e["errors"]),
                "error_rate": round(e["errors"] / count, 4) if count else None,
                "avg_latency_ms": round(e["latency_sum"] / count, 2) if count else None,
                "max_latency_ms": round(e["latency_max"], 2),
                "unanswered_calls": int(e["unanswered"]),
                "failure_observed": e["errors"] > 0,
            }
        )
    return {
        "window": {"start": to_iso(start), "end": to_iso(end)},
        "nodes": sorted(nodes.values(), key=lambda n: n["service_name"]),
        "edges": edge_list,
    }
