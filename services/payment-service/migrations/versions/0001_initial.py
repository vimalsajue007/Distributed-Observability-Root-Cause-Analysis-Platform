"""initial schema for payment-service

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
        "payments",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("order_id", BigPK, nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reference", sa.String(40), nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_payments_idempotency_key"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_payments_order", "payments", ["order_id"])


def downgrade() -> None:
    op.drop_table("payments")
