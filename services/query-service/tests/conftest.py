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
from query_service.config import Settings
from query_service.main import create_app
from rca_service.config import Settings as RcaSettings
from rca_service.main import create_app as create_rca
from rca_service.models import Base as RcaBase
from trace_service.config import Settings as TraceSettings
from trace_service.main import create_app as create_trace
from trace_service.models import Base as TraceBase

COMMON = dict(internal_api_token=INTERNAL_TOKEN)


def _db(base):
    d = Database("sqlite://")
    d.create_all(base)
    return d


@pytest.fixture
def stack():
    mk = lambda: InMemoryBroker()  # noqa: E731
    trace_app = create_trace(TraceSettings(database_url="sqlite://", broker_url="memory://", run_background_workers=False, **COMMON), broker=mk(), db=_db(TraceBase))
    log_app = create_log(LogSettings(database_url="sqlite://", broker_url="memory://", **COMMON), broker=mk(), db=_db(LogBase))
    metrics_app = create_metrics(MetricsSettings(database_url="sqlite://", broker_url="memory://", run_background_workers=False, **COMMON), broker=mk(), db=_db(MetricsBase))
    with TestClient(trace_app) as t, TestClient(log_app) as l, TestClient(metrics_app) as m:
        rca_app = create_rca(
            RcaSettings(database_url="sqlite://", broker_url="memory://", trace_service_url="http://t", log_service_url="http://l", metrics_service_url="http://m", **COMMON),
            broker=mk(), db=_db(RcaBase),
            transports={"trace-service": TestClientTransport(t), "log-service": TestClientTransport(l), "metrics-service": TestClientTransport(m)})
        with TestClient(rca_app) as r:
            transports = {"trace-service": TestClientTransport(t), "log-service": TestClientTransport(l),
                          "metrics-service": TestClientTransport(m), "rca-service": TestClientTransport(r)}
            query = create_app(Settings(trace_service_url="http://t", log_service_url="http://l", metrics_service_url="http://m", rca_service_url="http://r",
                                        downstream_timeout_seconds=1.0, downstream_retries=0, **COMMON), transports=transports)
            with TestClient(query) as q:
                yield type("Stack", (), {"trace": t, "log": l, "metrics": m, "rca": r, "query": q, "query_app": query})


@pytest.fixture
def seed(stack):
    now = utcnow()

    def spans(events):
        for e in events:
            m = SpanPayload.model_validate(e)
            stack.trace.app.state.processor.handle_event(build_envelope("trace", m, m.default_event_id(), now))

    def logs(events):
        for i, e in enumerate(events):
            m = LogPayload.model_validate(e)
            stack.log.app.state.processor.handle_event(build_envelope("log", m, f"log-{i}", now))

    def metrics(events):
        for i, e in enumerate(events):
            m = MetricPayload.model_validate(e)
            stack.metrics.app.state.processor.handle_event(build_envelope("metric", m, f"m-{i}", now))
        stack.metrics.post("/api/v1/metrics/_flush", headers={"X-Internal-Token": INTERNAL_TOKEN, "X-User-Role": "admin"})

    return type("Seed", (), {"spans": staticmethod(spans), "logs": staticmethod(logs), "metrics": staticmethod(metrics)})
