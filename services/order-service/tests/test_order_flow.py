import time

from sqlalchemy import func, select


from obs_common.testing import INTERNAL_TOKEN
from payment_service.models import Payment

H = {"X-Internal-Token": INTERNAL_TOKEN, "X-User-Role": "operator", "X-User-Name": "tester"}
BODY = {"customer_id": "cust-1", "items": [{"sku": "SKU-1", "quantity": 2, "unit_price": 10.5}]}


def post(stack, scenario=None, body=None, headers=None):
    h = {**H, **(headers or {})}
    if scenario:
        h["X-Scenario"] = scenario
    return stack.client.post("/api/v1/orders", json=body or BODY, headers=h)


def payments(stack):
    with stack.pay_db.session() as s:
        return s.scalar(select(func.count()).select_from(Payment))


def test_normal_flow_completes(make_stack):
    s = make_stack()
    r = post(s)
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["status"] == "completed" and d["total_amount"] == 21.0 and d["notification_status"] == "sent" and d["payment_id"]
    assert s.client.get(f"/api/v1/orders/{d['order_id']}", headers=H).json()["data"]["items"][0]["sku"] == "SKU-1"
    assert s.client.get("/api/v1/orders", headers=H).json()["meta"]["total"] == 1


def test_slow_payment_succeeds_but_is_slow(make_stack):
    s = make_stack(payment={"slow_payment_ms": 250})
    t0 = time.monotonic()
    r = post(s, "slow_payment")
    assert r.status_code == 201 and time.monotonic() - t0 >= 0.25


def test_payment_failure_fails_order_and_names_payment_service(make_stack):
    s = make_stack()
    r = post(s, "payment_failure")
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["source"] == "downstream" and err["service"] == "payment-service" and err["details"]["order_id"]
    order = s.client.get(f"/api/v1/orders/{err['details']['order_id']}", headers=H).json()["data"]
    assert order["status"] == "payment_failed" and order["payment_id"] is None and payments(s) == 0


def test_payment_timeout_maps_to_504(make_stack):
    s = make_stack(order={"payment_timeout_seconds": 0.1}, payment={"payment_timeout_sleep_ms": 400})
    r = post(s, "payment_timeout")
    assert r.status_code == 504 and r.json()["error"]["code"] == "payment_timeout"


def test_notification_timeout_is_not_fatal(make_stack):
    s = make_stack(order={"notification_timeout_seconds": 0.1}, notification={"notification_sleep_ms": 400})
    r = post(s, "notification_timeout")
    assert r.status_code == 201
    assert r.json()["data"]["status"] == "completed" and r.json()["data"]["notification_status"] == "failed"
    assert r.json()["warnings"][0]["code"] == "notification_failed"


def test_invalid_downstream_response_is_rejected(make_stack):
    s = make_stack()
    r = post(s, "invalid_downstream_response")
    assert r.status_code == 502 and r.json()["error"]["code"] == "invalid_downstream_response"
    oid = r.json()["error"]["details"]["order_id"]
    assert s.client.get(f"/api/v1/orders/{oid}", headers=H).json()["data"]["status"] == "failed"


def test_intermittent_unavailability_is_retried_with_same_idempotency_key(make_stack):
    s = make_stack()
    r1 = post(s, "intermittent_unavailability")  # call 1 -> 503, retry (call 2) succeeds
    assert r1.status_code == 201 and r1.json()["data"]["status"] == "completed"
    assert payments(s) == 1
    r2 = post(s, "intermittent_unavailability")  # call 3 -> 503, call 4 ok
    assert r2.status_code == 201 and payments(s) == 2


def test_intermittent_gives_up_after_max_attempts(make_stack):
    s = make_stack(order={"payment_max_attempts": 1})
    r = post(s, "intermittent_unavailability")
    assert r.status_code == 503 and r.json()["error"]["service"] == "payment-service"


def test_payment_is_idempotent_per_key(make_stack):
    s = make_stack()
    body = {"order_id": 5, "amount": 10, "currency": "USD", "idempotency_key": "order-5-payment"}
    first = s.client.app.state.payment  # order's client; call payment app directly instead
    from fastapi.testclient import TestClient
    pay = TestClient(s.pay_app)
    a = pay.post("/api/v1/payments", json=body, headers=H)
    b = pay.post("/api/v1/payments", json=body, headers=H)
    assert a.status_code == 201 and b.status_code == 200 and a.json()["data"]["payment_id"] == b.json()["data"]["payment_id"]
    assert payments(s) == 1


def test_notification_is_deduplicated_per_order(make_stack):
    from fastapi.testclient import TestClient
    s = make_stack()
    n = TestClient(s.notif_app)
    body = {"order_id": 9, "recipient": "x", "channel": "email", "message": "hi"}
    a, b = n.post("/api/v1/notifications", json=body, headers=H), n.post("/api/v1/notifications", json=body, headers=H)
    assert a.status_code == 201 and b.status_code == 200 and a.json()["data"]["notification_id"] == b.json()["data"]["notification_id"]


def test_unknown_scenario_and_validation_and_auth(make_stack):
    s = make_stack()
    assert post(s, "chaos_monkey").status_code == 422
    assert post(s, body={"customer_id": "c", "items": []}).status_code == 422
    assert post(s, body={"customer_id": "c", "items": [{"sku": "A", "quantity": 0, "unit_price": 1}]}).status_code == 422
    assert post(s, body={"customer_id": "bad id!", "items": BODY["items"]}).status_code == 422
    assert s.client.post("/api/v1/orders", json=BODY).status_code == 401
    assert post(s, headers={"X-User-Role": "viewer"}).status_code == 403
    assert s.client.get("/api/v1/orders/9999", headers=H).status_code == 404


def test_default_scenario_from_environment(make_stack):
    s = make_stack(order={"default_scenario": "payment_failure"}, payment={"default_scenario": "payment_failure"})
    r = s.client.post("/api/v1/orders", json=BODY, headers=H)  # no header: env decides
    assert r.status_code == 502


def test_scenario_catalogue(make_stack):
    s = make_stack()
    names = {x["name"] for x in s.client.get("/api/v1/scenarios", headers=H).json()["data"]}
    assert names == {"normal", "slow_payment", "payment_failure", "payment_timeout", "notification_timeout", "invalid_downstream_response", "intermittent_unavailability"}
