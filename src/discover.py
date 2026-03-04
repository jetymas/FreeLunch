from __future__ import annotations

from src.db import Database, utc_now_iso
from src.providers.registry import ProviderRegistry


async def run_discovery(db: Database, registry: ProviderRegistry) -> int:
    discovered = 0
    now = utc_now_iso()
    for provider in registry.all():
        models = await provider.discover_models()
        for model in models:
            db.writer.enqueue(
                """
                INSERT INTO models(
                  id, name, provider_id, provider_model_id, provider_base_url, provider_api_key_env,
                  endpoint_id, provider_options_json,
                  context_window, max_output_tokens, supports_tools, supports_streaming, supports_vision,
                  supports_structured_output, supports_system_messages, tokenizer_family, openrouter_rank, chatbot_arena_elo,
                  open_llm_score, is_healthy, discovered_at, last_seen_at, score_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name,
                  provider_id=excluded.provider_id,
                  endpoint_id=excluded.endpoint_id,
                  provider_model_id=excluded.provider_model_id,
                  provider_base_url=excluded.provider_base_url,
                  provider_api_key_env=excluded.provider_api_key_env,
                  provider_options_json=excluded.provider_options_json,
                  is_active=1,
                  last_seen_at=excluded.last_seen_at,
                  is_healthy=excluded.is_healthy,
                  supports_tools=excluded.supports_tools,
                  supports_streaming=excluded.supports_streaming,
                  supports_vision=excluded.supports_vision,
                  supports_structured_output=excluded.supports_structured_output,
                  supports_system_messages=excluded.supports_system_messages,
                  context_window=excluded.context_window,
                  max_output_tokens=excluded.max_output_tokens,
                  tokenizer_family=excluded.tokenizer_family,
                  openrouter_rank=excluded.openrouter_rank,
                  chatbot_arena_elo=excluded.chatbot_arena_elo,
                  open_llm_score=excluded.open_llm_score
                """,
                (
                    model["id"],
                    model["name"],
                    model["provider_id"],
                    model["provider_model_id"],
                    model["provider_base_url"],
                    model["provider_api_key_env"],
                    model.get("endpoint_id"),
                    model.get("provider_options_json"),
                    model.get("context_window", 4096),
                    model.get("max_output_tokens"),
                    model.get("supports_tools", 0),
                    model.get("supports_streaming", 1),
                    model.get("supports_vision", 0),
                    model.get("supports_structured_output", 0),
                    model.get("supports_system_messages", 1),
                    model.get("tokenizer_family"),
                    model.get("openrouter_rank"),
                    model.get("chatbot_arena_elo"),
                    model.get("open_llm_score"),
                    model.get("is_healthy", 1),
                    now,
                    now,
                    now,
                ),
            )
            discovered += 1
    return discovered
