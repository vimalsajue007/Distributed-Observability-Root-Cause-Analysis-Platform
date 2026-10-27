import pytest
from fastapi.testclient import TestClient

from obs_common.broker import InMemoryBroker
from obs_common.db import Database
from obs_common.testing import INTERNAL_TOKEN

from trace_service.config import Settings
from trace_service.main import create_app
from trace_service.models import Base


def _settings(**overrides):
    values = dict(
        database_url="sqlite://",
        internal_api_token=INTERNAL_TOKEN,
        broker_url="memory://",
        run_background_workers=False,
        trace_completion_wait_seconds=10,
    )
    values.update(overrides)
    return Settings(**values)


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
    return create_app(_settings(), broker=broker, db=db)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def processor(app):
    return app.state.processor


@pytest.fixture
def envelope():
    """Wrap a span payload in the broker envelope exactly as the ingestion service does."""
    from obs_common.schemas import SpanPayload, build_envelope
    from obs_common.timeutil import utcnow

    def _wrap(payload: dict) -> dict:
        model = SpanPayload.model_validate(payload)
        return build_envelope("trace", model, model.default_event_id(), utcnow())

    return _wrap
