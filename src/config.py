from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class Settings:
    openrouter_api_key: str = ""
    gateway_api_key: str = ""
    database_url: str = "freelunch.db"
    app_env: str = "dev"
    routing_max_attempts: int = 3
    startup_probe_limit: int = 3
    openrouter_api_base: str = "https://openrouter.ai/api/v1"

    @classmethod
    def from_env(cls, config_path: str = "config.yaml") -> Settings:
        config_data: dict[str, Any] = {}
        if Path(config_path).exists():
            with open(config_path, encoding="utf-8") as fh:
                config_data = yaml.safe_load(fh) or {}

        routing = config_data.get("routing", {})
        health = config_data.get("health", {})
        providers = config_data.get("providers", {})
        openrouter = providers.get("openrouter", {}) if isinstance(providers, dict) else {}

        return cls(
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            gateway_api_key=os.getenv("GATEWAY_API_KEY", ""),
            database_url=os.getenv("DATABASE_URL", "freelunch.db"),
            app_env=os.getenv("APP_ENV", "dev"),
            routing_max_attempts=int(os.getenv("ROUTING_MAX_ATTEMPTS", routing.get("max_attempts", 3))),
            startup_probe_limit=int(os.getenv("STARTUP_PROBE_LIMIT", health.get("startup_probe_limit", 3))),
            openrouter_api_base=os.getenv("OPENROUTER_API_BASE", openrouter.get("api_base", "https://openrouter.ai/api/v1")),
        )

    def apply_overrides(self, overrides: dict[str, Any]) -> None:
        if "routing.max_attempts" in overrides:
            self.routing_max_attempts = int(overrides["routing.max_attempts"])
        if "health.startup_probe_limit" in overrides:
            self.startup_probe_limit = int(overrides["health.startup_probe_limit"])
