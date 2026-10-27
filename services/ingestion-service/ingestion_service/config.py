from obs_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "ingestion-service"
    broker_url: str
    ingest_api_keys: str  # comma separated; supports key rotation
    max_batch_events: int = 1000
    publish_retries: int = 2
    publish_backoff_seconds: float = 0.2
    republish_interval_seconds: float = 5.0
    republish_batch_size: int = 200
    republish_max_attempts: int = 50
    run_background_workers: bool = True

    @property
    def api_keys(self) -> set[str]:
        return {k.strip() for k in self.ingest_api_keys.split(",") if k.strip()}
