"""initial schema for log-service

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
        "error_patterns",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("service_name", sa.String(100), nullable=False),
        sa.Column("pattern_hash", sa.String(40), nullable=False),
        sa.Column("pattern", sa.String(500), nullable=False),
        sa.Column("sample_message", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("first_seen", UTCDateTime, nullable=False),
        sa.Column("last_seen", UTCDateTime, nullable=False),
        sa.Column("last_trace_id", sa.String(32), nullable=True),
        sa.UniqueConstraint("service_name", "pattern_hash", name="uq_error_pattern_service_hash"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_error_patterns_count", "error_patterns", ["occurrence_count"])
    op.create_index("ix_error_patterns_last_seen", "error_patterns", ["last_seen"])

    op.create_table(
        "processed_logs",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=True),
        sa.Column("span_id", sa.String(16), nullable=True),
        sa.Column("service_name", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("timestamp", UTCDateTime, nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("raw_severity", sa.String(32), nullable=True),
        sa.Column("logger", sa.String(200), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=True),
        sa.UniqueConstraint("event_id", name="uq_processed_logs_event_id"),
        sa.UniqueConstraint("fingerprint", name="uq_processed_logs_fingerprint"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_processed_logs_correlation_id", "processed_logs", ["correlation_id"])
    op.create_index("ix_processed_logs_service_ts", "processed_logs", ["service_name", "timestamp"])
    op.create_index("ix_processed_logs_severity_ts", "processed_logs", ["severity", "timestamp"])
    op.create_index("ix_processed_logs_span_id", "processed_logs", ["span_id"])
    op.create_index("ix_processed_logs_timestamp", "processed_logs", ["timestamp"])
    op.create_index("ix_processed_logs_trace_id", "processed_logs", ["trace_id"])


def downgrade() -> None:
    op.drop_table("processed_logs")
    op.drop_table("error_patterns")
