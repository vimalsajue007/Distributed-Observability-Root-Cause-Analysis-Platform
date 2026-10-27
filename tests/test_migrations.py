"""Alembic migrations must create exactly the schema the models describe (and be reversible)."""
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

import importlib

ROOT = Path(__file__).resolve().parent.parent
SERVICES = [
    ("ingestion-service", "ingestion_service"), ("trace-service", "trace_service"), ("log-service", "log_service"),
    ("metrics-service", "metrics_service"), ("rca-service", "rca_service"), ("api-gateway", "api_gateway"),
    ("order-service", "order_service"), ("payment-service", "payment_service"), ("notification-service", "notification_service"),
]


@pytest.mark.parametrize("service,package", SERVICES)
def test_migration_matches_models_and_is_reversible(service, package, tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "services" / service / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "services" / service / "migrations"))
    command.upgrade(cfg, "head")

    metadata = importlib.import_module(f"{package}.models").Base.metadata
    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": False}), metadata)
    assert diff == [], f"{service}: models and migrations differ: {diff}"
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set(metadata.tables)

    command.downgrade(cfg, "base")
    assert set(inspect(create_engine(url)).get_table_names()) - {"alembic_version"} == set()
