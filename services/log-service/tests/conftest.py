import pytest
from fastapi.testclient import TestClient

from obs_common.broker import InMemoryBroker
from obs_common.db import Database
from obs_common.testing import INTERNAL_TOKEN

from log_service.config import Settings
from log_service.main import create_app
from log_service.models import Base


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
def app(broker, db):
    settings = Settings(database_url="sqlite://", internal_api_token=INTERNAL_TOKEN, broker_url="memory://")
    return create_app(settings, broker=broker, db=db)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def processor(app):
    return app.state.processor


@pytest.fixture
def envelope():
    from obs_common.schemas import LogPayload, build_envelope
    from obs_common.timeutil import utcnow

    counter = {"n": 0}

    def _wrap(payload: dict, event_id: str | None = None) -> dict:
        counter["n"] += 1
        model = LogPayload.model_validate(payload)
        return build_envelope("log", model, event_id or model.event_id or f"log-{counter['n']}", utcnow())

    return _wrap
