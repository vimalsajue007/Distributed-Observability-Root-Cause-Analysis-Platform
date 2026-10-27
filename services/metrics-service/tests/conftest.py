import pytest
from fastapi.testclient import TestClient

from obs_common.broker import InMemoryBroker
from obs_common.db import Database
from obs_common.testing import BASE_TIME, INTERNAL_TOKEN

from metrics_service.config import Settings
from metrics_service.main import create_app
from metrics_service.models import Base


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
    settings = Settings(
        database_url="sqlite://", internal_api_token=INTERNAL_TOKEN, broker_url="memory://", run_background_workers=False,
        aggregation_bucket_seconds=60, default_window_seconds=300, default_min_samples=5, alert_resolve_after_seconds=300,
    )
    return create_app(settings, broker=broker, db=db)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        c.app.state.processor.clock = lambda: BASE_TIME + __import__("datetime").timedelta(seconds=120)
        yield c


@pytest.fixture
def processor(client):
    return client.app.state.processor


@pytest.fixture
def envelope():
    from obs_common.schemas import MetricPayload, build_envelope
    from obs_common.timeutil import utcnow

    counter = {"n": 0}

    def _wrap(payload: dict, event_id: str | None = None) -> dict:
        counter["n"] += 1
        model = MetricPayload.model_validate(payload)
        return build_envelope("metric", model, event_id or model.event_id or f"metric-{counter['n']}", utcnow())

    return _wrap
