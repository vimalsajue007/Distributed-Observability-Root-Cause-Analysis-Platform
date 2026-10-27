from obs_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "payment-service"
    ingestion_service_url: str = "http://ingestion-service:8000"
    ingestion_api_key: str = ""
    telemetry_enabled: bool = True
    default_scenario: str = "normal"
    slow_payment_ms: int = 1500          # slow_payment scenario
    payment_timeout_sleep_ms: int = 4000  # payment_timeout scenario (must exceed the order service's payment timeout)
    intermittent_period: int = 2         # intermittent_unavailability: every Nth call fails (N=2 -> every other call)
    base_latency_ms: int = 20
