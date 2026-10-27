"""OpenTelemetry setup for instrumented applications (sample services, gateway).

* Traces: OTel SDK -> ``IngestionSpanExporter`` -> Telemetry Ingestion Service (HTTP, batch).
* Logs:   ``TelemetryLogHandler`` ships structured log records (with trace_id/span_id/correlation_id).
* Metrics: ``MetricsMiddleware`` ships one ``request.duration_ms`` sample per request.

The shipping path uses ``urllib`` (not httpx) on purpose: the httpx instrumentation would otherwise
create spans for the exporter's own calls and feed back into itself.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol

import httpx
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import StatusCode

from .context import get_correlation_id
from .redact import redact_text
from .timeutil import to_iso, utcnow

log = logging.getLogger("obs_common.telemetry")


class TelemetrySink(Protocol):
    def send(self, kind: str, events: list[dict[str, Any]]) -> bool: ...


class HttpSink:
    """Posts telemetry batches to the Telemetry Ingestion Service."""

    def __init__(self, ingestion_url: str, api_key: str, timeout: float = 3.0):
        self.base = ingestion_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def send(self, kind: str, events: list[dict[str, Any]]) -> bool:
        if not events:
            return True
        url = f"{self.base}/api/v1/telemetry/{kind}"
        data = json.dumps({"events": events}).encode()
        req = urllib.request.Request(
            url, data=data, method="POST", headers={"Content-Type": "application/json", "X-API-Key": self.api_key}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - internal URL from env
                return 200 <= resp.status < 300
        except Exception as exc:  # noqa: BLE001
            # Never use the instrumented logger here (avoid feedback loops).
            logging.getLogger("obs_common.telemetry.shipper").debug("telemetry send failed: %r", exc)
            return False


class CallableSink:
    """Sink that hands batches to a Python callable (used by in-process tests)."""

    def __init__(self, fn: Callable[[str, list[dict[str, Any]]], bool | None]):
        self.fn = fn

    def send(self, kind: str, events: list[dict[str, Any]]) -> bool:
        return self.fn(kind, events) is not False


# ---------------------------------------------------------------- spans


def _hex(value: int, width: int) -> str:
    return format(value, f"0{width}x")


def _ns_to_iso(ns: int) -> str:
    from datetime import datetime, timedelta

    return to_iso(datetime(1970, 1, 1) + timedelta(microseconds=ns // 1000))  # type: ignore[arg-type]


def span_to_event(span: ReadableSpan) -> dict[str, Any]:
    ctx = span.get_span_context()
    attrs = {
        k: v for k, v in (span.attributes or {}).items() if isinstance(v, (str, int, float, bool)) and not k.startswith("http.request.header")
    }
    status = "ERROR" if span.status.status_code == StatusCode.ERROR else "OK"
    error_message = span.status.description or None
    if status == "ERROR":
        for event in span.events:
            if event.name == "exception":
                ev = event.attributes or {}
                error_message = error_message or f"{ev.get('exception.type', 'Exception')}: {ev.get('exception.message', '')}".strip()
        if not error_message:
            code = attrs.get("http.status_code") or attrs.get("http.response.status_code")
            error_message = f"HTTP {code}" if code else "error"
    service = str((span.resource.attributes or {}).get("service.name", "unknown-service"))
    event: dict[str, Any] = {
        "trace_id": _hex(ctx.trace_id, 32),
        "span_id": _hex(ctx.span_id, 16),
        "parent_span_id": _hex(span.parent.span_id, 16) if span.parent else None,
        "service_name": service,
        "operation_name": span.name,
        "kind": span.kind.name,
        "start_time": _ns_to_iso(span.start_time),
        "end_time": _ns_to_iso(span.end_time),
        "status": status,
        "error_message": error_message,
        "attributes": attrs,
    }
    if "correlation_id" in attrs:
        event["correlation_id"] = attrs["correlation_id"]
    return event


class IngestionSpanExporter(SpanExporter):
    def __init__(self, sink: TelemetrySink):
        self.sink = sink

    def export(self, spans: Iterable[ReadableSpan]) -> SpanExportResult:
        events = [span_to_event(s) for s in spans]
        return SpanExportResult.SUCCESS if self.sink.send("traces", events) else SpanExportResult.FAILURE

    def shutdown(self) -> None:  # noqa: D401
        return None


# ---------------------------------------------------------------- batching shipper


class BatchShipper:
    """Bounded queue + background flusher. Drops (and counts) events when full; never blocks the app."""

    def __init__(self, kind: str, sink: TelemetrySink, *, flush_interval: float = 1.0, batch_size: int = 50, max_queue: int = 5000):
        self.kind = kind
        self.sink = sink
        self.flush_interval = flush_interval
        self.batch_size = batch_size
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max_queue)
        self.dropped = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"shipper-{kind}", daemon=True)
        self._thread.start()

    def add(self, event: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            self.dropped += 1

    def flush(self) -> None:
        while True:
            batch: list[dict[str, Any]] = []
            while len(batch) < self.batch_size:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            if not batch:
                return
            if not self.sink.send(self.kind, batch):
                self.dropped += len(batch)  # bounded: no unbounded re-queueing

    def _run(self) -> None:
        while not self._stop.wait(self.flush_interval):
            try:
                self.flush()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        self._stop.set()
        try:
            self.flush()
        except Exception:  # noqa: BLE001
            pass


class TelemetryLogHandler(logging.Handler):
    """Ships log records (with trace/span/correlation ids) to the ingestion service."""

    def __init__(self, shipper: BatchShipper, service_name: str):
        super().__init__(level=logging.INFO)
        self.shipper = shipper
        self.service_name = service_name

    def emit(self, record: logging.LogRecord) -> None:
        try:
            ctx = trace.get_current_span().get_span_context()
            attributes = {
                k: v
                for k, v in record.__dict__.items()
                if k not in logging.LogRecord("", 0, "", 0, "", (), None).__dict__ and k not in ("message", "asctime", "taskName")
                and isinstance(v, (str, int, float, bool))
            }
            if record.exc_info and record.exc_info[0] is not None:
                attributes["exception"] = f"{record.exc_info[0].__name__}: {record.exc_info[1]}"[:500]
            event: dict[str, Any] = {
                "service_name": self.service_name,
                "timestamp": to_iso(_from_epoch(record.created)),
                "severity": record.levelname,
                "message": redact_text(record.getMessage()),
                "logger": record.name,
                "attributes": attributes,
                "event_id": f"log-{uuid.uuid4().hex}",
            }
            if ctx.is_valid:
                event["trace_id"] = _hex(ctx.trace_id, 32)
                event["span_id"] = _hex(ctx.span_id, 16)
            cid = get_correlation_id()
            if cid:
                event["correlation_id"] = cid
            self.shipper.add(event)
        except Exception:  # noqa: BLE001
            self.handleError(record)


def _from_epoch(seconds: float):
    from datetime import datetime, timedelta

    return datetime(1970, 1, 1) + timedelta(seconds=seconds)


# ---------------------------------------------------------------- metrics middleware


class MetricsMiddleware:
    """Emits one ``request.duration_ms`` sample per handled request."""

    def __init__(self, app, *, service_name: str, shipper: BatchShipper, include_prefixes: tuple[str, ...]):
        self.app = app
        self.service_name = service_name
        self.shipper = shipper
        self.include_prefixes = include_prefixes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(self.include_prefixes):
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            ctx = trace.get_current_span().get_span_context()
            route = scope.get("route")
            labels: dict[str, Any] = {
                "route": getattr(route, "path", scope["path"]),
                "method": scope.get("method", "GET"),
                "status_code": status["code"],
                "error": status["code"] >= 500,
            }
            event: dict[str, Any] = {
                "service_name": self.service_name,
                "metric_name": "request.duration_ms",
                "value": round(elapsed_ms, 3),
                "unit": "ms",
                "timestamp": to_iso(utcnow()),
                "labels": labels,
                "event_id": f"metric-{uuid.uuid4().hex}",
            }
            if ctx.is_valid:
                event["trace_id"] = _hex(ctx.trace_id, 32)
            cid = get_correlation_id()
            if cid:
                event["correlation_id"] = cid
            self.shipper.add(event)


# ---------------------------------------------------------------- setup


@dataclass
class Telemetry:
    service_name: str
    provider: TracerProvider
    tracer: trace.Tracer
    span_shipper_flush: Callable[[], None]
    log_shipper: BatchShipper
    metric_shipper: BatchShipper
    log_handler: TelemetryLogHandler
    logger_name: str

    def instrument_client(self, client) -> None:
        """Instrument an httpx client with *this* service's tracer provider (context propagation + peer.service)."""

        def request_hook(span, request):  # sync + async compatible (receives httpx.Request or raw tuple)
            try:
                url = getattr(request, "url", None)
                host = getattr(url, "host", None)
                if host is None and isinstance(request, tuple):
                    host = request[1].host.decode() if hasattr(request[1], "host") else None
                    host = host or None
                if host:
                    span.set_attribute("peer.service", host if isinstance(host, str) else host.decode())
            except Exception:  # noqa: BLE001
                pass

        async def async_request_hook(span, request):
            request_hook(span, request)

        # the instrumentation dispatches on the hook type, so an AsyncClient needs a coroutine function
        hook = async_request_hook if isinstance(client, httpx.AsyncClient) else request_hook
        HTTPXClientInstrumentor.instrument_client(client, tracer_provider=self.provider, request_hook=hook)

    def flush(self) -> None:
        self.span_shipper_flush()
        self.log_shipper.flush()
        self.metric_shipper.flush()

    def shutdown(self) -> None:
        try:
            self.provider.shutdown()
        finally:
            self.log_shipper.close()
            self.metric_shipper.close()
            logging.getLogger(self.logger_name).removeHandler(self.log_handler)


def setup_telemetry(
    app,
    *,
    service_name: str,
    sink: TelemetrySink,
    logger_name: str,
    span_processor: str = "batch",
    include_prefixes: tuple[str, ...] = ("/api/",),
    excluded_urls: str = "health,ready,docs,openapi.json,redoc",
    flush_interval: float = 1.0,
) -> Telemetry:
    """Instrument a FastAPI app. ``span_processor='simple'`` exports synchronously (tests)."""
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    exporter = IngestionSpanExporter(sink)
    if span_processor == "simple":
        processor: Any = SimpleSpanProcessor(exporter)
    else:
        processor = BatchSpanProcessor(
            exporter, schedule_delay_millis=int(flush_interval * 1000), max_export_batch_size=128, max_queue_size=4096
        )
    provider.add_span_processor(processor)

    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=provider, excluded_urls=excluded_urls, exclude_spans=["receive", "send"]
    )

    log_shipper = BatchShipper("logs", sink, flush_interval=flush_interval)
    metric_shipper = BatchShipper("metrics", sink, flush_interval=flush_interval)
    handler = TelemetryLogHandler(log_shipper, service_name)
    logging.getLogger(logger_name).addHandler(handler)
    app.add_middleware(MetricsMiddleware, service_name=service_name, shipper=metric_shipper, include_prefixes=include_prefixes)

    telemetry = Telemetry(
        service_name=service_name,
        provider=provider,
        tracer=provider.get_tracer(service_name),
        span_shipper_flush=lambda: provider.force_flush(),
        log_shipper=log_shipper,
        metric_shipper=metric_shipper,
        log_handler=handler,
        logger_name=logger_name,
    )
    app.state.telemetry = telemetry
    return telemetry
