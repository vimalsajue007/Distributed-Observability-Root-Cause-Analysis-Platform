"""initial schema for trace-service

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
        "traces",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("root_service", sa.String(100), nullable=True),
        sa.Column("start_time", UTCDateTime, nullable=False),
        sa.Column("end_time", UTCDateTime, nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("completed_at", UTCDateTime, nullable=True),
        sa.Column("completion_state", sa.String(16), nullable=False),
        sa.Column("span_count", sa.Integer(), nullable=False),
        sa.Column("error_span_count", sa.Integer(), nullable=False),
        sa.Column("missing_parent_count", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column("last_event_at", UTCDateTime, nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("trace_id", name="uq_traces_trace_id"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_traces_correlation_id", "traces", ["correlation_id"])
    op.create_index("ix_traces_root_service", "traces", ["root_service"])
    op.create_index("ix_traces_start_time", "traces", ["start_time"])
    op.create_index("ix_traces_state_last_event", "traces", ["completion_state", "last_event_at"])
    op.create_index("ix_traces_status_start", "traces", ["status", "start_time"])

    op.create_table(
        "trace_spans",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(32), sa.ForeignKey("traces.trace_id", ondelete="CASCADE"), nullable=False),
        sa.Column("span_id", sa.String(16), nullable=False),
        sa.Column("parent_span_id", sa.String(16), nullable=True),
        sa.Column("service_name", sa.String(100), nullable=False),
        sa.Column("operation_name", sa.String(255), nullable=False),
        sa.Column("start_time", UTCDateTime, nullable=False),
        sa.Column("end_time", UTCDateTime, nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("peer_service", sa.String(100), nullable=True),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column("event_id", sa.String(128), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=True),
        sa.Column("received_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("trace_id", "span_id", name="uq_trace_spans_trace_span"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_trace_spans_op", "trace_spans", ["service_name", "operation_name"])
    op.create_index("ix_trace_spans_parent", "trace_spans", ["trace_id", "parent_span_id"])
    op.create_index("ix_trace_spans_peer", "trace_spans", ["peer_service"])
    op.create_index("ix_trace_spans_service_name", "trace_spans", ["service_name"])
    op.create_index("ix_trace_spans_service_start", "trace_spans", ["service_name", "start_time"])
    op.create_index("ix_trace_spans_start_time", "trace_spans", ["start_time"])
    op.create_index("ix_trace_spans_trace_id", "trace_spans", ["trace_id"])


def downgrade() -> None:
    op.drop_table("trace_spans")
    op.drop_table("traces")
