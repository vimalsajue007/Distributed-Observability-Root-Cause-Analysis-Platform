from obs_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "order-service"
    ingestion_service_url: str = "http://ingestion-service:8000"
    ingestion_api_key: str = ""
    telemetry_enabled: bool = True
    payment_service_url: str
    notification_service_url: str
    default_scenario: str = "normal"
    payment_timeout_seconds: float = 2.0
    notification_timeout_seconds: float = 1.5
    payment_max_attempts: int = 2  # retried only on 502/503/504, always with the same idempotency key
    payment_retry_backoff_seconds: float = 0.2
