"""initial schema for notification-service

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
        "notifications",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("order_id", BigPK, nullable=False),
        sa.Column("dedupe_key", sa.String(128), nullable=False),
        sa.Column("recipient", sa.String(120), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_notifications_dedupe_key"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_notifications_order", "notifications", ["order_id"])


def downgrade() -> None:
    op.drop_table("notifications")
