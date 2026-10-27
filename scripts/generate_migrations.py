#!/usr/bin/env python3
"""Generate Alembic scaffolding and the initial migration of every database-owning service from its SQLAlchemy models.

    python scripts/generate_migrations.py

The generated files are committed; re-run this only when a model changes *before the first release*
(afterwards add a new revision by hand). tests/test_migrations.py proves migrations and models agree.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from sqlalchemy import BigInteger, Boolean, Float, Integer, JSON, String, Text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "libs/obs_common/src"))

SERVICES = [
    ("ingestion-service", "ingestion_service", "ingestion_db"),
    ("trace-service", "trace_service", "trace_db"),
    ("log-service", "log_service", "log_db"),
    ("metrics-service", "metrics_service", "metrics_db"),
    ("rca-service", "rca_service", "rca_db"),
    ("api-gateway", "api_gateway", "gateway_db"),
    ("order-service", "order_service", "order_db"),
    ("payment-service", "payment_service", "payment_db"),
    ("notification-service", "notification_service", "notification_db"),
]

ALEMBIC_INI = """[alembic]
script_location = %(here)s/migrations
prepend_sys_path = .
# The URL comes from the DATABASE_URL environment variable (see migrations/env.py).

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""

ENV_PY = '''"""Alembic environment for {service}. The database URL is read from DATABASE_URL."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from {package}.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, dialect_opts={{"paramstyle": "named"}})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'''

MAKO = '''"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''


def render_type(col) -> str:
    t = col.type
    if isinstance(t, BigInteger) and getattr(t, "_variant_mapping", None):
        return "BigPK"
    if type(t).__name__ == "DateTime":
        return "UTCDateTime"
    if isinstance(t, String) and not isinstance(t, Text):
        return f"sa.String({t.length})"
    if isinstance(t, Text):
        return "sa.Text()"
    if isinstance(t, Boolean):
        return "sa.Boolean()"
    if isinstance(t, Float):
        return "sa.Float()"
    if isinstance(t, JSON):
        return "sa.JSON()"
    if isinstance(t, BigInteger):
        return "sa.BigInteger()"
    if isinstance(t, Integer):
        return "sa.Integer()"
    raise ValueError(f"unsupported column type {t!r} for {col.table.name}.{col.name}")


def render_table(table) -> tuple[str, str]:
    lines = [f'    op.create_table(\n        "{table.name}",']
    for col in table.columns:
        args = [f'"{col.name}"', render_type(col)]
        for fk in col.foreign_keys:
            ondelete = f', ondelete="{fk.ondelete}"' if fk.ondelete else ""
            args.append(f'sa.ForeignKey("{fk.target_fullname}"{ondelete})')
        if col.primary_key:
            args.append("primary_key=True")
            if col.autoincrement is True or (col.autoincrement == "auto" and len(table.primary_key.columns) == 1):
                args.append("autoincrement=True")
        args.append(f"nullable={bool(col.nullable)}")
        lines.append(f"        sa.Column({', '.join(args)}),")
    from sqlalchemy import UniqueConstraint

    for c in sorted(table.constraints, key=lambda c: c.name or ""):
        if isinstance(c, UniqueConstraint):
            cols = ", ".join(f'"{x.name}"' for x in c.columns)
            lines.append(f'        sa.UniqueConstraint({cols}, name="{c.name}"),')
    lines.append('        mysql_engine="InnoDB",\n        mysql_default_charset="utf8mb4",\n    )')
    for ix in sorted(table.indexes, key=lambda i: i.name):
        cols = ", ".join(f'"{x.name}"' for x in ix.columns)
        uq = ", unique=True" if ix.unique else ""
        lines.append(f'    op.create_index("{ix.name}", "{table.name}", [{cols}]{uq})')
    return "\n".join(lines), table.name


def generate(service: str, package: str) -> None:
    sys.path.insert(0, str(ROOT / "services" / service))
    for mod in [m for m in sys.modules if m.startswith(package)]:
        del sys.modules[mod]
    models = importlib.import_module(f"{package}.models")
    metadata = models.Base.metadata
    base = ROOT / "services" / service
    (base / "migrations/versions").mkdir(parents=True, exist_ok=True)
    (base / "alembic.ini").write_text(ALEMBIC_INI)
    (base / "migrations/env.py").write_text(ENV_PY.format(service=service, package=package))
    (base / "migrations/script.py.mako").write_text(MAKO)
    body, drops = [], []
    for table in metadata.sorted_tables:
        text, name = render_table(table)
        body.append(text)
        drops.append(f'    op.drop_table("{name}")')
    content = (
        f'"""initial schema for {service}\n\nRevision ID: 0001\nRevises: None\n"""\n'
        "from alembic import op\nimport sqlalchemy as sa\n\nfrom obs_common.db import BigPK, UTCDateTime\n\n"
        "revision = \"0001\"\ndown_revision = None\nbranch_labels = None\ndepends_on = None\n\n\n"
        "def upgrade() -> None:\n" + "\n\n".join(body) + "\n\n\n"
        "def downgrade() -> None:\n" + "\n".join(reversed(drops)) + "\n"
    )
    (base / "migrations/versions/0001_initial.py").write_text(content)
    sys.path.remove(str(ROOT / "services" / service))
    print(f"generated {service}: {len(metadata.tables)} tables")


if __name__ == "__main__":
    for svc, pkg, _ in SERVICES:
        generate(svc, pkg)
