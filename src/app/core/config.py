from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Ticket Reservation Lab"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    cookie_secure: bool = False
    visitor_session_ttl_seconds: int = 60 * 60 * 24 * 30
    reservation_ttl_seconds: int = 60 * 15
    expiration_worker_interval_seconds: int = 5
    expiration_worker_batch_size: int = 100
    database_url: str = (
        "postgresql+asyncpg://ticket_reservation:ticket_reservation"
        "@localhost:5432/ticket_reservation"
    )
    redis_url: str = "redis://localhost:6379/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
