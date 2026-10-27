"""Full in-process platform: gateway -> order -> payment -> notification, telemetry -> ingestion -> broker -> consumers -> query/RCA.

Everything real except the network (ASGI/TestClient transports), the database (SQLite) and the broker (InMemoryBroker).
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from obs_common.broker import InMemoryBroker
from obs_common.db import Database
from obs_common.telemetry import CallableSink
from obs_common.testing import INGEST_KEY, INTERNAL_TOKEN, DeadlineASGITransport, TestClientTransport

from api_gateway.config import Settings as GwSettings
from api_gateway.main import create_app as create_gateway
from api_gateway.models import Base as GwBase
from ingestion_service.config import Settings as IngSettings
from ingestion_service.main import create_app as create_ingestion
from ingestion_service.models import Base as IngBase
from log_service.config import Settings as LogSettings
from log_service.main import create_app as create_log
from log_service.models import Base as LogBase
from metrics_service.config import Settings as MetSettings
from metrics_service.main import create_app as create_metrics
from metrics_service.models import Base as MetBase
from notification_service.config import Settings as NotifSettings
from notification_service.main import create_app as create_notification
from notification_service.models import Base as NotifBase
from order_service.config import Settings as OrderSettings
from order_service.main import create_app as create_order
from order_service.models import Base as OrderBase
from payment_service.config import Settings as PaySettings
from payment_service.main import create_app as create_payment
from payment_service.models import Base as PayBase
from query_service.config import Settings as QuerySettings
from query_service.main import create_app as create_query
from rca_service.config import Settings as RcaSettings
from rca_service.main import create_app as create_rca
from rca_service.models import Base as RcaBase
from trace_service.config import Settings as TraceSettings
from trace_service.main import create_app as create_trace
from trace_service.models import Base as TraceBase

TOK = dict(internal_api_token=INTERNAL_TOKEN)
ADMIN = {"X-Internal-Token": INTERNAL_TOKEN, "X-User-Role": "admin", "X-User-Name": "e2e"}


def _db(base):
    d = Database("sqlite://")
    d.create_all(base)
    return d


class Platform:
    def __init__(self, gateway: TestClient, parts: dict):
        self.gateway, self.parts = gateway, parts
        self.broker: InMemoryBroker = parts["broker"]
        self.auth: dict = {}

    def login(self, user="operator", password=None):
        pw = password or {"admin": "admin-pass-1", "operator": "operator-pass-1", "viewer": "viewer-pass-1"}[user]
        r = self.gateway.post("/api/v1/auth/login", json={"username": user, "password": pw})
        assert r.status_code == 200, r.text
        self.auth[user] = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
        return self.auth[user]

    def settle(self):
        """Flush telemetry from every instrumented app, deliver all messages, apply the trace completion policy and deliver again."""
        for key in ("gateway_app", "order_app", "payment_app", "notification_app"):
            tel = self.parts[key].state.telemetry
            if tel:
                tel.flush()
        self.broker.drain()
        self.parts["trace"].post("/api/v1/traces/_finalize", headers=ADMIN)
        self.broker.drain()
        self.parts["metrics"].post("/api/v1/metrics/_flush", headers=ADMIN)

    def sample_order(self, scenario=None, user="operator", **kw):
        headers = dict(self.auth.get(user) or self.login(user))
        if scenario:
            headers["X-Scenario"] = scenario
        body = kw.get("body") or {"customer_id": "cust-42", "items": [{"sku": "SKU-1", "quantity": 2, "unit_price": 12.5}]}
        return self.gateway.post("/api/v1/sample/orders", json=body, headers=headers)

    def latest_trace(self, **params):
        h = self.auth.get("operator") or self.login("operator")
        r = self.gateway.get("/api/v1/traces", params={"page_size": 1, **params}, headers=h)
        assert r.status_code == 200, r.text
        items = r.json()["data"]
        assert items, "no trace found"
        return items[0]


@pytest.fixture
def platform():
    broker = InMemoryBroker()
    broker.declare_topology()

    ingestion = create_ingestion(IngSettings(database_url="sqlite://", broker_url="memory://", ingest_api_keys=INGEST_KEY, run_background_workers=False, **TOK),
                                 broker=broker, db=_db(IngBase))
    trace = create_trace(TraceSettings(database_url="sqlite://", broker_url="memory://", run_background_workers=False, slow_span_ms=250, **TOK), broker=broker, db=_db(TraceBase))
    logs = create_log(LogSettings(database_url="sqlite://", broker_url="memory://", **TOK), broker=broker, db=_db(LogBase))
    metrics = create_metrics(MetSettings(database_url="sqlite://", broker_url="memory://", run_background_workers=False, **TOK), broker=broker, db=_db(MetBase))

    clients = []

    def start(app):
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        return c

    ing_c, trace_c, log_c, met_c = start(ingestion), start(trace), start(logs), start(metrics)
    rca = create_rca(RcaSettings(database_url="sqlite://", broker_url="memory://", trace_service_url="http://trace-service", log_service_url="http://log-service",
                                 metrics_service_url="http://metrics-service", downstream_timeout_seconds=2.0, **TOK),
                     broker=broker, db=_db(RcaBase),
                     transports={"trace-service": TestClientTransport(trace_c), "log-service": TestClientTransport(log_c), "metrics-service": TestClientTransport(met_c)})
    rca_c = start(rca)
    query = create_query(QuerySettings(trace_service_url="http://trace-service", log_service_url="http://log-service", metrics_service_url="http://metrics-service",
                                       rca_service_url="http://rca-service", downstream_timeout_seconds=3.0, downstream_retries=0, **TOK),
                         transports={"trace-service": TestClientTransport(trace_c), "log-service": TestClientTransport(log_c),
                                     "metrics-service": TestClientTransport(met_c), "rca-service": TestClientTransport(rca_c)})

    def ship(kind, events):
        r = ing_c.post(f"/api/v1/telemetry/{kind}", json={"events": events}, headers={"X-API-Key": INGEST_KEY})
        return r.status_code < 300

    sink = CallableSink(ship)
    common = dict(**TOK, ingestion_service_url="http://ingestion-service", ingestion_api_key=INGEST_KEY)
    payment = create_payment(PaySettings(database_url="sqlite://", base_latency_ms=0, slow_payment_ms=300, payment_timeout_sleep_ms=900, **common), db=_db(PayBase), sink=sink)
    notification = create_notification(NotifSettings(database_url="sqlite://", base_latency_ms=0, notification_sleep_ms=900, **common), db=_db(NotifBase), sink=sink)
    order = create_order(OrderSettings(database_url="sqlite://", payment_service_url="http://payment-service", notification_service_url="http://notification-service",
                                       payment_timeout_seconds=0.4, notification_timeout_seconds=0.4, payment_retry_backoff_seconds=0.0, **common),
                         db=_db(OrderBase), sink=sink,
                         transports={"payment-service": DeadlineASGITransport(payment), "notification-service": DeadlineASGITransport(notification)})
    urls = {f"{n}_service_url": f"http://{n}-service" for n in ("query", "rca", "metrics", "ingestion", "trace", "log", "order", "payment", "notification")}
    gateway = create_gateway(
        GwSettings(database_url="sqlite://", jwt_secret="e" * 40, password_iterations=1000, seed_admin_password="admin-pass-1", seed_operator_password="operator-pass-1",
                   seed_viewer_password="viewer-pass-1", ingestion_api_key=INGEST_KEY, sample_timeout_seconds=8.0, **TOK, **urls),
        db=_db(GwBase), sink=sink,
        transports={"query-service": DeadlineASGITransport(query), "rca-service": DeadlineASGITransport(rca), "metrics-service": DeadlineASGITransport(metrics),
                    "order-service": DeadlineASGITransport(order)})
    gw_c = start(gateway)
    for app in (order, payment, notification):
        start(app)  # run their lifespans (telemetry shutdown on exit)
    yield Platform(gw_c, {"broker": broker, "gateway_app": gateway, "order_app": order, "payment_app": payment, "notification_app": notification,
                          "trace": trace_c, "logs": log_c, "metrics": met_c, "rca": rca_c, "ingestion": ing_c})
    for c in reversed(clients):
        c.__exit__(None, None, None)
