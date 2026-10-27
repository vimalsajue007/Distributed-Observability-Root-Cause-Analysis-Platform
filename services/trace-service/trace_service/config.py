from obs_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "trace-service"
    broker_url: str

    # Completion policy: a trace is finalised once no new span arrived for this many seconds.
    trace_completion_wait_seconds: float = 10.0
    finalizer_interval_seconds: float = 2.0

    # Slow-span detection
    slow_span_ms: float = 1000.0
    slow_baseline_factor: float = 3.0
    slow_baseline_min_samples: int = 20
    slow_baseline_min_ms: float = 100.0
    baseline_window_hours: int = 24

    run_background_workers: bool = True
