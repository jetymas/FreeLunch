from __future__ import annotations

from src.benchmarks import normalize_model_name, refresh_leaderboard_cache
from src.config import Settings
from src.db import Database, utc_now_iso
from src.providers.registry import ProviderRegistry
from src.runtime_logging import get_logger, runtime_log
from src.tokens import schedule_tokenizer_preload

logger = get_logger(__name__)


def _coerce_rank(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int | float | str):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _resolve_rank_metadata(model: dict) -> tuple[int | None, int | None]:
    provider_rank = _coerce_rank(model.get("provider_rank"))
    openrouter_rank = _coerce_rank(model.get("openrouter_rank"))

    if provider_rank is None:
        provider_rank = openrouter_rank
    if openrouter_rank is None and str(model.get("provider_id", "")) == "openrouter":
        openrouter_rank = provider_rank

    return provider_rank, openrouter_rank


def _benchmark_lookup_keys(model: dict) -> list[str]:
    raw_values = [
        str(model.get("provider_model_id", "")),
        str(model.get("name", "")),
        str(model.get("id", "")),
    ]
    keys: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        if not raw_value:
            continue
        value = raw_value.removesuffix(":free")
        candidates = [value]
        if "/" in value:
            candidates.append(value.split("/")[-1])
        for candidate in candidates:
            normalized = normalize_model_name(candidate)
            if normalized and normalized not in seen:
                keys.append(normalized)
                seen.add(normalized)
    return keys


def _apply_cached_benchmarks(db: Database, model: dict) -> dict:
    enriched = dict(model)
    if enriched.get("chatbot_arena_elo") is not None or enriched.get("open_llm_score") is not None:
        return enriched

    for key in _benchmark_lookup_keys(enriched):
        cached = db.get_leaderboard_cache(key)
        if cached is None:
            continue
        if enriched.get("chatbot_arena_elo") is None:
            enriched["chatbot_arena_elo"] = cached["chatbot_arena_elo"]
        if enriched.get("open_llm_score") is None:
            enriched["open_llm_score"] = cached["open_llm_avg_score"]
        break
    return enriched


async def run_discovery(
    db: Database,
    registry: ProviderRegistry,
    *,
    settings: Settings | None = None,
) -> int:
    discovered = 0
    now = utc_now_iso()
    if settings is not None:
        benchmark_outcome = await refresh_leaderboard_cache(db, settings)
        runtime_log(
            logger,
            "discovery.benchmark_refresh.completed",
            verbosity="verbose",
            message="Benchmark cache refresh completed",
            **benchmark_outcome,
        )
    runtime_log(
        logger,
        "discovery.started",
        verbosity="verbose",
        message="Starting provider discovery",
        provider_count=len(registry.all()),
    )
    for provider in registry.all():
        runtime_log(
            logger,
            "discovery.provider.started",
            verbosity="debug",
            message="Discovering provider models",
            provider_id=provider.name,
        )
        models = await provider.discover_models()
        seen_ids: list[str] = []
        for model in models:
            model = _apply_cached_benchmarks(db, model)
            provider_rank, openrouter_rank = _resolve_rank_metadata(model)
            schedule_tokenizer_preload(str(model.get("provider_model_id", "")))
            seen_ids.append(str(model["id"]))
            db.writer.enqueue(
                """
                INSERT INTO models(
                  id, name, provider_id, provider_model_id, provider_base_url, provider_api_key_env,
                  endpoint_id, provider_options_json,
                  context_window, max_output_tokens, supports_tools, supports_streaming, supports_vision,
                  supports_structured_output, supports_system_messages, tokenizer_family, provider_rank,
                  openrouter_rank, chatbot_arena_elo, open_llm_score, is_healthy, discovered_at, last_seen_at,
                  score_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                  provider_rank=excluded.provider_rank,
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
                    provider_rank,
                    openrouter_rank,
                    model.get("chatbot_arena_elo"),
                    model.get("open_llm_score"),
                    model.get("is_healthy", 1),
                    now,
                    now,
                    now,
                ),
            )
            discovered += 1
        db.mark_models_not_seen(provider.name, seen_ids)
        runtime_log(
            logger,
            "discovery.provider.completed",
            verbosity="verbose",
            message="Provider discovery completed",
            provider_id=provider.name,
            discovered_models=len(models),
        )
    runtime_log(
        logger,
        "discovery.completed",
        verbosity="verbose",
        message="Provider discovery completed",
        discovered=discovered,
    )
    return discovered
