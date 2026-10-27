"""Base settings. Everything comes from environment variables (12-factor)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, env_file=None)

    service_name: str = "service"
    environment: str = "dev"
    log_level: str = "INFO"
    enable_docs: bool = True

    # Required secrets/URLs: no defaults so a missing variable fails fast at startup.
    database_url: str
    internal_api_token: str

    # Limits / timeouts
    max_body_bytes: int = 1_048_576
    downstream_timeout_seconds: float = 3.0
    downstream_retries: int = 1
    downstream_backoff_seconds: float = 0.2

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}
