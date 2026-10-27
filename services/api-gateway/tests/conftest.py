import httpx
import pytest
from fastapi.testclient import TestClient

from obs_common.db import Database
from obs_common.telemetry import CallableSink
from obs_common.testing import INTERNAL_TOKEN

from api_gateway.config import Settings
from api_gateway.main import create_app
from api_gateway.models import Base

URLS = {f"{n}_service_url": f"http://{n}-service" for n in ("query", "rca", "metrics", "ingestion", "trace", "log", "order", "payment", "notification")}


class Upstream:
    """Scriptable upstream: records requests, returns a canned envelope unless a responder is set."""

    def __init__(self, name):
        self.name, self.calls, self.responder = name, [], None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.responder:
            result = self.responder(request)
            if isinstance(result, Exception):
                raise result
            return result
        return httpx.Response(200, json={"data": {"from": self.name, "path": request.url.path}, "meta": None, "warnings": None, "error": None})


@pytest.fixture
def upstreams():
    return {n: Upstream(n) for n in ("query-service", "rca-service", "metrics-service", "order-service")}


@pytest.fixture
def spans():
    return []


@pytest.fixture
def app(upstreams, spans):
    settings = Settings(database_url="sqlite://", internal_api_token=INTERNAL_TOKEN, jwt_secret="x" * 40, password_iterations=1000,
                        seed_admin_password="admin-pass-1", seed_operator_password="operator-pass-1", seed_viewer_password="viewer-pass-1",
                        login_max_failures=3, login_lockout_seconds=60, ingestion_api_key="k", **URLS)
    db = Database("sqlite://")
    db.create_all(Base)
    transports = {n: httpx.MockTransport(u) for n, u in upstreams.items()}
    for n in ("ingestion-service", "trace-service", "log-service", "payment-service", "notification-service"):
        transports[n] = httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "ready", "checks": {"database": True}}))
    # health probes use the same transports; give metrics/query/rca/order a ready endpoint response
    for n, u in upstreams.items():
        transports[f"health:{n}"] = httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "ready", "checks": {"database": True}}))
    sink = CallableSink(lambda kind, events: spans.extend((kind, e) for e in events) or True)
    return create_app(settings, db=db, sink=sink, transports=transports)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def login(client, user="admin", pw=None):
    pw = pw or {"admin": "admin-pass-1", "operator": "operator-pass-1", "viewer": "viewer-pass-1"}[user]
    r = client.post("/api/v1/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


@pytest.fixture
def auth(client):
    return {role: login(client, role) for role in ("admin", "operator", "viewer")}
