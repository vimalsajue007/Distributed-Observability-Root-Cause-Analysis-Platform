"""Database plumbing. Every service creates its own Database for its own schema."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import BigInteger, DateTime, Integer, create_engine, text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

# MySQL DATETIME has second precision by default; telemetry needs sub-millisecond precision.
UTCDateTime = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
# BIGINT primary keys on MySQL, INTEGER on SQLite (needed for SQLite autoincrement in tests).
BigPK = BigInteger().with_variant(Integer(), "sqlite")


def make_base() -> type[DeclarativeBase]:
    """Each service owns a separate declarative Base (=> separate metadata/migrations)."""

    class Base(DeclarativeBase):
        pass

    return Base


class Database:
    def __init__(self, url: str, *, echo: bool = False, pool_size: int = 10):
        kwargs: dict = {"pool_pre_ping": True, "echo": echo}
        if url.startswith("sqlite"):
            kwargs.update(connect_args={"check_same_thread": False})
            if ":memory:" in url or url.endswith("://") or url == "sqlite://":
                kwargs["poolclass"] = StaticPool
        else:
            kwargs.update(
                pool_size=pool_size,
                max_overflow=10,
                pool_recycle=1800,
                connect_args={"connect_timeout": 5},
            )
        self.url = url
        # An in-memory SQLite database is ONE shared connection (StaticPool); sqlite3 connections must not be used by two
        # threads at once, so sessions are serialised. MySQL (real connection pool) is unaffected.
        self._lock: threading.RLock | None = threading.RLock() if kwargs.get("poolclass") is StaticPool else None
        self.engine = create_engine(url, **kwargs)
        self._factory = sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Unit of work: commit on success, rollback on error."""
        if self._lock is not None:
            self._lock.acquire()
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            if self._lock is not None:
                self._lock.release()

    def create_all(self, base: type[DeclarativeBase]) -> None:
        base.metadata.create_all(self.engine)

    def ping(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError:
            return False

    def dispose(self) -> None:
        self.engine.dispose()


def escape_like(value: str) -> str:
    """Escape LIKE wildcards in user-supplied search text (use with escape='\\\\')."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
