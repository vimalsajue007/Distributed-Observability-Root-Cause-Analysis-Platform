"""Order Service (sample application): orchestrates payment and notification."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Path, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select

from obs_common.api import AppError, PageParams, install_common, ok, page_meta, pagination, require_internal, require_role
from obs_common.db import Database
from obs_common.http import DownstreamClient, DownstreamError
from obs_common.logging_setup import configure_logging
from obs_common.scenarios import SCENARIO_HEADER, SCENARIOS, resolve_scenario
from obs_common.telemetry import HttpSink, TelemetrySink, setup_telemetry

from .config import Settings
from .models import Order, OrderItem

log = logging.getLogger("order-service")


class ItemIn(BaseModel):
    sku: str = Field(..., pattern=r"^[A-Za-z0-9._\-]{1,64}$")
    quantity: int = Field(..., ge=1, le=1000)
    unit_price: float = Field(..., gt=0, le=100_000)


class OrderIn(BaseModel):
    customer_id: str = Field(..., pattern=r"^[A-Za-z0-9._\-@]{1,64}$")
    items: list[ItemIn] = Field(..., min_length=1, max_length=50)
    currency: str = Field("USD", pattern="^[A-Z]{3}$")


class PaymentResult(BaseModel):
    """Contract of the payment service response. Anything else is an invalid downstream response."""

    payment_id: int
    status: str
    reference: str


def order_dict(o: Order, *, with_items: bool = True) -> dict[str, Any]:
    out = {
        "order_id": o.id, "customer_id": o.customer_id, "total_amount": o.total_amount, "currency": o.currency, "status": o.status,
        "payment_id": o.payment_id, "notification_status": o.notification_status, "scenario": o.scenario,
        "failure_reason": o.failure_reason, "correlation_id": o.correlation_id, "created_at": o.created_at, "updated_at": o.updated_at,
    }
    if with_items:
        out["items"] = [{"sku": i.sku, "quantity": i.quantity, "unit_price": i.unit_price} for i in o.items]
    return out


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    sink: TelemetrySink | None = None,
    transports: dict | None = None,
) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(settings.service_name, settings.log_level)
    db = db or Database(settings.database_url)
    transports = transports or {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if telemetry:
            telemetry.shutdown()
        await payment.aclose()
        await notification.aclose()

    app = FastAPI(title="Order Service (sample)", version="1.0.0",
                  description="Creates orders by calling the payment and notification services. Choose a failure scenario with the X-Scenario header.",
                  lifespan=lifespan, docs_url="/docs" if settings.enable_docs else None, redoc_url=None,
                  openapi_url="/openapi.json" if settings.enable_docs else None)
    install_common(app, settings=settings, readiness_checks={"database": db.ping})
    telemetry = None
    if settings.telemetry_enabled:
        telemetry = setup_telemetry(app, service_name=settings.service_name, sink=sink or HttpSink(settings.ingestion_service_url, settings.ingestion_api_key),
                                    logger_name="order-service")
    instrument = telemetry.instrument_client if telemetry else None
    payment = DownstreamClient("payment-service", settings.payment_service_url, timeout=settings.payment_timeout_seconds, retries=0,
                               internal_token=settings.internal_api_token, transport=transports.get("payment-service"), instrument=instrument)
    notification = DownstreamClient("notification-service", settings.notification_service_url, timeout=settings.notification_timeout_seconds, retries=0,
                                    internal_token=settings.internal_api_token, transport=transports.get("notification-service"), instrument=instrument)
    app.state.db, app.state.telemetry, app.state.payment, app.state.notification = db, telemetry, payment, notification
    guard = [Depends(require_internal)]

    def span(name: str):
        return telemetry.tracer.start_as_current_span(name) if telemetry else _NullSpan()

    # ---------------------------------------------------------------- persistence helpers (sync, run in a thread)

    def insert_order(body: OrderIn, scenario: str, correlation_id: str | None) -> int:
        with db.session() as s:
            order = Order(customer_id=body.customer_id, currency=body.currency, status="pending", scenario=scenario, correlation_id=correlation_id,
                          total_amount=round(sum(i.quantity * i.unit_price for i in body.items), 2),
                          items=[OrderItem(sku=i.sku, quantity=i.quantity, unit_price=i.unit_price) for i in body.items])
            s.add(order)
            s.flush()
            return order.id

    def update_order(order_id: int, **fields: Any) -> dict[str, Any]:
        with db.session() as s:
            order = s.get(Order, order_id)
            for k, v in fields.items():
                setattr(order, k, v)
            s.flush()
            _ = order.items
            return order_dict(order)

    # ---------------------------------------------------------------- downstream orchestration

    def forward_headers(request: Request, scenario: str) -> dict[str, str]:
        h = {SCENARIO_HEADER: scenario}
        for name in ("X-User-Name", "X-User-Role"):
            if request.headers.get(name):
                h[name] = request.headers[name]
        return h

    async def charge(order_id: int, amount: float, currency: str, headers: dict[str, str]) -> PaymentResult:
        """Call the payment service. Retries only transient failures (502/503/504, connection errors) with the *same* idempotency key."""
        key = f"order-{order_id}-payment"
        last: AppError | None = None
        for attempt in range(1, settings.payment_max_attempts + 1):
            try:
                resp = await payment.send("POST", "/api/v1/payments", json={"order_id": order_id, "amount": amount, "currency": currency, "idempotency_key": key},
                                          headers=headers, retry=False)
            except DownstreamError as exc:
                kind_status = 504 if exc.kind == "timeout" else 503
                last = AppError(kind_status, "payment_timeout" if exc.kind == "timeout" else "payment_unreachable",
                                f"payment-service {'timed out' if exc.kind == 'timeout' else 'is unreachable'}", {"order_id": order_id},
                                source="downstream", service="payment-service")
                log.error("payment call failed", extra={"order_id": order_id, "kind": exc.kind, "attempt": attempt})
                if exc.kind == "timeout":
                    break  # the charge may still complete; the idempotency key protects a later retry by the caller
            else:
                if resp.status_code in (502, 503, 504):
                    last = AppError(resp.status_code, "payment_unavailable", "payment-service is temporarily unavailable", {"order_id": order_id},
                                    source="downstream", service="payment-service")
                    log.warning("payment unavailable, will retry" if attempt < settings.payment_max_attempts else "payment unavailable, giving up",
                                extra={"order_id": order_id, "attempt": attempt, "status_code": resp.status_code})
                elif resp.status_code >= 400:
                    try:
                        code = (resp.json().get("error") or {}).get("code", "payment_rejected")
                    except (ValueError, AttributeError):
                        code = "payment_rejected"
                    log.error("payment failed", extra={"order_id": order_id, "status_code": resp.status_code, "code": code})
                    raise AppError(502, code, "The payment could not be processed", {"order_id": order_id}, source="downstream", service="payment-service")
                else:
                    with span("order.validate_payment_response"):
                        try:
                            return PaymentResult.model_validate((resp.json() or {}).get("data"))
                        except (ValidationError, ValueError, AttributeError) as exc:
                            log.error("payment-service returned an invalid response", extra={"order_id": order_id, "problem": type(exc).__name__})
                            raise AppError(502, "invalid_downstream_response", "payment-service returned a response that violates its contract",
                                           {"order_id": order_id}, source="downstream", service="payment-service") from exc
            if attempt < settings.payment_max_attempts:
                await asyncio.sleep(settings.payment_retry_backoff_seconds * attempt)
        assert last is not None
        raise last

    async def notify(order_id: int, customer_id: str, total: float, headers: dict[str, str]) -> bool:
        try:
            resp = await notification.send("POST", "/api/v1/notifications", retry=False, headers=headers,
                                           json={"order_id": order_id, "recipient": customer_id, "channel": "email", "message": f"Order {order_id} confirmed, total {total:.2f}"})
            if resp.status_code >= 400:
                raise DownstreamError("notification-service", "http_5xx" if resp.status_code >= 500 else "http_4xx", f"HTTP {resp.status_code}", status_code=resp.status_code)
            return True
        except DownstreamError as exc:
            # A missing e-mail must not undo a paid order: record it and carry on.
            log.warning("notification failed, order kept", extra={"order_id": order_id, "kind": exc.kind})
            return False

    # ---------------------------------------------------------------- routes

    @app.post("/api/v1/orders", tags=["orders"], dependencies=[*guard, require_role("operator")], status_code=201, summary="Create an order")
    async def create_order(body: OrderIn, request: Request):
        scenario = resolve_scenario(request.headers.get(SCENARIO_HEADER), settings.default_scenario)
        headers = forward_headers(request, scenario)
        with span("order.create") as sp:
            if sp:
                sp.set_attribute("order.scenario", scenario)
            correlation = request.headers.get("X-Correlation-ID")
            order_id = await run_in_threadpool(insert_order, body, scenario, correlation)
            total = round(sum(i.quantity * i.unit_price for i in body.items), 2)
            log.info("order received", extra={"order_id": order_id, "scenario": scenario, "total": total})
            try:
                result = await charge(order_id, total, body.currency, headers)
            except AppError as exc:
                status = "payment_failed" if exc.code != "invalid_downstream_response" else "failed"
                await run_in_threadpool(update_order, order_id, status=status, failure_reason=f"{exc.code}: {exc.message}"[:500])
                raise
            notified = await notify(order_id, body.customer_id, total, headers)
            data = await run_in_threadpool(update_order, order_id, status="completed", payment_id=result.payment_id,
                                           notification_status="sent" if notified else "failed")
            log.info("order completed", extra={"order_id": order_id, "payment_id": result.payment_id, "notification": data["notification_status"]})
            warnings = None if notified else [{"code": "notification_failed", "message": "The order was completed but the confirmation could not be sent."}]
            return ok(data, status_code=201, warnings=warnings)

    @app.get("/api/v1/orders", tags=["orders"], dependencies=guard, summary="List orders")
    def list_orders(page: PageParams = Depends(pagination)):
        with db.session() as s:
            total = s.scalar(select(func.count()).select_from(Order)) or 0
            rows = s.scalars(select(Order).order_by(Order.id.desc()).offset(page.offset).limit(page.page_size)).all()
            data = [order_dict(o, with_items=False) for o in rows]
        return ok(data, meta=page_meta(page.page, page.page_size, total))

    @app.get("/api/v1/orders/{order_id}", tags=["orders"], dependencies=guard, summary="Get an order")
    def get_order(order_id: int = Path(..., ge=1)):
        with db.session() as s:
            order = s.get(Order, order_id)
            if order is None:
                raise AppError(404, "order_not_found", f"Order {order_id} was not found")
            return ok(order_dict(order))

    @app.get("/api/v1/scenarios", tags=["scenarios"], dependencies=guard, summary="Available failure scenarios")
    def scenarios():
        return ok([{"name": name, **info} for name, info in SCENARIOS.items()], meta={"header": SCENARIO_HEADER, "default": settings.default_scenario})

    return app


class _NullSpan:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False

# Run with: uvicorn order_service.main:create_app --factory
