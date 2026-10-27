"""Builds the real trace, log and metrics services in-process and wires the RCA service to them."""
import pytest
from fastapi.testclient import TestClient

from obs_common.broker import InMemoryBroker
from obs_common.db import Database
from obs_common.schemas import LogPayload, MetricPayload, SpanPayload, build_envelope
from obs_common.testing import INTERNAL_TOKEN, TestClientTransport
from obs_common.timeutil import utcnow

from log_service.config import Settings as LogSettings
from log_service.main import create_app as create_log
from log_service.models import Base as LogBase
from metrics_service.config import Settings as MetricsSettings
from metrics_service.main import create_app as create_metrics
from metrics_service.models import Base as MetricsBase
from rca_service.config import Settings
from rca_service.main import create_app
from rca_service.models import Base
from trace_service.config import Settings as TraceSettings
from trace_service.main import create_app as create_trace
from trace_service.models import Base as TraceBase

COMMON = dict(internal_api_token=INTERNAL_TOKEN, broker_url="memory://")


def _db(base):
    d = Database("sqlite://")
    d.create_all(base)
    return d


@pytest.fixture
def broker():
    b = InMemoryBroker()
    b.declare_topology()
    return b


@pytest.fixture
def stack(broker):
    trace_app = create_trace(TraceSettings(database_url="sqlite://", run_background_workers=False, trace_completion_wait_seconds=10, **COMMON), broker=InMemoryBroker(), db=_db(TraceBase))
    log_app = create_log(LogSettings(database_url="sqlite://", **COMMON), broker=InMemoryBroker(), db=_db(LogBase))
    metrics_app = create_metrics(MetricsSettings(database_url="sqlite://", run_background_workers=False, **COMMON), broker=InMemoryBroker(), db=_db(MetricsBase))
    with TestClient(trace_app) as t, TestClient(log_app) as l, TestClient(metrics_app) as m:
        transports = {"trace-service": TestClientTransport(t), "log-service": TestClientTransport(l), "metrics-service": TestClientTransport(m)}
        rca = create_app(
            Settings(database_url="sqlite://", trace_service_url="http://trace", log_service_url="http://log", metrics_service_url="http://metrics",
                     downstream_timeout_seconds=1.0, **COMMON),
            broker=broker, db=_db(Base), transports=transports)
        with TestClient(rca) as r:
            yield type("Stack", (), {"trace": t, "log": l, "metrics": m, "rca": r, "broker": broker, "transports": transports})


@pytest.fixture
def seed(stack):
    """Seed helpers that push events straight into each service's processor (as the broker consumers would)."""
    now = utcnow()

    def spans(events):
        for e in events:
            m = SpanPayload.model_validate(e)
            stack.trace.app.state.processor.handle_event(build_envelope("trace", m, m.default_event_id(), now))
        stack.trace.post("/api/v1/traces/_finalize", headers={"X-Internal-Token": INTERNAL_TOKEN, "X-User-Role": "admin"})

    def logs(events):
        for i, e in enumerate(events):
            m = LogPayload.model_validate(e)
            stack.log.app.state.processor.handle_event(build_envelope("log", m, e.get("event_id") or f"log-{i}", now))

    def metrics(events):
        for i, e in enumerate(events):
            m = MetricPayload.model_validate(e)
            stack.metrics.app.state.processor.handle_event(build_envelope("metric", m, e.get("event_id") or f"m-{i}", now))
        stack.metrics.post("/api/v1/metrics/_flush", headers={"X-Internal-Token": INTERNAL_TOKEN, "X-User-Role": "admin"})

    return type("Seed", (), {"spans": staticmethod(spans), "logs": staticmethod(logs), "metrics": staticmethod(metrics)})
