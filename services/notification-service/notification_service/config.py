from obs_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "notification-service"
    ingestion_service_url: str = "http://ingestion-service:8000"
    ingestion_api_key: str = ""
    telemetry_enabled: bool = True
    default_scenario: str = "normal"
    notification_sleep_ms: int = 3000  # notification_timeout scenario (must exceed the order service's notification timeout)
    base_latency_ms: int = 10
