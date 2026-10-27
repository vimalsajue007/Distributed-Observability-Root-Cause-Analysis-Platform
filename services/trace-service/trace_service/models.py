from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from obs_common.db import BigPK, UTCDateTime, make_base
from obs_common.timeutil import utcnow

Base = make_base()


class Trace(Base):
    __tablename__ = "traces"
    __table_args__ = (
        UniqueConstraint("trace_id", name="uq_traces_trace_id"),
        Index("ix_traces_start_time", "start_time"),
        Index("ix_traces_root_service", "root_service"),
        Index("ix_traces_correlation_id", "correlation_id"),
        Index("ix_traces_state_last_event", "completion_state", "last_event_at"),
        Index("ix_traces_status_start", "status", "start_time"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    root_service: Mapped[str | None] = mapped_column(String(100))
    start_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OK")
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # additional bookkeeping
    completion_state: Mapped[str] = mapped_column(String(16), nullable=False, default="in_progress")
    span_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_span_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_parent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    last_event_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class TraceSpan(Base):
    __tablename__ = "trace_spans"
    __table_args__ = (
        UniqueConstraint("trace_id", "span_id", name="uq_trace_spans_trace_span"),
        Index("ix_trace_spans_trace_id", "trace_id"),
        Index("ix_trace_spans_service_name", "service_name"),
        Index("ix_trace_spans_service_start", "service_name", "start_time"),
        Index("ix_trace_spans_start_time", "start_time"),
        Index("ix_trace_spans_parent", "trace_id", "parent_span_id"),
        Index("ix_trace_spans_op", "service_name", "operation_name"),
        Index("ix_trace_spans_peer", "peer_service"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(32), ForeignKey("traces.trace_id", ondelete="CASCADE"), nullable=False)
    span_id: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(16))
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    operation_name: Mapped[str] = mapped_column(String(255), nullable=False)
    start_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OK")
    error_message: Mapped[str | None] = mapped_column(Text)
    # additional columns
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="INTERNAL")
    peer_service: Mapped[str | None] = mapped_column(String(100))
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    event_id: Mapped[str | None] = mapped_column(String(128))
    attributes: Mapped[dict | None] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
