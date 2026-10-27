"""Notification Service (sample application)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Path, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from obs_common.api import AppError, install_common, ok, require_internal, require_role
from obs_common.db import Database
from obs_common.logging_setup import configure_logging
from obs_common.scenarios import SCENARIO_HEADER, resolve_scenario
from obs_common.telemetry import HttpSink, TelemetrySink, setup_telemetry

from .config import Settings
from .models import Notification

log = logging.getLogger("notification-service")


class NotificationRequest(BaseModel):
    order_id: int = Field(..., ge=1)
    recipient: str = Field(..., min_length=1, max_length=120)
    channel: str = Field("email", pattern="^(email|sms|push)$")
    message: str = Field(..., min_length=1, max_length=1000)


def create_app(settings: Settings | None = None, *, db: Database | None = None, sink: TelemetrySink | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(settings.service_name, settings.log_level)
    db = db or Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if telemetry:
            telemetry.shutdown()

    app = FastAPI(title="Notification Service (sample)", version="1.0.0", description="Sends order notifications. Supports the notification_timeout scenario.",
                  lifespan=lifespan, docs_url="/docs" if settings.enable_docs else None, redoc_url=None,
                  openapi_url="/openapi.json" if settings.enable_docs else None)
    install_common(app, settings=settings, readiness_checks={"database": db.ping})
    telemetry = None
    if settings.telemetry_enabled:
        telemetry = setup_telemetry(app, service_name=settings.service_name, sink=sink or HttpSink(settings.ingestion_service_url, settings.ingestion_api_key),
                                    logger_name="notification-service")
    app.state.db, app.state.telemetry = db, telemetry

    def store(body: NotificationRequest) -> tuple[dict, bool]:
        key = f"{body.order_id}:{body.channel}"
        try:
            with db.session() as s:
                row = Notification(order_id=body.order_id, dedupe_key=key, recipient=body.recipient, channel=body.channel, message=body.message, status="sent")
                s.add(row)
                s.flush()
                return {"notification_id": row.id, "order_id": row.order_id, "channel": row.channel, "status": row.status}, True
        except IntegrityError:  # same order+channel already notified -> idempotent
            with db.session() as s:
                row = s.scalar(select(Notification).where(Notification.dedupe_key == key))
                return {"notification_id": row.id, "order_id": row.order_id, "channel": row.channel, "status": row.status}, False

    @app.post("/api/v1/notifications", tags=["notifications"], dependencies=[Depends(require_internal), require_role("operator")], status_code=201,
              summary="Send a notification (one per order+channel)")
    async def send(body: NotificationRequest, request: Request):
        scenario = resolve_scenario(request.headers.get(SCENARIO_HEADER), settings.default_scenario)
        await asyncio.sleep(settings.base_latency_ms / 1000)
        if scenario == "notification_timeout":
            log.warning("notification provider unresponsive", extra={"order_id": body.order_id, "delay_ms": settings.notification_sleep_ms})
            await asyncio.sleep(settings.notification_sleep_ms / 1000)
        data, created = await run_in_threadpool(store, body)
        log.info("notification sent" if created else "notification already sent", extra={"order_id": body.order_id, "channel": body.channel})
        return ok(data, status_code=201 if created else 200)

    @app.get("/api/v1/notifications/{notification_id}", tags=["notifications"], dependencies=[Depends(require_internal)], summary="Get a notification")
    def get_notification(notification_id: int = Path(..., ge=1)):
        with db.session() as s:
            row = s.get(Notification, notification_id)
            if row is None:
                raise AppError(404, "notification_not_found", f"Notification {notification_id} was not found")
            return ok({"notification_id": row.id, "order_id": row.order_id, "channel": row.channel, "status": row.status, "created_at": row.created_at})

    return app

# Run with: uvicorn notification_service.main:create_app --factory
