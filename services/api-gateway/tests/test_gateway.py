import time

import httpx
import jwt

from obs_common.security import create_token
from obs_common.testing import INTERNAL_TOKEN



def test_login_success_and_me(client):
    r = client.post("/api/v1/auth/login", json={"username": "operator", "password": "operator-pass-1"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["token_type"] == "bearer" and d["user"] == {"username": "operator", "role": "operator"}
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {d['access_token']}"})
    assert me.json()["data"] == {"username": "operator", "role": "operator"}


def test_login_failures_do_not_leak_and_lock_out(client):
    for _ in range(3):
        r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials" and r.json()["error"]["source"] == "gateway"
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin-pass-1"})
    assert r.status_code == 429 and "Retry-After" in r.headers
    unknown = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "x"})
    assert unknown.status_code == 401 and unknown.json()["error"]["message"] == "Invalid username or password"


def test_login_validation(client):
    assert client.post("/api/v1/auth/login", json={"username": "", "password": "x"}).status_code == 422
    assert client.post("/api/v1/auth/login", json={"username": "a" * 500, "password": "x"}).status_code == 422
    assert client.post("/api/v1/auth/login", content=b"not json", headers={"Content-Type": "application/json"}).status_code == 422


def test_protected_routes_need_valid_token(client):
    assert client.get("/api/v1/traces").status_code == 401
    assert client.get("/api/v1/traces", headers={"Authorization": "Bearer garbage"}).status_code == 401
    forged = create_token("y" * 40, subject="admin", role="admin", ttl_seconds=60)
    assert client.get("/api/v1/traces", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    expired = jwt.encode({"sub": "admin", "exp": int(time.time()) - 10}, "x" * 40, algorithm="HS256")
    r = client.get("/api/v1/traces", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "token_expired"


def test_role_enforcement(client, auth):
    assert client.get("/api/v1/traces", headers=auth["viewer"]).status_code == 200
    assert client.get("/api/v1/logs", headers=auth["viewer"]).status_code == 403
    assert client.get("/api/v1/logs", headers=auth["operator"]).status_code == 200
    assert client.post("/api/v1/diagnostics/analyze/" + "a" * 32, headers=auth["viewer"]).status_code == 403
    assert client.post("/api/v1/diagnostics/analyze/" + "a" * 32, headers=auth["operator"]).status_code == 200
    assert client.patch("/api/v1/alerts/1", json={"status": "acknowledged"}, headers=auth["viewer"]).status_code == 403
    assert client.patch("/api/v1/alerts/1", json={"status": "acknowledged"}, headers=auth["operator"]).status_code == 200
    assert client.put("/api/v1/alerts/rules", json={}, headers=auth["operator"]).status_code == 403
    assert client.put("/api/v1/alerts/rules", json={}, headers=auth["admin"]).status_code == 200
    assert client.post("/api/v1/sample/orders", json={}, headers=auth["viewer"]).status_code == 403
    assert client.get("/api/v1/system/requests", headers=auth["operator"]).status_code == 403
    assert client.get("/api/v1/system/requests", headers=auth["admin"]).status_code == 200


def test_proxy_sets_identity_and_ignores_spoofed_headers(client, auth, upstreams):
    r = client.get("/api/v1/traces?status=ERROR&page=2", headers={**auth["viewer"], "X-User-Role": "admin", "X-User-Name": "evil", "X-Internal-Token": "bad"})
    assert r.status_code == 200 and r.json()["data"]["path"] == "/api/v1/query/traces"
    req = upstreams["query-service"].calls[-1]
    assert req.headers["x-user-role"] == "viewer" and req.headers["x-user-name"] == "viewer"
    assert req.headers["x-internal-token"] == INTERNAL_TOKEN
    assert dict(req.url.params) == {"status": "ERROR", "page": "2"}
    assert req.headers.get("x-correlation-id")


def test_path_params_and_alert_write_routing(client, auth, upstreams):
    client.get("/api/v1/traces/" + "ab" * 16, headers=auth["viewer"])
    assert upstreams["query-service"].calls[-1].url.path == "/api/v1/query/traces/" + "ab" * 16
    client.patch("/api/v1/alerts/7", json={"status": "resolved"}, headers=auth["operator"])
    req = upstreams["metrics-service"].calls[-1]
    assert req.method == "PATCH" and req.url.path == "/api/v1/alerts/7" and b"resolved" in req.content


def test_downstream_error_relayed_with_source(client, auth, upstreams):
    upstreams["query-service"].responder = lambda r: httpx.Response(404, json={"data": None, "error": {"code": "trace_not_found", "message": "nope", "source": "service", "service": "trace-service"}})
    r = client.get("/api/v1/traces/" + "0" * 32, headers=auth["viewer"])
    err = r.json()["error"]
    assert r.status_code == 404 and err["code"] == "trace_not_found" and err["source"] == "downstream" and err["service"] == "trace-service"


def test_downstream_failures_are_mapped(client, auth, upstreams):
    up = upstreams["query-service"]
    up.responder = lambda r: httpx.ReadTimeout("slow")
    r = client.get("/api/v1/traces", headers=auth["viewer"])
    assert r.status_code == 504 and r.json()["error"]["code"] == "downstream_timeout" and r.json()["error"]["service"] == "query-service"
    up.responder = lambda r: httpx.ConnectError("refused")
    r = client.get("/api/v1/traces", headers=auth["viewer"])
    assert r.status_code == 503 and r.json()["error"]["source"] == "downstream"
    up.responder = lambda r: httpx.Response(500, text="<html>boom</html>")
    r = client.get("/api/v1/traces", headers=auth["viewer"])
    assert r.status_code == 502 and "boom" not in r.text


def test_sample_scenario_header_validated_and_forwarded(client, auth, upstreams):
    r = client.post("/api/v1/sample/orders", json={"amount": 10}, headers={**auth["operator"], "X-Scenario": "payment_failure"})
    assert r.status_code == 200
    req = upstreams["order-service"].calls[-1]
    assert req.url.path == "/api/v1/orders" and req.headers["x-scenario"] == "payment_failure"
    bad = client.post("/api/v1/sample/orders", json={}, headers={**auth["operator"], "X-Scenario": "x; drop"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_scenario"


def test_sample_post_is_never_retried(client, auth, upstreams):
    upstreams["order-service"].responder = lambda r: httpx.Response(503, json={"data": None, "error": {"code": "unavailable", "message": "x", "source": "service", "service": "order-service"}})
    r = client.post("/api/v1/sample/orders", json={}, headers=auth["operator"])
    assert r.status_code == 503 and len(upstreams["order-service"].calls) == 1


def test_get_requests_retry_once_on_transient_5xx(client, auth, upstreams):
    seq = iter([httpx.Response(503, json={"error": {"code": "x"}}), httpx.Response(200, json={"data": [], "error": None})])
    upstreams["query-service"].responder = lambda r: next(seq)
    assert client.get("/api/v1/traces", headers=auth["viewer"]).status_code == 200
    assert len(upstreams["query-service"].calls) == 2


def test_system_health_reports_down_service(client, auth, app):
    app.state.health_clients["payment-service"].client._transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x")))
    r = client.get("/api/v1/system/health", headers=auth["viewer"])
    data = r.json()["data"]
    by = {s["service_name"]: s for s in data["services"]}
    assert data["status"] == "down" and by["payment-service"]["status"] == "down" and by["api-gateway"]["status"] == "up"
    assert len(data["services"]) == 10


def test_requests_are_audited_without_bodies(client, auth):
    client.get("/api/v1/traces", headers=auth["viewer"])
    rows = client.get("/api/v1/system/requests", headers=auth["admin"]).json()["data"]
    assert rows[0]["path"] == "/api/v1/traces" and rows[0]["username"] == "viewer" and rows[0]["upstream"] == "query-service" and rows[0]["status_code"] == 200


def test_only_sample_requests_are_traced(client, auth, app, spans):
    client.get("/api/v1/traces", headers=auth["viewer"])
    client.post("/api/v1/sample/orders", json={}, headers=auth["operator"])
    app.state.telemetry.flush()
    span_events = [e for k, e in spans if k == "traces"]
    assert span_events and all("/api/v1/sample" in e["operation_name"] or "sample" in str(e["attributes"]) for e in span_events if e["kind"] == "SERVER")
    assert not any("/api/v1/traces" in str(e["attributes"]) for e in span_events)
    assert {e["service_name"] for e in span_events} == {"api-gateway"}
    assert any(e["kind"] == "CLIENT" and e.get("attributes", {}).get("peer.service") == "order-service" for e in span_events)


def test_health_and_security_headers(client):
    assert client.get("/health").status_code == 200
    r = client.get("/ready")
    assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/v1/unknown").status_code == 404
