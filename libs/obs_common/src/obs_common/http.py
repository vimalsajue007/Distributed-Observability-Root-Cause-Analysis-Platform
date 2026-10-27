"""Service-to-service HTTP clients with timeouts, bounded retries and exponential backoff.

Retries are only performed for idempotent requests (GET by default) and only for
timeouts / connection errors / HTTP 502-504, never for 4xx. Attempts are bounded and the total
time budget is capped so retry loops cannot occur.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Callable

import httpx

from .context import CORRELATION_HEADER, get_correlation_id

log = logging.getLogger("obs_common.http")

RETRY_STATUS = {502, 503, 504}


class DownstreamError(Exception):
    """A call to another service failed.

    kind: timeout | unavailable | http_5xx | http_4xx | bad_response
    """

    def __init__(self, service: str, kind: str, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(f"{service}: {kind}: {message}")
        self.service = service
        self.kind = kind
        self.message = message
        self.status_code = status_code
        self.payload = payload


def _headers(token: str | None, extra: dict[str, str] | None) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    cid = get_correlation_id()
    if cid:
        headers[CORRELATION_HEADER] = cid
    if extra:
        headers.update(extra)
    return headers


def _classify_exception(service: str, exc: Exception) -> DownstreamError:
    if isinstance(exc, httpx.TimeoutException):
        return DownstreamError(service, "timeout", f"request timed out ({type(exc).__name__})")
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError)):
        return DownstreamError(service, "unavailable", f"connection failed ({type(exc).__name__})")
    return DownstreamError(service, "unavailable", f"transport error ({type(exc).__name__})")


def _interpret(service: str, response: httpx.Response) -> dict[str, Any]:
    """Turn an HTTP response into a parsed JSON body or raise DownstreamError."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code >= 500:
        raise DownstreamError(service, "http_5xx", f"HTTP {response.status_code}", status_code=response.status_code, payload=body)
    if response.status_code >= 400:
        raise DownstreamError(service, "http_4xx", f"HTTP {response.status_code}", status_code=response.status_code, payload=body)
    if not isinstance(body, dict):
        raise DownstreamError(service, "bad_response", "response is not a JSON object", status_code=response.status_code)
    return body


def _sleep_time(backoff: float, attempt: int) -> float:
    return backoff * (2**attempt) + random.uniform(0, backoff / 2)


class DownstreamClient:
    """Async client (query service, gateway, sample apps)."""

    def __init__(
        self,
        name: str,
        base_url: str,
        *,
        timeout: float = 3.0,
        retries: int = 1,
        backoff: float = 0.2,
        max_total_seconds: float | None = None,
        internal_token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        instrument: Callable[[httpx.AsyncClient], None] | None = None,
    ):
        self.name = name
        self.retries = retries
        self.backoff = backoff
        self.token = internal_token
        self.max_total = max_total_seconds or (timeout * (retries + 1) + 1.0)
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=min(timeout, 2.0)),
            transport=transport,
            follow_redirects=False,
        )
        if instrument:
            instrument(self.client)

    async def send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        retry: bool | None = None,
    ) -> httpx.Response:
        """Send a request; returns the raw response (any status). Transport failures raise DownstreamError."""
        do_retry = (method.upper() == "GET") if retry is None else retry
        attempts = (self.retries if do_retry else 0) + 1
        started = time.monotonic()
        last: DownstreamError | None = None
        for attempt in range(attempts):
            try:
                response = await self.client.request(
                    method, path, params=params, json=json, content=content, headers=_headers(self.token, headers)
                )
                if response.status_code in RETRY_STATUS and attempt < attempts - 1:
                    last = DownstreamError(self.name, "http_5xx", f"HTTP {response.status_code}", status_code=response.status_code)
                else:
                    return response
            except httpx.HTTPError as exc:
                last = _classify_exception(self.name, exc)
                log.warning(
                    "downstream call failed",
                    extra={"downstream": self.name, "path": path, "kind": last.kind, "attempt": attempt + 1},
                )
            if attempt < attempts - 1:
                delay = _sleep_time(self.backoff, attempt)
                if time.monotonic() - started + delay > self.max_total:
                    break
                await asyncio.sleep(delay)
        assert last is not None
        raise last

    async def request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self.send(method, path, **kwargs)
        return _interpret(self.name, response)

    async def get(self, path: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        return await self.request_json("GET", path, params=params, headers=headers)

    async def aclose(self) -> None:
        await self.client.aclose()


class SyncDownstreamClient:
    """Blocking client (RCA service endpoints and broker consumer threads)."""

    def __init__(
        self,
        name: str,
        base_url: str,
        *,
        timeout: float = 3.0,
        retries: int = 1,
        backoff: float = 0.2,
        internal_token: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.name = name
        self.retries = retries
        self.backoff = backoff
        self.token = internal_token
        self.max_total = timeout * (retries + 1) + 1.0
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=min(timeout, 2.0)),
            transport=transport,
            follow_redirects=False,
        )

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        retry: bool | None = None,
    ) -> dict[str, Any]:
        do_retry = (method.upper() == "GET") if retry is None else retry
        attempts = (self.retries if do_retry else 0) + 1
        started = time.monotonic()
        last: DownstreamError | None = None
        for attempt in range(attempts):
            try:
                response = self.client.request(method, path, params=params, json=json, headers=_headers(self.token, headers))
                if response.status_code in RETRY_STATUS and attempt < attempts - 1:
                    last = DownstreamError(self.name, "http_5xx", f"HTTP {response.status_code}", status_code=response.status_code)
                else:
                    return _interpret(self.name, response)
            except httpx.HTTPError as exc:
                last = _classify_exception(self.name, exc)
                log.warning(
                    "downstream call failed",
                    extra={"downstream": self.name, "path": path, "kind": last.kind, "attempt": attempt + 1},
                )
            if attempt < attempts - 1:
                delay = _sleep_time(self.backoff, attempt)
                if time.monotonic() - started + delay > self.max_total:
                    break
                time.sleep(delay)
        assert last is not None
        raise last

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request_json("GET", path, params=params)

    def close(self) -> None:
        self.client.close()


def downstream_to_app_error(exc: DownstreamError):
    """Map a DownstreamError onto an AppError that clearly names the failing downstream service."""
    from .api import AppError

    if exc.kind == "timeout":
        return AppError(504, "downstream_timeout", f"{exc.service} did not respond in time", source="downstream", service=exc.service)
    if exc.kind == "unavailable":
        return AppError(503, "downstream_unavailable", f"{exc.service} is unavailable", source="downstream", service=exc.service)
    if exc.kind == "http_4xx":
        error = (exc.payload or {}).get("error") if isinstance(exc.payload, dict) else None
        code = (error or {}).get("code", "downstream_rejected")
        message = (error or {}).get("message", f"{exc.service} rejected the request")
        return AppError(exc.status_code or 400, code, message, (error or {}).get("details"), source="downstream", service=exc.service)
    return AppError(502, "downstream_error", f"{exc.service} returned an invalid or failing response", source="downstream", service=exc.service)
