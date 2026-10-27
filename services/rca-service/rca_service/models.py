from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from obs_common.db import BigPK, UTCDateTime, make_base
from obs_common.timeutil import utcnow

Base = make_base()


class DiagnosticReport(Base):
    """One deterministic diagnosis per trace (re-analysis updates it in place)."""

    __tablename__ = "diagnostic_reports"
    __table_args__ = (
        UniqueConstraint("trace_id", name="uq_diagnostic_reports_trace"),
        Index("ix_diagnostic_reports_service", "affected_service"),
        Index("ix_diagnostic_reports_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    affected_service: Mapped[str | None] = mapped_column(String(100))
    diagnosis: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="complete")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    # additional columns
    classification: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence_level: Mapped[str] = mapped_column(String(20), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON)  # ranking, timeline, paths, contributions, warnings
    rules_version: Mapped[str] = mapped_column(String(20), nullable=False)
    analysis_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")  # event | manual
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    evidence: Mapped[list["DiagnosticEvidence"]] = relationship(
        back_populates="report", cascade="all, delete-orphan", order_by="DiagnosticEvidence.id"
    )


class DiagnosticEvidence(Base):
    __tablename__ = "diagnostic_evidence"
    __table_args__ = (Index("ix_diagnostic_evidence_report", "report_id"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(BigPK, ForeignKey("diagnostic_reports.id", ondelete="CASCADE"), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(30), nullable=False)
    reference_id: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    # additional columns
    service_name: Mapped[str | None] = mapped_column(String(100))
    observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    data: Mapped[dict | None] = mapped_column(JSON)

    report: Mapped[DiagnosticReport] = relationship(back_populates="evidence")
