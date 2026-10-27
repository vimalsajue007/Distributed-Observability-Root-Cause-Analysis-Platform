"""Helpers for in-process tests (kept in the library so every service can reuse them)."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient


class TestClientTransport(httpx.BaseTransport):
    """Lets the *sync* downstream client talk to an in-process FastAPI app (via TestClient)."""

    __test__ = False  # not a pytest class

    def __init__(self, client: TestClient):
        self.client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.client.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")},
        )
        return httpx.Response(response.status_code, headers=dict(response.headers), content=response.content)


class FailingTransport(httpx.BaseTransport, httpx.AsyncBaseTransport):
    """Transport that always raises (simulates timeouts / connection failures)."""

    def __init__(self, exc: Exception):
        self.exc = exc

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise self.exc

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise self.exc


class StatusTransport(httpx.BaseTransport, httpx.AsyncBaseTransport):
    """Transport that always answers with a fixed status/body (simulates HTTP 5xx)."""

    def __init__(self, status: int = 500, body: dict | None = None):
        self.status = status
        self.body = body if body is not None else {"error": {"code": "boom"}}
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(self.status, json=self.body)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(self.status, json=self.body)


class DeadlineASGITransport(httpx.AsyncBaseTransport):
    """In-process ASGI transport that honours the request's read timeout (ASGITransport itself never times out)."""

    def __init__(self, app):
        self.inner = httpx.ASGITransport(app=app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import asyncio

        read = (request.extensions.get("timeout") or {}).get("read")
        try:
            return await asyncio.wait_for(self.inner.handle_async_request(request), read)
        except asyncio.TimeoutError as exc:
            raise httpx.ReadTimeout("deadline exceeded", request=request) from exc


# ------------------------------------------------------------------ event factories for tests

from datetime import datetime, timedelta  # noqa: E402

from .timeutil import to_iso  # noqa: E402

INTERNAL_TOKEN = "test-internal-token"
INGEST_KEY = "test-ingest-key"
INTERNAL_HEADERS = {"X-Internal-Token": INTERNAL_TOKEN}

BASE_TIME = datetime(2026, 10, 16, 10, 0, 0)


def tid(n: int = 1) -> str:
    return f"{n:032x}"


def sid(n: int) -> str:
    return f"{n:016x}"


def span_event(
    trace: int | str,
    span: int,
    parent: int | None,
    service: str,
    operation: str,
    offset_ms: float,
    duration_ms: float,
    *,
    status: str = "OK",
    error: str | None = None,
    kind: str = "INTERNAL",
    peer: str | None = None,
    correlation_id: str | None = None,
    base: datetime = BASE_TIME,
    attributes: dict | None = None,
) -> dict:
    start = base + timedelta(milliseconds=offset_ms)
    end = start + timedelta(milliseconds=duration_ms)
    attrs = dict(attributes or {})
    if peer:
        attrs["peer.service"] = peer
    event = {
        "trace_id": tid(trace) if isinstance(trace, int) else trace,
        "span_id": sid(span),
        "parent_span_id": sid(parent) if parent else None,
        "service_name": service,
        "operation_name": operation,
        "kind": kind,
        "start_time": to_iso(start),
        "end_time": to_iso(end),
        "status": status,
        "error_message": error,
        "attributes": attrs,
    }
    if correlation_id:
        event["correlation_id"] = correlation_id
    return event


def log_event(service: str, severity: str, message, *, trace: int | str | None = None, span: int | None = None,
              offset_ms: float = 0, correlation_id: str | None = None, base: datetime = BASE_TIME, event_id: str | None = None) -> dict:
    event: dict = {"service_name": service, "severity": severity, "message": message, "timestamp": to_iso(base + timedelta(milliseconds=offset_ms))}
    if trace is not None:
        event["trace_id"] = tid(trace) if isinstance(trace, int) else trace
    if span is not None:
        event["span_id"] = sid(span)
    if correlation_id:
        event["correlation_id"] = correlation_id
    if event_id:
        event["event_id"] = event_id
    return event


def metric_event(service: str, value: float, *, offset_ms: float = 0, error: bool = False, metric: str = "request.duration_ms",
                 trace: int | str | None = None, event_id: str | None = None, base: datetime = BASE_TIME) -> dict:
    event: dict = {
        "service_name": service,
        "metric_name": metric,
        "value": value,
        "timestamp": to_iso(base + timedelta(milliseconds=offset_ms)),
        "labels": {"error": error, "status_code": 500 if error else 200},
    }
    if trace is not None:
        event["trace_id"] = tid(trace) if isinstance(trace, int) else trace
    if event_id:
        event["event_id"] = event_id
    return event
