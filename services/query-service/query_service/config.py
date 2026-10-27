from obs_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "query-service"
    database_url: str = "none://stateless"  # stateless service: no database
    trace_service_url: str
    log_service_url: str
    metrics_service_url: str
    rca_service_url: str
