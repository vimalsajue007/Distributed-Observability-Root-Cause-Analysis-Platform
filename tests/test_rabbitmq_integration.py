"""Broker integration against a real RabbitMQ:  RABBITMQ_URL=amqp://user:pass@localhost:5672/%2F pytest -m rabbitmq"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from obs_common.broker import ConsumerSpec, RabbitBroker

pytestmark = pytest.mark.rabbitmq
URL = os.getenv("RABBITMQ_URL")
if not URL:
    pytest.skip("RABBITMQ_URL not set", allow_module_level=True)


def test_publish_consume_and_dead_letter():
    suffix = uuid.uuid4().hex[:8]
    spec = ConsumerSpec(f"it.{suffix}", (f"it.{suffix}.evt",), retry_delays_ms=(200, 200))
    broker = RabbitBroker(URL)
    broker.declare_topology((spec,))
    seen: list[dict] = []

    def handler(payload):
        seen.append(payload)
        if payload.get("poison"):
            raise RuntimeError("always fails")

    broker.start_consumer(spec, handler)
    try:
        broker.publish(f"it.{suffix}.evt", {"n": 1}, message_id=str(uuid.uuid4()))
        broker.publish(f"it.{suffix}.evt", {"poison": True}, message_id=str(uuid.uuid4()))
        deadline = time.time() + 20
        while time.time() < deadline and len([p for p in seen if p.get("poison")]) < spec.max_attempts:
            time.sleep(0.5)
        assert any(p.get("n") == 1 for p in seen)
        assert len([p for p in seen if p.get("poison")]) == spec.max_attempts  # bounded retries, then DLQ
    finally:
        broker.stop()
