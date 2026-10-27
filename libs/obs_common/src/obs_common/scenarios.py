"""Failure scenarios of the sample application.

A scenario is chosen per request with the ``X-Scenario`` header (forwarded by every service) or globally
with the ``DEFAULT_SCENARIO`` environment variable. Unknown names are rejected, never silently ignored.
"""

from __future__ import annotations

import re

from .api import AppError

SCENARIO_HEADER = "X-Scenario"

SCENARIOS: dict[str, dict[str, str]] = {
    "normal": {"description": "Everything succeeds quickly.", "expected_diagnosis": "healthy"},
    "slow_payment": {"description": "Payment service answers slowly but successfully.", "expected_diagnosis": "latency_bottleneck in payment-service"},
    "payment_failure": {"description": "Payment service fails with an internal error; the order fails.", "expected_diagnosis": "confirmed_failure in payment-service"},
    "payment_timeout": {"description": "Payment service takes longer than the order service is willing to wait.", "expected_diagnosis": "potential_cause payment-service (caller observed timeout)"},
    "notification_timeout": {"description": "Notification service exceeds its timeout; the order still completes.", "expected_diagnosis": "potential_cause notification-service"},
    "invalid_downstream_response": {"description": "Payment service answers 200 with a malformed body.", "expected_diagnosis": "confirmed_failure in order-service (invalid payment response)"},
    "intermittent_unavailability": {"description": "Payment service answers 503 on every other call; the order service retries with the same idempotency key.", "expected_diagnosis": "confirmed_failure in payment-service (recovered by retry)"},
}

_NAME = re.compile(r"^[a-z0-9_]{1,40}$")


def resolve_scenario(header_value: str | None, default: str = "normal") -> str:
    name = (header_value or default or "normal").strip().lower()
    if not _NAME.match(name) or name not in SCENARIOS:
        raise AppError(422, "invalid_scenario", f"Unknown scenario {name[:40]!r}", {"allowed": sorted(SCENARIOS)})
    return name
