"""initial schema for rca-service

Revision ID: 0001
Revises: None
"""
from alembic import op
import sqlalchemy as sa

from obs_common.db import BigPK, UTCDateTime

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diagnostic_reports",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("affected_service", sa.String(100), nullable=True),
        sa.Column("diagnosis", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.Column("classification", sa.String(30), nullable=False),
        sa.Column("confidence_level", sa.String(20), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("rules_version", sa.String(20), nullable=False),
        sa.Column("analysis_count", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("updated_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("trace_id", name="uq_diagnostic_reports_trace"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_diagnostic_reports_created", "diagnostic_reports", ["created_at"])
    op.create_index("ix_diagnostic_reports_service", "diagnostic_reports", ["affected_service"])

    op.create_table(
        "diagnostic_evidence",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("report_id", BigPK, sa.ForeignKey("diagnostic_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_type", sa.String(30), nullable=False),
        sa.Column("reference_id", sa.String(128), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.Column("service_name", sa.String(100), nullable=True),
        sa.Column("observed_at", UTCDateTime, nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_diagnostic_evidence_report", "diagnostic_evidence", ["report_id"])


def downgrade() -> None:
    op.drop_table("diagnostic_evidence")
    op.drop_table("diagnostic_reports")
