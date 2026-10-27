"""Gateway route table: public path -> upstream service/path with a minimum role.

Roles: viewer < operator < admin.  Anything not listed here is not exposed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    method: str
    path: str            # public path (FastAPI syntax)
    upstream: str        # client key
    target: str          # upstream path (format with path params)
    role: str = "viewer"
    summary: str = ""
    tag: str = "console"
    timeout_key: str = "default"


Q = "/api/v1/query"
ROUTES: tuple[Route, ...] = (
    Route("GET", "/api/v1/overview", "query", f"{Q}/overview", summary="Dashboard overview"),
    Route("GET", "/api/v1/traces", "query", f"{Q}/traces", summary="Search traces", tag="traces"),
    Route("GET", "/api/v1/traces/{trace_id}", "query", f"{Q}/traces/{{trace_id}}", summary="Trace detail with logs and diagnostic", tag="traces"),
    Route("GET", "/api/v1/logs", "query", f"{Q}/logs", "operator", "Search logs", "logs"),
    Route("GET", "/api/v1/logs/patterns", "query", f"{Q}/logs/patterns", "operator", "Repeated error patterns", "logs"),
    Route("GET", "/api/v1/dependencies", "query", f"{Q}/dependencies", summary="Dependency graph", tag="dependencies"),
    Route("GET", "/api/v1/dependencies/{service_name}", "query", f"{Q}/dependencies/{{service_name}}", summary="One service's dependencies", tag="dependencies"),
    Route("GET", "/api/v1/metrics/summary", "query", f"{Q}/metrics/summary", summary="Metrics summary", tag="metrics"),
    Route("GET", "/api/v1/metrics/services", "query", f"{Q}/metrics/services", summary="Per-service metrics", tag="metrics"),
    Route("GET", "/api/v1/metrics/timeseries", "query", f"{Q}/metrics/timeseries", summary="Time series", tag="metrics"),
    Route("GET", "/api/v1/metrics/latency", "query", f"{Q}/metrics/latency", summary="Latency percentiles", tag="metrics"),
    Route("GET", "/api/v1/alerts", "query", f"{Q}/alerts", summary="Alerts", tag="alerts"),
    Route("GET", "/api/v1/alerts/rules", "query", f"{Q}/alerts/rules", summary="Alert rules", tag="alerts"),
    Route("GET", "/api/v1/alerts/{alert_id}", "query", f"{Q}/alerts/{{alert_id}}", summary="One alert", tag="alerts"),
    Route("PATCH", "/api/v1/alerts/{alert_id}", "metrics", "/api/v1/alerts/{alert_id}", "operator", "Acknowledge / resolve an alert", "alerts"),
    Route("PUT", "/api/v1/alerts/rules", "metrics", "/api/v1/alerts/rules", "admin", "Create/update an alert rule", "alerts"),
    Route("GET", "/api/v1/diagnostics", "query", f"{Q}/diagnostics", summary="Root-cause reports", tag="diagnostics"),
    Route("GET", "/api/v1/diagnostics/{report_id}", "query", f"{Q}/diagnostics/{{report_id}}", summary="Root-cause report with evidence", tag="diagnostics"),
    Route("POST", "/api/v1/diagnostics/analyze/{trace_id}", "rca", "/api/v1/diagnostics/analyze/{trace_id}", "operator", "Run root-cause analysis for a trace", "diagnostics"),
    Route("GET", "/api/v1/sample/orders/{order_id}", "order", "/api/v1/orders/{order_id}", "operator", "Read a sample order", "sample", "sample"),
    Route("GET", "/api/v1/sample/orders", "order", "/api/v1/orders", "operator", "List sample orders", "sample", "sample"),
    Route("POST", "/api/v1/sample/orders", "order", "/api/v1/orders", "operator", "Create a sample order (supports X-Scenario)", "sample", "sample"),
    Route("GET", "/api/v1/sample/scenarios", "order", "/api/v1/scenarios", "operator", "List failure scenarios", "sample", "sample"),
)

SCENARIO_HEADER = "X-Scenario"
