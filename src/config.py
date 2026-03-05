from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import yaml


@dataclass(slots=True)
class Settings:
    OVERRIDABLE_KEYS: ClassVar[set[str]] = {
        "discovery.interval_minutes",
        "ranking.interval_minutes",
        "routing.max_attempts",
        "routing.enable_request_preference_headers",
        "ranking.fallback_model",
        "providers.openrouter.active_probe_enabled",
        "health.probe_interval_minutes",
        "health.probe_timeout_seconds",
        "health.probe_concurrency",
        "health.max_probes_per_run",
        "health.stale_after_minutes",
        "health.top_n_stale_probe",
        "health.startup_probe_limit",
        "health.consecutive_failures_threshold",
        "health.cooldown_minutes",
        "health.max_backoff_exponent",
        "health.probe_max_tokens",
        "health.daily_request_budget_by_provider.openrouter",
        "logging.runtime_enabled",
        "logging.runtime_verbosity",
        "logging.request_log_retention_days",
    }
    DEFAULT_RANKING_WEIGHTS: ClassVar[dict[str, float]] = {
        "benchmark_score": 0.30,
        "real_world_usage": 0.15,
        "latency": 0.20,
        "availability": 0.20,
        "context_window": 0.10,
        "feature_support": 0.05,
    }
    DEFAULT_PROVIDER_PROBE_BUDGET: ClassVar[int] = 5

    openrouter_api_key: str = ""
    gateway_api_key: str = ""
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8000
    gateway_workers: int = 1
    gateway_log_level: str = "info"
    database_url: str = "freelunch.db"
    database_busy_timeout_ms: int = 5000
    app_env: str = "dev"
    providers_enabled: tuple[str, ...] = ("openrouter",)
    provider_enabled: dict[str, bool] = field(default_factory=dict)
    provider_discovery_enabled: dict[str, bool] = field(default_factory=dict)
    provider_inference_enabled: dict[str, bool] = field(default_factory=dict)
    provider_active_probe_enabled: dict[str, bool] = field(default_factory=dict)
    provider_bootstrap_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    openrouter_enabled: bool = True
    openrouter_discovery_enabled: bool = True
    openrouter_inference_enabled: bool = True
    openrouter_dev_stub_enabled: bool = False
    discovery_interval_minutes: int = 30
    discovery_request_timeout_seconds: int = 15
    discovery_leaderboard_chatbot_arena_enabled: bool = True
    discovery_leaderboard_chatbot_arena_cache_hours: int = 24
    discovery_leaderboard_open_llm_enabled: bool = True
    discovery_leaderboard_open_llm_cache_hours: int = 24
    ranking_interval_minutes: int = 15
    routing_max_attempts: int = 3
    routing_enable_request_preference_headers: bool = True
    openrouter_api_base: str = "https://openrouter.ai/api/v1"
    openrouter_active_probe_enabled: bool = True
    ranking_weights: dict[str, float] = field(
        default_factory=lambda: dict(Settings.DEFAULT_RANKING_WEIGHTS)
    )
    ranking_fallback_model: str = "openrouter/openrouter/free"
    health_probe_interval_minutes: int = 180
    health_probe_timeout_seconds: int = 15
    health_probe_concurrency: int = 1
    health_max_probes_per_run: int = 1
    health_stale_after_minutes: int = 360
    health_top_n_stale_probe: int = 3
    health_startup_probe_limit: int = 2
    health_consecutive_failures_threshold: int = 3
    health_cooldown_minutes: int = 30
    health_max_backoff_exponent: int = 4
    health_probe_max_tokens: int = 1
    health_daily_request_budget_by_provider: dict[str, int] = field(
        default_factory=lambda: {"openrouter": 5}
    )
    logging_request_log_enabled: bool = True
    logging_log_queue_size: int = 5000
    logging_request_log_retention_days: int = 30
    logging_runtime_enabled: bool = True
    logging_runtime_verbosity: str = "concise"
    logging_runtime_queue_size: int = 1000

    def __post_init__(self) -> None:
        self.provider_enabled = self._coerce_bool_mapping(self.provider_enabled)
        self.provider_discovery_enabled = self._coerce_bool_mapping(self.provider_discovery_enabled)
        self.provider_inference_enabled = self._coerce_bool_mapping(self.provider_inference_enabled)
        self.provider_active_probe_enabled = self._coerce_bool_mapping(
            self.provider_active_probe_enabled
        )
        self.provider_bootstrap_config = self._coerce_provider_bootstrap_config(
            self.provider_bootstrap_config
        )
        self.health_daily_request_budget_by_provider = self._coerce_int_mapping(
            self.health_daily_request_budget_by_provider
        )

        if "openrouter" not in self.provider_enabled:
            self.provider_enabled["openrouter"] = bool(self.openrouter_enabled)
        if "openrouter" not in self.provider_discovery_enabled:
            self.provider_discovery_enabled["openrouter"] = bool(self.openrouter_discovery_enabled)
        if "openrouter" not in self.provider_inference_enabled:
            self.provider_inference_enabled["openrouter"] = bool(self.openrouter_inference_enabled)
        if "openrouter" not in self.provider_active_probe_enabled:
            self.provider_active_probe_enabled["openrouter"] = bool(
                self.openrouter_active_probe_enabled
            )
        if "openrouter" not in self.provider_bootstrap_config:
            self.provider_bootstrap_config["openrouter"] = {}

        self.openrouter_enabled = bool(self.provider_enabled.get("openrouter", False))
        self.openrouter_discovery_enabled = bool(
            self.provider_discovery_enabled.get("openrouter", False)
        )
        self.openrouter_inference_enabled = bool(
            self.provider_inference_enabled.get("openrouter", False)
        )
        self.openrouter_active_probe_enabled = bool(
            self.provider_active_probe_enabled.get("openrouter", True)
        )

    @classmethod
    def from_env(cls, config_path: str = "config.yaml") -> Settings:
        config_data: dict[str, Any] = {}
        if Path(config_path).exists():
            with open(config_path, encoding="utf-8") as fh:
                config_data = yaml.safe_load(fh) or {}

        gateway = config_data.get("gateway", {})
        discovery = config_data.get("discovery", {})
        leaderboard = discovery.get("leaderboard", {}) if isinstance(discovery, dict) else {}
        chatbot_arena = (
            leaderboard.get("chatbot_arena", {}) if isinstance(leaderboard, dict) else {}
        )
        open_llm = leaderboard.get("open_llm", {}) if isinstance(leaderboard, dict) else {}
        routing = config_data.get("routing", {})
        health = config_data.get("health", {})
        ranking = config_data.get("ranking", {})
        logging = config_data.get("logging", {})
        database = config_data.get("database", {})
        providers = config_data.get("providers", {})
        provider_sections = cls._coerce_provider_sections(providers)
        openrouter = provider_sections.get("openrouter", {})
        enabled_providers = cls._coerce_string_list(
            providers.get("enabled", ["openrouter"])
            if isinstance(providers, dict)
            else ["openrouter"]
        )

        provider_ids = set(provider_sections.keys())
        provider_ids.update(enabled_providers)
        provider_ids.add("openrouter")
        provider_bootstrap_config: dict[str, dict[str, Any]] = {}

        provider_enabled: dict[str, bool] = {}
        provider_discovery_enabled: dict[str, bool] = {}
        provider_inference_enabled: dict[str, bool] = {}
        provider_active_probe_enabled: dict[str, bool] = {}
        for provider_id in sorted(provider_ids):
            section = provider_sections.get(provider_id, {})
            provider_bootstrap_config[provider_id] = (
                dict(section) if isinstance(section, Mapping) else {}
            )
            globally_enabled = provider_id in enabled_providers
            configured_enabled = globally_enabled and bool(section.get("enabled", True))
            provider_enabled[provider_id] = configured_enabled
            provider_discovery_enabled[provider_id] = configured_enabled and bool(
                section.get("discovery_enabled", True)
            )
            provider_inference_enabled[provider_id] = configured_enabled and bool(
                section.get("inference_enabled", True)
            )

            active_probe_default = section.get("active_probe_enabled", True)
            if provider_id == "openrouter":
                provider_active_probe_enabled[provider_id] = cls._env_bool(
                    "OPENROUTER_ACTIVE_PROBE_ENABLED",
                    active_probe_default,
                )
            else:
                provider_active_probe_enabled[provider_id] = bool(active_probe_default)

        ranking_weights = dict(cls.DEFAULT_RANKING_WEIGHTS)
        ranking_weights.update(cls._coerce_float_mapping(ranking.get("weights", {})))
        probe_budgets = {
            provider_id: cls.DEFAULT_PROVIDER_PROBE_BUDGET for provider_id in provider_ids
        }
        probe_budgets.update(
            cls._coerce_int_mapping(health.get("daily_request_budget_by_provider", {}))
        )

        return cls(
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            gateway_api_key=os.getenv("GATEWAY_API_KEY", ""),
            gateway_host=str(gateway.get("host", "0.0.0.0")),
            gateway_port=max(int(gateway.get("port", 8000)), 1),
            gateway_workers=max(int(gateway.get("workers", 1)), 1),
            gateway_log_level=str(gateway.get("log_level", "info")),
            database_url=os.getenv("DATABASE_URL", database.get("path", "freelunch.db")),
            database_busy_timeout_ms=max(int(database.get("busy_timeout_ms", 5000)), 1),
            app_env=os.getenv("APP_ENV", "dev"),
            providers_enabled=tuple(enabled_providers),
            provider_enabled=provider_enabled,
            provider_discovery_enabled=provider_discovery_enabled,
            provider_inference_enabled=provider_inference_enabled,
            provider_active_probe_enabled=provider_active_probe_enabled,
            provider_bootstrap_config=provider_bootstrap_config,
            openrouter_enabled=provider_enabled.get("openrouter", False),
            openrouter_discovery_enabled=provider_discovery_enabled.get("openrouter", False),
            openrouter_inference_enabled=provider_inference_enabled.get("openrouter", False),
            openrouter_dev_stub_enabled=cls._env_bool(
                "OPENROUTER_DEV_STUB_ENABLED",
                openrouter.get("dev_stub_enabled", False),
            ),
            discovery_interval_minutes=max(int(discovery.get("interval_minutes", 30)), 1),
            discovery_request_timeout_seconds=max(
                int(discovery.get("request_timeout_seconds", 15)),
                1,
            ),
            discovery_leaderboard_chatbot_arena_enabled=bool(chatbot_arena.get("enabled", True)),
            discovery_leaderboard_chatbot_arena_cache_hours=max(
                int(chatbot_arena.get("cache_hours", 24)),
                1,
            ),
            discovery_leaderboard_open_llm_enabled=bool(open_llm.get("enabled", True)),
            discovery_leaderboard_open_llm_cache_hours=max(
                int(open_llm.get("cache_hours", 24)),
                1,
            ),
            ranking_interval_minutes=max(int(ranking.get("interval_minutes", 15)), 1),
            routing_max_attempts=int(
                os.getenv("ROUTING_MAX_ATTEMPTS", routing.get("max_attempts", 3))
            ),
            routing_enable_request_preference_headers=cls._env_bool(
                "ROUTING_ENABLE_REQUEST_PREFERENCE_HEADERS",
                routing.get("enable_request_preference_headers", True),
            ),
            openrouter_api_base=os.getenv(
                "OPENROUTER_API_BASE", openrouter.get("api_base", "https://openrouter.ai/api/v1")
            ),
            openrouter_active_probe_enabled=provider_active_probe_enabled.get("openrouter", True),
            ranking_weights=ranking_weights,
            ranking_fallback_model=str(ranking.get("fallback_model", "openrouter/openrouter/free")),
            health_probe_interval_minutes=int(health.get("probe_interval_minutes", 180)),
            health_probe_timeout_seconds=int(health.get("probe_timeout_seconds", 15)),
            health_probe_concurrency=max(int(health.get("probe_concurrency", 1)), 1),
            health_max_probes_per_run=max(int(health.get("max_probes_per_run", 1)), 0),
            health_stale_after_minutes=max(int(health.get("stale_after_minutes", 360)), 1),
            health_top_n_stale_probe=max(int(health.get("top_n_stale_probe", 3)), 0),
            health_startup_probe_limit=max(
                int(os.getenv("STARTUP_PROBE_LIMIT", health.get("startup_probe_limit", 2))),
                0,
            ),
            health_consecutive_failures_threshold=max(
                int(health.get("consecutive_failures_threshold", 3)),
                1,
            ),
            health_cooldown_minutes=max(int(health.get("cooldown_minutes", 30)), 1),
            health_max_backoff_exponent=max(int(health.get("max_backoff_exponent", 4)), 0),
            health_probe_max_tokens=max(int(health.get("probe_max_tokens", 1)), 1),
            health_daily_request_budget_by_provider=probe_budgets,
            logging_request_log_enabled=bool(logging.get("request_log_enabled", True)),
            logging_log_queue_size=max(int(logging.get("log_queue_size", 5000)), 1),
            logging_request_log_retention_days=max(
                int(logging.get("request_log_retention_days", 30)),
                1,
            ),
            logging_runtime_enabled=bool(logging.get("runtime_enabled", True)),
            logging_runtime_verbosity=str(logging.get("runtime_verbosity", "concise")),
            logging_runtime_queue_size=max(int(logging.get("runtime_queue_size", 1000)), 1),
        )

    def apply_overrides(self, overrides: dict[str, Any]) -> None:
        if "discovery.interval_minutes" in overrides:
            self.discovery_interval_minutes = max(int(overrides["discovery.interval_minutes"]), 1)
        if "ranking.interval_minutes" in overrides:
            self.ranking_interval_minutes = max(int(overrides["ranking.interval_minutes"]), 1)
        if "routing.max_attempts" in overrides:
            self.routing_max_attempts = int(overrides["routing.max_attempts"])
        if "routing.enable_request_preference_headers" in overrides:
            self.routing_enable_request_preference_headers = bool(
                overrides["routing.enable_request_preference_headers"]
            )
        if "health.probe_interval_minutes" in overrides:
            self.health_probe_interval_minutes = max(
                int(overrides["health.probe_interval_minutes"]), 1
            )
        if "health.probe_timeout_seconds" in overrides:
            self.health_probe_timeout_seconds = max(
                int(overrides["health.probe_timeout_seconds"]), 1
            )
        if "health.probe_concurrency" in overrides:
            self.health_probe_concurrency = max(int(overrides["health.probe_concurrency"]), 1)
        if "health.startup_probe_limit" in overrides:
            self.health_startup_probe_limit = int(overrides["health.startup_probe_limit"])
        if "health.max_probes_per_run" in overrides:
            self.health_max_probes_per_run = max(int(overrides["health.max_probes_per_run"]), 0)
        if "health.stale_after_minutes" in overrides:
            self.health_stale_after_minutes = max(int(overrides["health.stale_after_minutes"]), 1)
        if "health.top_n_stale_probe" in overrides:
            self.health_top_n_stale_probe = max(int(overrides["health.top_n_stale_probe"]), 0)
        if "health.consecutive_failures_threshold" in overrides:
            self.health_consecutive_failures_threshold = max(
                int(overrides["health.consecutive_failures_threshold"]),
                1,
            )
        if "health.cooldown_minutes" in overrides:
            self.health_cooldown_minutes = max(int(overrides["health.cooldown_minutes"]), 1)
        if "health.max_backoff_exponent" in overrides:
            self.health_max_backoff_exponent = max(int(overrides["health.max_backoff_exponent"]), 0)
        if "health.probe_max_tokens" in overrides:
            self.health_probe_max_tokens = max(int(overrides["health.probe_max_tokens"]), 1)
        if "ranking.fallback_model" in overrides:
            self.ranking_fallback_model = str(overrides["ranking.fallback_model"])
        if "logging.request_log_retention_days" in overrides:
            self.logging_request_log_retention_days = max(
                int(overrides["logging.request_log_retention_days"]),
                1,
            )
        if "logging.runtime_enabled" in overrides:
            self.logging_runtime_enabled = bool(overrides["logging.runtime_enabled"])
        if "logging.runtime_verbosity" in overrides:
            self.logging_runtime_verbosity = str(overrides["logging.runtime_verbosity"])
        for key, raw in overrides.items():
            provider_id = self._provider_from_active_probe_key(key)
            if provider_id is not None:
                self.provider_active_probe_enabled[provider_id] = bool(raw)
                if provider_id == "openrouter":
                    self.openrouter_active_probe_enabled = bool(raw)
                continue

            provider_id = self._provider_from_probe_budget_key(key)
            if provider_id is not None:
                self.health_daily_request_budget_by_provider[provider_id] = max(int(raw), 0)
        for key in self.DEFAULT_RANKING_WEIGHTS:
            override_key = f"ranking.weights.{key}"
            if override_key in overrides:
                self.ranking_weights[key] = float(overrides[override_key])

    @property
    def startup_probe_limit(self) -> int:
        return self.health_startup_probe_limit

    @property
    def known_provider_ids(self) -> tuple[str, ...]:
        provider_ids = set(self.providers_enabled)
        provider_ids.update(self.provider_enabled.keys())
        provider_ids.update(self.provider_discovery_enabled.keys())
        provider_ids.update(self.provider_inference_enabled.keys())
        provider_ids.update(self.provider_active_probe_enabled.keys())
        provider_ids.update(self.provider_bootstrap_config.keys())
        provider_ids.update(self.health_daily_request_budget_by_provider.keys())
        provider_ids.add("openrouter")
        return tuple(sorted(str(provider_id) for provider_id in provider_ids if str(provider_id)))

    def is_provider_enabled(self, provider_id: str) -> bool:
        normalized = provider_id.strip()
        if not normalized:
            return False
        return bool(self.provider_enabled.get(normalized, normalized in self.providers_enabled))

    def is_provider_discovery_enabled(self, provider_id: str) -> bool:
        normalized = provider_id.strip()
        if not normalized:
            return False
        default = self.is_provider_enabled(normalized)
        return default and bool(self.provider_discovery_enabled.get(normalized, default))

    def is_provider_inference_enabled(self, provider_id: str) -> bool:
        normalized = provider_id.strip()
        if not normalized:
            return False
        default = self.is_provider_enabled(normalized)
        return default and bool(self.provider_inference_enabled.get(normalized, default))

    def is_provider_active_probe_enabled(self, provider_id: str) -> bool:
        normalized = provider_id.strip()
        if not normalized:
            return False
        return bool(
            self.provider_active_probe_enabled.get(normalized, self.openrouter_active_probe_enabled)
            if normalized == "openrouter"
            else self.provider_active_probe_enabled.get(normalized, True)
        )

    def get_provider_bootstrap_config(self, provider_id: str) -> dict[str, Any]:
        normalized = provider_id.strip()
        if not normalized:
            return {}
        return dict(self.provider_bootstrap_config.get(normalized, {}))

    @classmethod
    def _coerce_float_mapping(cls, value: Any) -> dict[str, float]:
        if not isinstance(value, Mapping):
            return {}
        return {str(key): float(raw) for key, raw in value.items()}

    @classmethod
    def _coerce_int_mapping(cls, value: Any) -> dict[str, int]:
        if not isinstance(value, Mapping):
            return {}
        return {str(key): int(raw) for key, raw in value.items()}

    @classmethod
    def _coerce_bool_mapping(cls, value: Any) -> dict[str, bool]:
        if not isinstance(value, Mapping):
            return {}
        return {str(key): bool(raw) for key, raw in value.items()}

    @classmethod
    def _coerce_provider_bootstrap_config(cls, value: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(value, Mapping):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if not key or not isinstance(raw_value, Mapping):
                continue
            out[key] = dict(raw_value)
        return out

    @classmethod
    def _coerce_string_list(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    @classmethod
    def _coerce_provider_sections(cls, providers: Any) -> dict[str, Mapping[str, Any]]:
        if not isinstance(providers, Mapping):
            return {}
        out: dict[str, Mapping[str, Any]] = {}
        for raw_key, raw_value in providers.items():
            key = str(raw_key)
            if key == "enabled" or not isinstance(raw_value, Mapping):
                continue
            out[key] = raw_value
        return out

    @classmethod
    def _provider_from_active_probe_key(cls, key: str) -> str | None:
        prefix = "providers."
        suffix = ".active_probe_enabled"
        if not key.startswith(prefix) or not key.endswith(suffix):
            return None
        provider_id = key[len(prefix) : len(key) - len(suffix)].strip()
        return provider_id or None

    @classmethod
    def _provider_from_probe_budget_key(cls, key: str) -> str | None:
        prefix = "health.daily_request_budget_by_provider."
        if not key.startswith(prefix):
            return None
        provider_id = key[len(prefix) :].strip()
        return provider_id or None

    @staticmethod
    def _env_bool(name: str, default: Any) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return bool(default)
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def is_overridable(cls, key: str) -> bool:
        return (
            key in cls.OVERRIDABLE_KEYS
            or key.startswith("ranking.weights.")
            or cls._provider_from_active_probe_key(key) is not None
            or cls._provider_from_probe_budget_key(key) is not None
        )

    def public_settings(self) -> dict[str, Any]:
        effective: dict[str, Any] = {
            "gateway.host": self.gateway_host,
            "gateway.port": self.gateway_port,
            "gateway.workers": self.gateway_workers,
            "gateway.log_level": self.gateway_log_level,
            "database.path": self.database_url,
            "database.busy_timeout_ms": self.database_busy_timeout_ms,
            "providers.enabled": list(self.providers_enabled),
            "providers.openrouter.dev_stub_enabled": self.openrouter_dev_stub_enabled,
            "discovery.interval_minutes": self.discovery_interval_minutes,
            "discovery.request_timeout_seconds": self.discovery_request_timeout_seconds,
            "discovery.leaderboard.chatbot_arena.enabled": self.discovery_leaderboard_chatbot_arena_enabled,
            "discovery.leaderboard.chatbot_arena.cache_hours": self.discovery_leaderboard_chatbot_arena_cache_hours,
            "discovery.leaderboard.open_llm.enabled": self.discovery_leaderboard_open_llm_enabled,
            "discovery.leaderboard.open_llm.cache_hours": self.discovery_leaderboard_open_llm_cache_hours,
            "ranking.interval_minutes": self.ranking_interval_minutes,
            "routing.max_attempts": self.routing_max_attempts,
            "routing.enable_request_preference_headers": self.routing_enable_request_preference_headers,
            "ranking.fallback_model": self.ranking_fallback_model,
            "ranking.weights": dict(self.ranking_weights),
            "health.probe_interval_minutes": self.health_probe_interval_minutes,
            "health.probe_timeout_seconds": self.health_probe_timeout_seconds,
            "health.probe_concurrency": self.health_probe_concurrency,
            "health.max_probes_per_run": self.health_max_probes_per_run,
            "health.stale_after_minutes": self.health_stale_after_minutes,
            "health.top_n_stale_probe": self.health_top_n_stale_probe,
            "health.startup_probe_limit": self.health_startup_probe_limit,
            "health.consecutive_failures_threshold": self.health_consecutive_failures_threshold,
            "health.cooldown_minutes": self.health_cooldown_minutes,
            "health.max_backoff_exponent": self.health_max_backoff_exponent,
            "health.probe_max_tokens": self.health_probe_max_tokens,
            "health.daily_request_budget_by_provider": dict(
                self.health_daily_request_budget_by_provider
            ),
            "logging.request_log_enabled": self.logging_request_log_enabled,
            "logging.log_queue_size": self.logging_log_queue_size,
            "logging.request_log_retention_days": self.logging_request_log_retention_days,
            "logging.runtime_enabled": self.logging_runtime_enabled,
            "logging.runtime_verbosity": self.logging_runtime_verbosity,
            "logging.runtime_queue_size": self.logging_runtime_queue_size,
        }
        for provider_id in self.known_provider_ids:
            effective[f"providers.{provider_id}.enabled"] = self.is_provider_enabled(provider_id)
            effective[f"providers.{provider_id}.discovery_enabled"] = (
                self.is_provider_discovery_enabled(provider_id)
            )
            effective[f"providers.{provider_id}.inference_enabled"] = (
                self.is_provider_inference_enabled(provider_id)
            )
            effective[f"providers.{provider_id}.active_probe_enabled"] = (
                self.is_provider_active_probe_enabled(provider_id)
            )
        return effective
