from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from obs_common.db import BigPK, UTCDateTime, make_base
from obs_common.timeutil import utcnow

Base = make_base()


class ProcessedLog(Base):
    __tablename__ = "processed_logs"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_processed_logs_event_id"),
        UniqueConstraint("fingerprint", name="uq_processed_logs_fingerprint"),
        Index("ix_processed_logs_trace_id", "trace_id"),
        Index("ix_processed_logs_span_id", "span_id"),
        Index("ix_processed_logs_correlation_id", "correlation_id"),
        Index("ix_processed_logs_service_ts", "service_name", "timestamp"),
        Index("ix_processed_logs_severity_ts", "severity", "timestamp"),
        Index("ix_processed_logs_timestamp", "timestamp"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    span_id: Mapped[str | None] = mapped_column(String(16))
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    # additional columns
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_severity: Mapped[str | None] = mapped_column(String(32))
    logger: Mapped[str | None] = mapped_column(String(200))
    attributes: Mapped[dict | None] = mapped_column(JSON)


class ErrorPattern(Base):
    """Repeated ERROR/CRITICAL message shapes (numbers/ids replaced by placeholders)."""

    __tablename__ = "error_patterns"
    __table_args__ = (
        UniqueConstraint("service_name", "pattern_hash", name="uq_error_pattern_service_hash"),
        Index("ix_error_patterns_last_seen", "last_seen"),
        Index("ix_error_patterns_count", "occurrence_count"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    pattern_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    sample_message: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_trace_id: Mapped[str | None] = mapped_column(String(32))
