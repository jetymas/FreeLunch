from __future__ import annotations

from src.db import Database
from src.providers.registry import ProviderRegistry


async def run_discovery(db: Database, registry: ProviderRegistry) -> int:
    discovered = 0
    for provider in registry.all():
        models = await provider.discover_models()
        for model in models:
            db.writer.enqueue(
                """
                INSERT INTO models(provider, model_name, is_healthy, score, supports_tools, supports_vision, supports_streaming)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, model_name) DO UPDATE SET
                  is_healthy=excluded.is_healthy,
                  score=excluded.score,
                  supports_tools=excluded.supports_tools,
                  supports_vision=excluded.supports_vision,
                  supports_streaming=excluded.supports_streaming,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    model["provider"],
                    model["model_name"],
                    model["is_healthy"],
                    model["score"],
                    model["supports_tools"],
                    model["supports_vision"],
                    model["supports_streaming"],
                ),
            )
            discovered += 1
    return discovered
