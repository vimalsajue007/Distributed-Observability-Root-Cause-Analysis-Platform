from obs_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "log-service"
    broker_url: str
    max_message_length: int = 8000
