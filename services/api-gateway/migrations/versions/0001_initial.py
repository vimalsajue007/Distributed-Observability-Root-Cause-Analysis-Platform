"""initial schema for api-gateway

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
        "gateway_requests",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("upstream", sa.String(50), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", UTCDateTime, nullable=False),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_gateway_requests_created", "gateway_requests", ["created_at"])
    op.create_index("ix_gateway_requests_user", "gateway_requests", ["username", "created_at"])

    op.create_table(
        "users",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.Column("last_login_at", UTCDateTime, nullable=True),
        sa.UniqueConstraint("username", name="uq_users_username"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("gateway_requests")
