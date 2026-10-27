from obs_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "metrics-service"
    broker_url: str

    aggregation_bucket_seconds: int = 60
    flush_interval_seconds: float = 1.0
    raw_retention_hours: int = 24

    # Alerting
    alert_eval_interval_seconds: float = 10.0
    alert_resolve_after_seconds: float = 300.0
    # Default rules (seeded at start-up, editable through PUT /api/v1/alerts/rules)
    default_window_seconds: int = 300
    default_min_samples: int = 5
    default_error_rate_threshold: float = 0.2          # ratio (0.2 = 20 %)
    default_avg_latency_threshold_ms: float = 1000.0
    default_p95_latency_threshold_ms: float = 2000.0
    default_request_volume_threshold: float = 1000.0   # requests per window

    run_background_workers: bool = True
