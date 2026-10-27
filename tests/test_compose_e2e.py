"""End-to-end test against a running docker compose stack.

    docker compose up -d --build
    BASE_URL=http://localhost:8000 E2E_USER=operator E2E_PASSWORD=... pytest -m compose
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

pytestmark = pytest.mark.compose
BASE_URL = os.getenv("BASE_URL")

if not BASE_URL:
    pytest.skip("BASE_URL not set", allow_module_level=True)

ORDER = {"customer_id": "e2e", "items": [{"sku": "SKU-1", "quantity": 1, "unit_price": 10.0}], "currency": "USD"}


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=20) as c:
        r = c.post("/api/v1/auth/login", json={"username": os.getenv("E2E_USER", "operator"), "password": os.environ["E2E_PASSWORD"]})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['data']['access_token']}"
        yield c


def _eventually(fn, timeout=60):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(2)
    pytest.fail("condition not met in time")


def test_failure_scenario_is_traced_and_diagnosed(client):
    r = client.post("/api/v1/sample/orders", json=ORDER, headers={"X-Scenario": "payment_failure"})
    assert r.status_code == 502
    err = r.json()["error"]
    assert (err["source"], err["service"]) == ("downstream", "payment-service")
    cid = r.headers["x-correlation-id"]

    trace = _eventually(lambda: (client.get("/api/v1/traces", params={"correlation_id": cid}).json()["data"] or [None])[0])
    report = _eventually(lambda: (client.get("/api/v1/diagnostics", params={"trace_id": trace["trace_id"]}).json()["data"] or [None])[0])
    assert report["affected_service"] == "payment-service"
    assert report["classification"] == "confirmed_failure"
