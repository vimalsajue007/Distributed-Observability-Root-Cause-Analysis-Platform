"""Payment Service (sample application)."""

from __future__ import annotations

import asyncio
import itertools
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Path, Request
from fastapi.concurrency import run_in_threadpool
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from obs_common.api import AppError, current_user, install_common, ok, require_internal, require_role
from obs_common.db import Database
from obs_common.logging_setup import configure_logging
from obs_common.scenarios import SCENARIO_HEADER, resolve_scenario
from obs_common.telemetry import HttpSink, TelemetrySink, setup_telemetry

from .config import Settings
from .models import Base, Payment

log = logging.getLogger("payment-service")


class PaymentRequest(BaseModel):
    order_id: int = Field(..., ge=1)
    amount: float = Field(..., gt=0, le=1_000_000)
    currency: str = Field("USD", pattern="^[A-Z]{3}$")
    idempotency_key: str = Field(..., min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:\-]+$")


def payment_dict(p: Payment) -> dict:
    return {"payment_id": p.id, "order_id": p.order_id, "amount": p.amount, "currency": p.currency, "status": p.status,
            "reference": p.reference, "created_at": p.created_at}


def create_app(settings: Settings | None = None, *, db: Database | None = None, sink: TelemetrySink | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(settings.service_name, settings.log_level)
    db = db or Database(settings.database_url)
    counter = itertools.count(1)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if telemetry:
            telemetry.shutdown()

    app = FastAPI(title="Payment Service (sample)", version="1.0.0", description="Charges orders. Supports failure scenarios via the X-Scenario header.",
                  lifespan=lifespan, docs_url="/docs" if settings.enable_docs else None, redoc_url=None,
                  openapi_url="/openapi.json" if settings.enable_docs else None)
    install_common(app, settings=settings, readiness_checks={"database": db.ping})
    telemetry = None
    if settings.telemetry_enabled:
        telemetry = setup_telemetry(app, service_name=settings.service_name, sink=sink or HttpSink(settings.ingestion_service_url, settings.ingestion_api_key),
                                    logger_name="payment-service")
    app.state.db, app.state.telemetry = db, telemetry
    guard = [Depends(require_internal)]

    def span(name: str):
        return telemetry.tracer.start_as_current_span(name) if telemetry else _NullSpan()

    def find(key: str) -> Payment | None:
        with db.session() as s:
            row = s.scalar(select(Payment).where(Payment.idempotency_key == key))
            return row and Payment(**{c.name: getattr(row, c.name) for c in Payment.__table__.columns})

    def save(body: PaymentRequest) -> tuple[Payment, bool]:
        try:
            with db.session() as s:
                row = Payment(order_id=body.order_id, idempotency_key=body.idempotency_key, amount=body.amount, currency=body.currency,
                              status="captured", reference=f"PAY-{uuid.uuid4().hex[:12].upper()}")
                s.add(row)
                s.flush()
                return Payment(**{c.name: getattr(row, c.name) for c in Payment.__table__.columns}), True
        except IntegrityError:  # concurrent duplicate request with the same idempotency key
            existing = find(body.idempotency_key)
            assert existing is not None
            return existing, False

    @app.post("/api/v1/payments", tags=["payments"], dependencies=[*guard, require_role("operator")], status_code=201, summary="Charge an order (idempotent)")
    async def charge(body: PaymentRequest, request: Request):
        scenario = resolve_scenario(request.headers.get(SCENARIO_HEADER), settings.default_scenario)
        n = next(counter)
        with span("payment.charge") as sp:
            if sp:
                sp.set_attribute("payment.scenario", scenario)
                sp.set_attribute("payment.order_id", body.order_id)
            existing = await run_in_threadpool(find, body.idempotency_key)
            if existing is not None:
                log.info("idempotent replay, returning stored payment", extra={"order_id": body.order_id, "payment_id": existing.id})
                return ok(payment_dict(existing), status_code=200)
            await asyncio.sleep(settings.base_latency_ms / 1000)
            if scenario == "slow_payment":
                log.warning("payment gateway responding slowly", extra={"order_id": body.order_id, "delay_ms": settings.slow_payment_ms})
                await asyncio.sleep(settings.slow_payment_ms / 1000)
            elif scenario == "payment_timeout":
                log.warning("payment processor stalled", extra={"order_id": body.order_id, "delay_ms": settings.payment_timeout_sleep_ms})
                await asyncio.sleep(settings.payment_timeout_sleep_ms / 1000)
            elif scenario == "payment_failure":
                log.error("payment processor rejected the charge", extra={"order_id": body.order_id, "reason": "processor_error"})
                _fail(sp, "payment processor error")
                raise AppError(500, "payment_processor_error", "The payment processor failed to process the charge")
            elif scenario == "intermittent_unavailability" and n % settings.intermittent_period == 1 % settings.intermittent_period:
                log.error("payment processor temporarily unavailable", extra={"order_id": body.order_id})
                _fail(sp, "payment processor unavailable")
                raise AppError(503, "payment_unavailable", "Payment processor temporarily unavailable", headers={"Retry-After": "1"})
            if scenario == "invalid_downstream_response":
                row, _ = await run_in_threadpool(save, body)
                log.info("payment captured (malformed response will be returned)", extra={"order_id": body.order_id})
                # deliberately violates the response contract: no payment_id / status
                return ok({"unexpected": True, "ref": row.reference})
            row, created = await run_in_threadpool(save, body)
            log.info("payment captured", extra={"order_id": body.order_id, "payment_id": row.id, "amount": body.amount})
            return ok(payment_dict(row), status_code=201 if created else 200)

    @app.get("/api/v1/payments/{payment_id}", tags=["payments"], dependencies=guard, summary="Get a payment")
    def get_payment(payment_id: int = Path(..., ge=1)):
        with db.session() as s:
            row = s.get(Payment, payment_id)
            if row is None:
                raise AppError(404, "payment_not_found", f"Payment {payment_id} was not found")
            return ok(payment_dict(row))

    return app


def _fail(sp, message: str) -> None:
    if sp is not None:
        sp.set_status(Status(StatusCode.ERROR, message))


class _NullSpan:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False

# Run with: uvicorn payment_service.main:create_app --factory
