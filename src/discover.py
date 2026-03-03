from __future__ import annotations

from .db import Database
from .providers.base import ProviderFatalError, ProviderRetryableError
from .providers.registry import ProviderRegistry


def run_discovery(db: Database, registry: ProviderRegistry) -> int:
    discovered = 0
    for provider in registry.all():
        try:
            models = provider.discover_models()
        except (ProviderRetryableError, ProviderFatalError):
            continue

        for model in models:
            db.enqueue(
                """
                INSERT INTO models(provider, model_name, display_name, supports_tools, supports_vision,
                                   supports_streaming, is_healthy, score, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(provider, model_name) DO UPDATE SET
                  display_name=excluded.display_name,
                  supports_tools=excluded.supports_tools,
                  supports_vision=excluded.supports_vision,
                  supports_streaming=excluded.supports_streaming,
                  is_healthy=excluded.is_healthy,
                  score=excluded.score,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    model["provider"],
                    model["model_name"],
                    model.get("display_name"),
                    model.get("supports_tools", 0),
                    model.get("supports_vision", 0),
                    model.get("supports_streaming", 1),
                    model.get("is_healthy", 1),
                    model.get("score", 0.0),
                ),
            )
            discovered += 1
    return discovered
