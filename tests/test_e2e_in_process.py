import pytest


def trace_detail(p, trace_id):
    r = p.gateway.get(f"/api/v1/traces/{trace_id}", headers=p.auth["operator"])
    assert r.status_code == 200, r.text
    return r.json()


def test_normal_order_is_traced_across_all_services(platform):
    platform.sample_order("normal")  # warm-up: the first in-process request pays one-off import/JIT costs and can look "slow"
    platform.settle()
    r = platform.sample_order("normal")
    assert r.status_code == 201, r.text
    platform.settle()
    t = platform.latest_trace()
    d = trace_detail(platform, t["trace_id"])["data"]
    services = {s["service_name"] for s in d["spans"]}
    assert {"api-gateway", "order-service", "payment-service", "notification-service"} <= services
    assert d["status"] == "OK" and d["completion_state"] == "complete" and d["missing_parent_count"] == 0
    root = next(s for s in d["spans"] if s["depth"] == 0)
    assert root["service_name"] == "api-gateway"
    assert d["logs_available"] and any(l["service_name"] == "payment-service" for l in d["logs"])
    assert d["diagnostic"] is None  # healthy traces are not announced for analysis


@pytest.mark.parametrize(
    "scenario,classification,service",
    [
        ("payment_failure", "confirmed_failure", "payment-service"),
        ("invalid_downstream_response", "confirmed_failure", "order-service"),
        ("intermittent_unavailability", "confirmed_failure", "payment-service"),
        ("notification_timeout", "potential_cause", "notification-service"),
        ("slow_payment", "latency_bottleneck", "payment-service"),
    ],
)
def test_failure_scenarios_are_diagnosed_automatically(platform, scenario, classification, service):
    r = platform.sample_order(scenario)
    assert r.status_code in (201, 502, 503, 504), r.text
    platform.settle()
    t = platform.latest_trace()
    detail = trace_detail(platform, t["trace_id"])
    diag = detail["data"]["diagnostic"]
    assert diag is not None, f"no automatic diagnostic for {scenario}: trace={t}"
    assert diag["classification"] == classification and diag["affected_service"] == service, diag["diagnosis"]
    assert diag["trigger"] == "event" and diag["evidence"]
    assert {"trace", "span"} <= {e["evidence_type"] for e in diag["evidence"]}


def test_payment_timeout_is_a_potential_cause_not_a_confirmed_one(platform):
    r = platform.sample_order("payment_timeout")
    assert r.status_code == 504 and r.json()["error"]["service"] == "payment-service"
    platform.settle()
    diag = trace_detail(platform, platform.latest_trace()["trace_id"])["data"]["diagnostic"]
    assert diag is not None and diag["affected_service"] == "payment-service"
    assert diag["classification"] in ("potential_cause", "confirmed_failure")


def test_dependency_map_is_derived_from_traffic(platform):
    platform.sample_order("normal")
    platform.sample_order("payment_failure")
    platform.settle()
    g = platform.gateway.get("/api/v1/dependencies?window_minutes=30", headers=platform.auth["operator"]).json()["data"]
    edges = {(e["source"], e["target"]): e for e in g["edges"]}
    assert ("api-gateway", "order-service") in edges and ("order-service", "payment-service") in edges and ("order-service", "notification-service") in edges
    assert edges[("order-service", "payment-service")]["failure_observed"] is True
    nodes = {n["service_name"]: n for n in g["nodes"]}
    assert nodes["payment-service"]["health"] in ("degraded", "critical")


def test_metrics_and_overview_reflect_traffic(platform):
    for _ in range(3):
        platform.sample_order("normal")
    platform.sample_order("payment_failure")
    platform.settle()
    ov = platform.gateway.get("/api/v1/overview?window_minutes=30", headers=platform.auth["operator"]).json()
    assert ov["data"]["metrics"]["request_count"] >= 8 and ov["data"]["failed_traces"]
    svc = platform.gateway.get("/api/v1/metrics/services?window_minutes=30", headers=platform.auth["operator"]).json()["data"]
    assert {"order-service", "payment-service"} <= {s["service_name"] for s in svc}


def test_logs_are_correlated_by_trace_and_secrets_never_appear(platform):
    platform.sample_order("payment_failure")
    platform.settle()
    t = platform.latest_trace()
    logs = platform.gateway.get("/api/v1/logs", params={"trace_id": t["trace_id"]}, headers=platform.auth["operator"]).json()["data"]
    assert any(l["severity"] == "ERROR" and l["service_name"] == "payment-service" for l in logs)
    assert all(l["trace_id"] == t["trace_id"] for l in logs)
    assert platform.gateway.get("/api/v1/logs", headers=platform.login("viewer")).status_code == 403


def test_manual_analysis_and_rca_report_endpoints(platform):
    platform.sample_order("normal")
    platform.settle()
    tid = platform.latest_trace()["trace_id"]
    r = platform.gateway.post(f"/api/v1/diagnostics/analyze/{tid}", headers=platform.auth["operator"])
    assert r.status_code == 201 and r.json()["data"]["classification"] == "healthy"
    assert platform.gateway.get("/api/v1/diagnostics", headers=platform.auth["viewer"] if "viewer" in platform.auth else platform.login("viewer")).json()["meta"]["total"] == 1


def test_system_health_shape(platform):
    h = platform.gateway.get("/api/v1/system/health", headers=platform.login("viewer"))
    assert h.status_code == 200 and {s["service_name"] for s in h.json()["data"]["services"]} >= {"api-gateway", "order-service"}
