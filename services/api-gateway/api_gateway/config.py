from obs_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "api-gateway"

    # --- authentication
    jwt_secret: str  # required, >= 32 chars
    jwt_ttl_seconds: int = 3600
    password_iterations: int = 240_000
    seed_admin_password: str
    seed_operator_password: str
    seed_viewer_password: str
    login_max_failures: int = 5
    login_lockout_seconds: int = 300

    # --- downstream services (hostnames equal the compose service names; peer.service derives from them)
    query_service_url: str
    rca_service_url: str
    metrics_service_url: str
    ingestion_service_url: str
    trace_service_url: str
    log_service_url: str
    order_service_url: str
    payment_service_url: str
    notification_service_url: str
    gateway_timeout_seconds: float = 5.0
    sample_timeout_seconds: float = 12.0

    # --- telemetry (the gateway is the root span of every sample request)
    telemetry_enabled: bool = True
    ingestion_api_key: str = ""

    # --- misc
    cors_origins: str = ""  # comma separated; empty = same-origin only
    request_log_retention_days: int = 7

    def validate_secrets(self) -> None:
        if len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        if self.is_production and ("change-me" in self.jwt_secret or "change-me" in self.internal_api_token):
            raise ValueError("placeholder secrets from .env.example must not be used when ENVIRONMENT=production")
