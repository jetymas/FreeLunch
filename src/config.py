from __future__ import annotations

from dataclasses import dataclass, field
import os


@dataclass(slots=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "./data/freelunch.db"))
    openrouter_api_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    gateway_api_key: str = field(default_factory=lambda: os.getenv("GATEWAY_API_KEY", ""))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    openrouter_base_url: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    )
    provider_timeout_s: float = field(default_factory=lambda: float(os.getenv("PROVIDER_TIMEOUT_S", "30")))
    max_failover_attempts: int = field(
        default_factory=lambda: int(os.getenv("MAX_FAILOVER_ATTEMPTS", "3"))
    )


def get_settings() -> Settings:
    return Settings()
