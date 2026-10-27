from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from obs_common.db import BigPK, UTCDateTime, make_base
from obs_common.timeutil import utcnow

Base = make_base()

# processing_status values
PUBLISHED = "published"  # broker confirmed the publish
PENDING = "pending_publish"  # broker unavailable; stored for re-publication
REJECTED = "rejected"  # failed validation, never entered the pipeline
FAILED = "failed"  # re-publication attempts exhausted


class IngestionEvent(Base):
    __tablename__ = "ingestion_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_ingestion_event_id"),
        Index("ix_ingestion_status_received", "processing_status", "received_at"),
        Index("ix_ingestion_type_received", "event_type", "received_at"),
        Index("ix_ingestion_trace_id", "trace_id"),
        Index("ix_ingestion_service", "service_name"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    service_name: Mapped[str | None] = mapped_column(String(100))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    processing_status: Mapped[str] = mapped_column(String(24), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    # Only kept while the event waits for re-publication (cleared once published).
    payload: Mapped[dict | None] = mapped_column(JSON)
    routing_key: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)
