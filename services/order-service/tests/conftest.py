import httpx
import pytest
from fastapi.testclient import TestClient

from obs_common.db import Database
from obs_common.testing import INTERNAL_TOKEN, DeadlineASGITransport

from notification_service.config import Settings as NotifSettings
from notification_service.main import create_app as create_notification
from notification_service.models import Base as NotifBase
from order_service.config import Settings
from order_service.main import create_app
from order_service.models import Base
from payment_service.config import Settings as PaySettings
from payment_service.main import create_app as create_payment
from payment_service.models import Base as PayBase

COMMON = dict(internal_api_token=INTERNAL_TOKEN, telemetry_enabled=False)
H = {"X-Internal-Token": INTERNAL_TOKEN, "X-User-Role": "operator", "X-User-Name": "tester"}
BODY = {"customer_id": "cust-1", "items": [{"sku": "SKU-1", "quantity": 2, "unit_price": 10.5}]}


def _db(base):
    d = Database("sqlite://")
    d.create_all(base)
    return d


@pytest.fixture
def make_stack():
    """Factory so each test can tune timeouts / delays."""

    def build(order=None, payment=None, notification=None):
        pay_db, notif_db, order_db = _db(PayBase), _db(NotifBase), _db(Base)
        pay_app = create_payment(PaySettings(database_url="sqlite://", base_latency_ms=0, **COMMON, **(payment or {})), db=pay_db)
        notif_app = create_notification(NotifSettings(database_url="sqlite://", base_latency_ms=0, **COMMON, **(notification or {})), db=notif_db)
        transports = {"payment-service": DeadlineASGITransport(pay_app), "notification-service": DeadlineASGITransport(notif_app)}
        order_app = create_app(Settings(database_url="sqlite://", payment_service_url="http://payment-service", notification_service_url="http://notification-service",
                                        payment_retry_backoff_seconds=0.0, **COMMON, **(order or {})), db=order_db, transports=transports)
        client = TestClient(order_app)
        return type("Stack", (), {"client": client, "pay_db": pay_db, "notif_db": notif_db, "order_db": order_db, "pay_app": pay_app, "notif_app": notif_app})

    return build
