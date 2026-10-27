"""initial schema for metrics-service

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
        "alert_rules",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("service_name", sa.String(100), nullable=False),
        sa.Column("alert_type", sa.String(32), nullable=False),
        sa.Column("operator", sa.String(4), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("min_samples", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("service_name", "alert_type", name="uq_alert_rule_service_type"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )

    op.create_table(
        "alerts",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("service_name", sa.String(100), nullable=False),
        sa.Column("alert_type", sa.String(32), nullable=False),
        sa.Column("measured_value", sa.Float(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("first_seen_at", UTCDateTime, nullable=False),
        sa.Column("last_seen_at", UTCDateTime, nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("operator", sa.String(4), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("active_key", sa.String(160), nullable=True),
        sa.Column("acknowledged_by", sa.String(100), nullable=True),
        sa.Column("acknowledged_at", UTCDateTime, nullable=True),
        sa.Column("resolved_at", UTCDateTime, nullable=True),
        sa.Column("resolution", sa.String(16), nullable=True),
        sa.UniqueConstraint("active_key", name="uq_alerts_active_key"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_alerts_last_seen", "alerts", ["last_seen_at"])
    op.create_index("ix_alerts_service", "alerts", ["service_name"])
    op.create_index("ix_alerts_status", "alerts", ["status"])

    op.create_table(
        "metric_aggregates",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("service_name", sa.String(100), nullable=False),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("interval_start", UTCDateTime, nullable=False),
        sa.Column("interval_end", UTCDateTime, nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("average_value", sa.Float(), nullable=True),
        sa.Column("minimum_value", sa.Float(), nullable=True),
        sa.Column("maximum_value", sa.Float(), nullable=True),
        sa.Column("percentile_data", sa.JSON(), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("sum_value", sa.Float(), nullable=False),
        sa.Column("updated_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("service_name", "metric_name", "interval_start", "interval_seconds", name="uq_metric_aggregate_bucket"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_metric_aggregates_start", "metric_aggregates", ["interval_start"])

    op.create_table(
        "metric_samples",
        sa.Column("id", BigPK, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("service_name", sa.String(100), nullable=False),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("recorded_at", UTCDateTime, nullable=False),
        sa.Column("is_error", sa.Boolean(), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=True),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("event_id", name="uq_metric_samples_event_id"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_metric_samples_lookup", "metric_samples", ["service_name", "metric_name", "recorded_at"])
    op.create_index("ix_metric_samples_recorded", "metric_samples", ["recorded_at"])


def downgrade() -> None:
    op.drop_table("metric_samples")
    op.drop_table("metric_aggregates")
    op.drop_table("alerts")
    op.drop_table("alert_rules")
