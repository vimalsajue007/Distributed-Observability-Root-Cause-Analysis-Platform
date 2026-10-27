import pytest
from fastapi.testclient import TestClient

from obs_common.broker import InMemoryBroker
from obs_common.db import Database
from obs_common.testing import INGEST_KEY, INTERNAL_TOKEN

from ingestion_service.config import Settings
from ingestion_service.main import create_app
from ingestion_service.models import Base


def _make_settings(**overrides):
    values = dict(
        database_url="sqlite://",
        internal_api_token=INTERNAL_TOKEN,
        broker_url="memory://",
        ingest_api_keys=INGEST_KEY,
        run_background_workers=False,
        publish_backoff_seconds=0.0,
        publish_retries=1,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def make_settings():
    return _make_settings


@pytest.fixture
def broker():
    b = InMemoryBroker()
    b.declare_topology()
    return b


@pytest.fixture
def db():
    database = Database("sqlite://")
    database.create_all(Base)
    return database


@pytest.fixture
def client(broker, db):
    app = create_app(_make_settings(), broker=broker, db=db)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth():
    return {"X-API-Key": INGEST_KEY}
