import json

from obs_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "rca-service"
    broker_url: str
    trace_service_url: str
    log_service_url: str
    metrics_service_url: str
    # optional JSON object overriding rule weights/thresholds, e.g. {"w_leaf_error": 60}
    rca_rule_overrides: str = "{}"
    metrics_window_before_minutes: int = 30
    metrics_window_after_minutes: int = 5
    related_traces_lookback_hours: int = 24

    @property
    def rule_overrides(self) -> dict:
        try:
            data = json.loads(self.rca_rule_overrides or "{}")
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}
