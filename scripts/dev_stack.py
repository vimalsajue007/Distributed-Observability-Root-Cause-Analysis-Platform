#!/usr/bin/env python3
"""Run the WHOLE platform in one Python process - no Docker, MySQL or RabbitMQ needed.

    python scripts/dev_stack.py            # gateway on http://127.0.0.1:8000
    cd frontend && npm run dev             # console on http://localhost:5173 (proxies /api to the gateway)

Everything is real application code; only the infrastructure is swapped for in-process stand-ins:
SQLite (in memory) instead of MySQL, an in-memory broker instead of RabbitMQ, and in-process HTTP between services.
Data is lost on exit. This is a convenience for UI development and demos, NOT a deployment mode - use docker compose for that.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "libs/obs_common/src"), *(str(p) for p in sorted((ROOT / "services").iterdir()) if p.is_dir())]

import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from obs_common.broker import InMemoryBroker  # noqa: E402
from obs_common.db import Database  # noqa: E402
from obs_common.telemetry import CallableSink  # noqa: E402
from obs_common.testing import DeadlineASGITransport, TestClientTransport  # noqa: E402

TOKEN = "dev-internal-token-0123456789abcdef0123456789"
INGEST_KEY = "dev-ingest-key-0123456789abcdef"
PASSWORDS = {"admin": os.getenv("DEV_ADMIN_PASSWORD", "Admin#dev-1"), "operator": os.getenv("DEV_OPERATOR_PASSWORD", "Operator#dev-1"), "viewer": os.getenv("DEV_VIEWER_PASSWORD", "Viewer#dev-1")}


def _db(models):
    d = Database("sqlite://")
    d.create_all(models.Base)
    return d


def build(trace_wait: float = 6.0):
    import importlib

    m = {n: importlib.import_module(f"{n}.models") for n in ("ingestion_service", "trace_service", "log_service", "metrics_service", "rca_service", "api_gateway", "order_service", "payment_service", "notification_service")}
    cfg = {n: importlib.import_module(f"{n}.config") for n in m}
    mains = {n: importlib.import_module(f"{n}.main") for n in [*m, "query_service"]}
    broker = InMemoryBroker()
    broker.declare_topology()
    tok = dict(internal_api_token=TOKEN)
    ing = TestClient(mains["ingestion_service"].create_app(cfg["ingestion_service"].Settings(database_url="sqlite://", broker_url="memory://", ingest_api_keys=INGEST_KEY, **tok), broker=broker, db=_db(m["ingestion_service"])))
    trace = TestClient(mains["trace_service"].create_app(cfg["trace_service"].Settings(database_url="sqlite://", broker_url="memory://", trace_completion_wait_seconds=trace_wait, finalizer_interval_seconds=1.0, **tok), broker=broker, db=_db(m["trace_service"])))
    logs = TestClient(mains["log_service"].create_app(cfg["log_service"].Settings(database_url="sqlite://", broker_url="memory://", **tok), broker=broker, db=_db(m["log_service"])))
    metrics = TestClient(mains["metrics_service"].create_app(cfg["metrics_service"].Settings(database_url="sqlite://", broker_url="memory://", **tok), broker=broker, db=_db(m["metrics_service"])))
    clients = [ing.__enter__(), trace.__enter__(), logs.__enter__(), metrics.__enter__()]
    rca_app = mains["rca_service"].create_app(
        cfg["rca_service"].Settings(database_url="sqlite://", broker_url="memory://", trace_service_url="http://trace-service", log_service_url="http://log-service", metrics_service_url="http://metrics-service", **tok),
        broker=broker, db=_db(m["rca_service"]), transports={"trace-service": TestClientTransport(trace), "log-service": TestClientTransport(logs), "metrics-service": TestClientTransport(metrics)})
    rca = TestClient(rca_app)
    rca.__enter__()
    query = mains["query_service"].create_app(
        importlib.import_module("query_service.config").Settings(trace_service_url="http://trace-service", log_service_url="http://log-service", metrics_service_url="http://metrics-service", rca_service_url="http://rca-service", **tok),
        transports={"trace-service": TestClientTransport(trace), "log-service": TestClientTransport(logs), "metrics-service": TestClientTransport(metrics), "rca-service": TestClientTransport(rca)})

    sink = CallableSink(lambda kind, events: ing.post(f"/api/v1/telemetry/{kind}", json={"events": events}, headers={"X-API-Key": INGEST_KEY}).status_code < 300)
    common = dict(**tok, ingestion_service_url="http://ingestion-service", ingestion_api_key=INGEST_KEY)
    payment = mains["payment_service"].create_app(cfg["payment_service"].Settings(database_url="sqlite://", **common), db=_db(m["payment_service"]), sink=sink)
    notification = mains["notification_service"].create_app(cfg["notification_service"].Settings(database_url="sqlite://", **common), db=_db(m["notification_service"]), sink=sink)
    order = mains["order_service"].create_app(
        cfg["order_service"].Settings(database_url="sqlite://", payment_service_url="http://payment-service", notification_service_url="http://notification-service", **common),
        db=_db(m["order_service"]), sink=sink, transports={"payment-service": DeadlineASGITransport(payment), "notification-service": DeadlineASGITransport(notification)})
    urls = {f"{n}_service_url": f"http://{n}-service" for n in ("query", "rca", "metrics", "ingestion", "trace", "log", "order", "payment", "notification")}
    gateway = mains["api_gateway"].create_app(
        cfg["api_gateway"].Settings(database_url="sqlite://", jwt_secret="dev-jwt-secret-0123456789abcdef0123456789", seed_admin_password=PASSWORDS["admin"], seed_operator_password=PASSWORDS["operator"],
                                    seed_viewer_password=PASSWORDS["viewer"], ingestion_api_key=INGEST_KEY, sample_timeout_seconds=15, **tok, **urls),
        db=_db(m["api_gateway"]), sink=sink,
        transports={"query-service": DeadlineASGITransport(query), "rca-service": DeadlineASGITransport(rca), "metrics-service": DeadlineASGITransport(metrics_app_asgi := metrics.app), "order-service": DeadlineASGITransport(order),
                    **{f"health:{n}-service": DeadlineASGITransport(a) for n, a in {"ingestion": ing.app, "trace": trace.app, "log": logs.app, "metrics": metrics.app, "rca": rca.app, "query": query, "order": order, "payment": payment, "notification": notification}.items()}})
    _ = metrics_app_asgi, clients
    return gateway, broker


def main() -> None:
    gateway, broker = build()
    stop = threading.Event()

    def pump() -> None:  # deliver broker messages continuously (RabbitMQ would push them)
        while not stop.is_set():
            try:
                broker.drain()
            except Exception as exc:  # noqa: BLE001
                print("broker pump error:", exc, file=sys.stderr)
            time.sleep(0.25)

    threading.Thread(target=pump, name="broker-pump", daemon=True).start()
    print("\nPlatform running in one process.\n  Gateway:  http://127.0.0.1:8000  (Swagger: /docs)\n  Console:  cd frontend && npm run dev   -> http://localhost:5173")
    print("  Users:    " + ", ".join(f"{u} / {p}" for u, p in PASSWORDS.items()) + "\n")
    uvicorn.run(gateway, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")), log_level="warning")
    stop.set()


if __name__ == "__main__":
    main()
