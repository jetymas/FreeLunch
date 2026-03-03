from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class Settings:
    openrouter_api_key: str = ""
    gateway_api_key: str = ""
    database_url: str = "freelunch.db"
    app_env: str = "dev"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            gateway_api_key=os.getenv("GATEWAY_API_KEY", ""),
            database_url=os.getenv("DATABASE_URL", "freelunch.db"),
            app_env=os.getenv("APP_ENV", "dev"),
        )
