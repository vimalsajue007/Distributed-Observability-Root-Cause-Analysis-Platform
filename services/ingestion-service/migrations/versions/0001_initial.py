"""initial schema for ingestion-service

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
        "ingestion_events",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("service_name", sa.String(100), nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=True),
        sa.Column("received_at", UTCDateTime, nullable=False),
        sa.Column("processing_status", sa.String(24), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("routing_key", sa.String(64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("updated_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("event_id", name="uq_ingestion_event_id"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_ingestion_service", "ingestion_events", ["service_name"])
    op.create_index("ix_ingestion_status_received", "ingestion_events", ["processing_status", "received_at"])
    op.create_index("ix_ingestion_trace_id", "ingestion_events", ["trace_id"])
    op.create_index("ix_ingestion_type_received", "ingestion_events", ["event_type", "received_at"])


def downgrade() -> None:
    op.drop_table("ingestion_events")
