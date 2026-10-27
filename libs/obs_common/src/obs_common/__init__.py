"""Shared building blocks for the observability platform services.

Only cross-cutting concerns live here (configuration, DB plumbing, API envelope,
broker abstraction, HTTP client with timeouts/retries, OpenTelemetry helpers).
No business logic and no cross-service database access.
"""

__version__ = "1.0.0"
