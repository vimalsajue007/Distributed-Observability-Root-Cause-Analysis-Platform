"""Message broker abstraction.

* ``RabbitBroker``   - production implementation (RabbitMQ, pika).
* ``InMemoryBroker`` - deterministic implementation used by the test-suite; it applies the
                        *same* retry / dead-letter decision logic as the RabbitMQ consumer.

Delivery semantics (documented in README): **at-least-once**.
  * publisher confirms + persistent messages + durable queues on the producer side,
  * manual consumer acknowledgements *after* the handler committed its DB transaction,
  * bounded retries through TTL retry queues, then a dead-letter queue (``<queue>.dlq``).
Duplicates are possible (consumer crash between commit and ack, publisher re-publish after a lost
confirm) - every consumer is therefore idempotent. Exactly-once is NOT claimed.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

log = logging.getLogger("obs_common.broker")

EXCHANGE = "telemetry"


class BrokerUnavailable(Exception):
    """The broker could not be reached / did not confirm the publish."""


class NonRetryableError(Exception):
    """Poison message: skip retries and dead-letter immediately."""


@dataclass(frozen=True)
class ConsumerSpec:
    queue: str
    routing_keys: tuple[str, ...]
    retry_delays_ms: tuple[int, ...] = (2000, 10000, 30000)
    prefetch: int = 20

    @property
    def max_attempts(self) -> int:
        return len(self.retry_delays_ms) + 1

    def retry_queue(self, attempt: int) -> str:
        return f"{self.queue}.retry.{attempt}"

    @property
    def dlq(self) -> str:
        return f"{self.queue}.dlq"


# Central topology: producers declare every queue so messages are retained while a consumer is down.
TRACE_SPEC = ConsumerSpec("trace-processing.traces", ("telemetry.traces",))
LOG_SPEC = ConsumerSpec("log-processing.logs", ("telemetry.logs",))
METRIC_SPEC = ConsumerSpec("metrics-aggregation.metrics", ("telemetry.metrics",))
TRACE_COMPLETED_SPEC = ConsumerSpec("rca.trace-completed", ("events.trace.completed",), retry_delays_ms=(3000, 15000, 45000))
ALL_SPECS = (TRACE_SPEC, LOG_SPEC, METRIC_SPEC, TRACE_COMPLETED_SPEC)

Handler = Callable[[dict[str, Any]], None]


@dataclass
class Outcome:
    action: str  # ack | retry | dead
    reason: str | None = None


def run_handler(handler: Handler, payload: dict[str, Any], attempt: int, spec: ConsumerSpec, *, queue: str) -> Outcome:
    """Shared decision logic: success => ack, poison => dead, transient => retry (bounded) => dead."""
    try:
        handler(payload)
        return Outcome("ack")
    except NonRetryableError as exc:
        log.error(
            "consumer rejected message permanently",
            extra={"queue": queue, "event_id": payload.get("event_id") if isinstance(payload, dict) else None, "error": str(exc)},
        )
        return Outcome("dead", f"non-retryable: {exc}")
    except Exception as exc:  # noqa: BLE001 - consumer boundary
        event_id = payload.get("event_id") if isinstance(payload, dict) else None
        if attempt >= spec.max_attempts:
            log.error(
                "consumer exhausted retries, dead-lettering",
                extra={"queue": queue, "event_id": event_id, "attempt": attempt, "error": repr(exc)},
            )
            return Outcome("dead", f"retries exhausted: {exc!r}")
        log.warning(
            "consumer failed, scheduling retry",
            extra={"queue": queue, "event_id": event_id, "attempt": attempt, "error": repr(exc)},
        )
        return Outcome("retry", repr(exc))


class Broker(Protocol):
    def declare_topology(self, specs: tuple[ConsumerSpec, ...]) -> None: ...
    def publish(self, routing_key: str, body: dict[str, Any], *, message_id: str | None = None) -> None: ...
    def start_consumer(self, spec: ConsumerSpec, handler: Handler) -> None: ...
    def ping(self) -> bool: ...
    def consumers_running(self) -> bool: ...
    def stop(self) -> None: ...


# ---------------------------------------------------------------------- RabbitMQ


class RabbitBroker:
    def __init__(self, url: str, *, exchange: str = EXCHANGE, connect_timeout: float = 5.0):
        import pika  # local import: only needed in real deployments

        self._pika = pika
        self._params = pika.URLParameters(url)
        self._params.socket_timeout = connect_timeout
        self._params.blocked_connection_timeout = connect_timeout
        self._params.heartbeat = 30
        self._exchange = exchange
        self._pub_lock = threading.Lock()
        self._pub_conn = None
        self._pub_channel = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._consuming: dict[str, bool] = {}

    # --- topology
    def _declare(self, channel, specs: tuple[ConsumerSpec, ...]) -> None:
        channel.exchange_declare(exchange=self._exchange, exchange_type="topic", durable=True)
        for spec in specs:
            channel.queue_declare(queue=spec.queue, durable=True)
            for key in spec.routing_keys:
                channel.queue_bind(queue=spec.queue, exchange=self._exchange, routing_key=key)
            for i, delay in enumerate(spec.retry_delays_ms, start=1):
                channel.queue_declare(
                    queue=spec.retry_queue(i),
                    durable=True,
                    arguments={
                        "x-message-ttl": int(delay),
                        "x-dead-letter-exchange": "",  # default exchange => back to the main queue
                        "x-dead-letter-routing-key": spec.queue,
                    },
                )
            channel.queue_declare(queue=spec.dlq, durable=True)

    def declare_topology(self, specs: tuple[ConsumerSpec, ...] = ALL_SPECS) -> None:
        try:
            conn = self._pika.BlockingConnection(self._params)
            try:
                self._declare(conn.channel(), specs)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            raise BrokerUnavailable(f"cannot declare topology: {exc!r}") from exc

    # --- publishing
    def _reset_publisher(self) -> None:
        try:
            if self._pub_conn and self._pub_conn.is_open:
                self._pub_conn.close()
        except Exception:  # noqa: BLE001
            pass
        self._pub_conn = None
        self._pub_channel = None

    def _ensure_publisher(self):
        if self._pub_channel is not None and self._pub_channel.is_open:
            return self._pub_channel
        self._reset_publisher()
        self._pub_conn = self._pika.BlockingConnection(self._params)
        self._pub_channel = self._pub_conn.channel()
        self._pub_channel.confirm_delivery()
        self._declare(self._pub_channel, ALL_SPECS)
        return self._pub_channel

    def publish(self, routing_key: str, body: dict[str, Any], *, message_id: str | None = None) -> None:
        data = json.dumps(body, separators=(",", ":")).encode()
        props = self._pika.BasicProperties(
            delivery_mode=2,
            content_type="application/json",
            message_id=message_id,
            headers={"x-attempt": 1},
        )
        last: Exception | None = None
        with self._pub_lock:
            for _ in range(2):  # one transparent reconnect
                try:
                    channel = self._ensure_publisher()
                    channel.basic_publish(self._exchange, routing_key, data, props, mandatory=True)
                    return
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    self._reset_publisher()
        raise BrokerUnavailable(f"publish failed: {last!r}")

    def ping(self) -> bool:
        try:
            conn = self._pika.BlockingConnection(self._params)
            conn.close()
            return True
        except Exception:  # noqa: BLE001
            return False

    # --- consuming
    def start_consumer(self, spec: ConsumerSpec, handler: Handler) -> None:
        self._consuming[spec.queue] = False
        thread = threading.Thread(target=self._consume_loop, args=(spec, handler), name=f"consumer-{spec.queue}", daemon=True)
        thread.start()
        self._threads.append(thread)

    def consumers_running(self) -> bool:
        return bool(self._consuming) and all(self._consuming.values())

    def _consume_loop(self, spec: ConsumerSpec, handler: Handler) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            conn = None
            try:
                conn = self._pika.BlockingConnection(self._params)
                channel = conn.channel()
                channel.confirm_delivery()
                self._declare(channel, (spec,))
                channel.basic_qos(prefetch_count=spec.prefetch)
                self._consuming[spec.queue] = True
                backoff = 1.0
                log.info("consumer started", extra={"queue": spec.queue})
                for method, props, body in channel.consume(spec.queue, inactivity_timeout=1.0):
                    if self._stop.is_set():
                        break
                    if method is None:
                        continue
                    self._deliver(channel, method, props, body, spec, handler)
            except Exception as exc:  # noqa: BLE001 - reconnect loop
                self._consuming[spec.queue] = False
                log.warning("consumer connection lost", extra={"queue": spec.queue, "error": repr(exc), "retry_in_s": backoff})
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                self._consuming[spec.queue] = False
                try:
                    if conn and conn.is_open:
                        conn.close()
                except Exception:  # noqa: BLE001
                    pass

    def _deliver(self, channel, method, props, body: bytes, spec: ConsumerSpec, handler: Handler) -> None:
        headers = dict(props.headers or {})
        attempt = int(headers.get("x-attempt", 1))
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("message body must be a JSON object")
        except Exception as exc:  # noqa: BLE001
            outcome = Outcome("dead", f"non-retryable: undecodable message ({exc})")
            payload = {}
        else:
            outcome = run_handler(handler, payload, attempt, spec, queue=spec.queue)

        try:
            if outcome.action == "retry":
                self._republish(channel, spec.retry_queue(attempt), body, props, attempt + 1, outcome.reason)
            elif outcome.action == "dead":
                self._republish(channel, spec.dlq, body, props, attempt, outcome.reason)
            channel.basic_ack(method.delivery_tag)
        except Exception:  # noqa: BLE001
            # Could not hand the message over: put it back so it is not lost, then reconnect.
            channel.basic_nack(method.delivery_tag, requeue=True)
            raise

    def _republish(self, channel, queue: str, body: bytes, props, attempt: int, reason: str | None) -> None:
        headers = dict(props.headers or {})
        headers["x-attempt"] = attempt
        if reason:
            headers["x-last-error"] = reason[:500]
        new_props = self._pika.BasicProperties(
            delivery_mode=2, content_type="application/json", message_id=props.message_id, headers=headers
        )
        channel.basic_publish("", queue, body, new_props, mandatory=True)

    def stop(self) -> None:
        self._stop.set()
        with self._pub_lock:
            self._reset_publisher()
        for thread in self._threads:
            thread.join(timeout=3)


# ---------------------------------------------------------------------- in-memory (tests)


def _routing_regex(pattern: str) -> re.Pattern[str]:
    parts = []
    for token in pattern.split("."):
        if token == "*":
            parts.append(r"[^.]+")
        elif token == "#":
            parts.append(r".*")
        else:
            parts.append(re.escape(token))
    return re.compile("^" + r"\.".join(parts) + "$")


@dataclass
class _Msg:
    body: dict[str, Any]
    attempt: int = 1


@dataclass
class InMemoryBroker:
    """Synchronous broker double. Messages are delivered when ``drain()`` is called."""

    available: bool = True
    specs: dict[str, ConsumerSpec] = field(default_factory=dict)
    handlers: dict[str, Handler] = field(default_factory=dict)
    paused: set[str] = field(default_factory=set)
    queues: dict[str, deque] = field(default_factory=dict)
    dead_letters: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    published: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    delivered: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def declare_topology(self, specs: tuple[ConsumerSpec, ...] = ALL_SPECS) -> None:
        for spec in specs:
            self.specs.setdefault(spec.queue, spec)
            self.queues.setdefault(spec.queue, deque())
            self.dead_letters.setdefault(spec.queue, [])
            self.delivered.setdefault(spec.queue, [])

    def publish(self, routing_key: str, body: dict[str, Any], *, message_id: str | None = None) -> None:
        if not self.available:
            raise BrokerUnavailable("in-memory broker marked unavailable")
        self.published.append((routing_key, body))
        for queue, spec in self.specs.items():
            if any(_routing_regex(k).match(routing_key) for k in spec.routing_keys):
                self.queues[queue].append(_Msg(json.loads(json.dumps(body))))

    def start_consumer(self, spec: ConsumerSpec, handler: Handler) -> None:
        self.declare_topology((spec,))
        self.handlers[spec.queue] = handler

    def pause_consumer(self, queue: str) -> None:
        self.paused.add(queue)

    def resume_consumer(self, queue: str) -> None:
        self.paused.discard(queue)

    def redeliver_last(self, queue: str) -> None:
        """Simulate a duplicate delivery of the most recently delivered message."""
        last = self.delivered[queue][-1]
        self.queues[queue].append(_Msg(json.loads(json.dumps(last))))

    def ping(self) -> bool:
        return self.available

    def consumers_running(self) -> bool:
        return True

    def stop(self) -> None:
        return None

    def pending(self, queue: str) -> int:
        return len(self.queues.get(queue, ()))

    def drain(self, max_steps: int = 10000) -> int:
        """Deliver messages to registered, un-paused consumers until the queues are empty."""
        steps = 0
        progressed = True
        while progressed and steps < max_steps:
            progressed = False
            for queue, handler in list(self.handlers.items()):
                if queue in self.paused:
                    continue
                q = self.queues[queue]
                while q and steps < max_steps:
                    msg = q.popleft()
                    steps += 1
                    progressed = True
                    spec = self.specs[queue]
                    self.delivered[queue].append(msg.body)
                    outcome = run_handler(handler, msg.body, msg.attempt, spec, queue=queue)
                    if outcome.action == "retry":
                        q.append(_Msg(msg.body, msg.attempt + 1))
                    elif outcome.action == "dead":
                        self.dead_letters[queue].append({"body": msg.body, "reason": outcome.reason, "attempt": msg.attempt})
        return steps
