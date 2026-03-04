from __future__ import annotations

import asyncio
import csv
import io
import pickle
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.db import Database
from src.runtime_logging import get_logger, runtime_log

logger = get_logger(__name__)

_NOISE_TOKENS = {"free", "instruct", "chat", "preview"}
_ARENA_TREE_URL = "https://huggingface.co/api/spaces/lmarena-ai/arena-leaderboard/tree/main"
_ARENA_RAW_URL = "https://huggingface.co/spaces/lmarena-ai/arena-leaderboard/resolve/main/{path}"
_OPEN_LLM_SIZE_URL = "https://datasets-server.huggingface.co/size"
_OPEN_LLM_ROWS_URL = "https://datasets-server.huggingface.co/rows"
_OPEN_LLM_DATASET = "open-llm-leaderboard/contents"
_OPEN_LLM_MAX_ROWS_PAGE_SIZE = 100
_ARENA_ELO_COLUMNS = ("arena elo", "elo", "rating", "arena rating")
_CHATBOT_ARENA_SCORE_COLUMNS = (
    "arena score",
    "arena hard score",
    "arena elo",
    "arena rating",
    "rating",
    "score",
)


def normalize_model_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", name.lower())
    tokens = [token for token in cleaned.split() if token and token not in _NOISE_TOKENS]
    return " ".join(tokens)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _update_score(scores: dict[str, float], model_name: str, score: float | None) -> None:
    normalized = normalize_model_name(model_name)
    if not normalized or score is None:
        return
    previous = scores.get(normalized)
    if previous is None or score > previous:
        scores[normalized] = score


def _cache_cutoff_iso(max_age_hours: int) -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(hours=max(max_age_hours, 1)))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _count_cached_source_entries(db: Database, *, column_name: str, max_age_hours: int) -> int:
    cutoff = _cache_cutoff_iso(max_age_hours)
    with db.read_conn() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM leaderboard_cache
            WHERE {column_name} IS NOT NULL
              AND fetched_at >= ?
            """,
            (cutoff,),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def _source_cache_ready(db: Database, *, column_name: str, max_age_hours: int) -> bool:
    return _count_cached_source_entries(
        db,
        column_name=column_name,
        max_age_hours=max_age_hours,
    ) > 0


def _matching_paths(
    items: list[dict[str, Any]],
    *,
    prefix: str,
    suffix: str,
) -> list[str]:
    matching = sorted(
        item["path"]
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and item["path"].startswith(prefix)
        and item["path"].endswith(suffix)
    )
    return matching


def _chatbot_arena_model_name(row: dict[str, str]) -> str:
    return str(row.get("key") or row.get("Model") or row.get("model") or "")


def _chatbot_arena_score(row: dict[str, str]) -> float | None:
    normalized_keys = {str(key).strip().lower(): key for key in row}
    for column_name in (*_ARENA_ELO_COLUMNS, *_CHATBOT_ARENA_SCORE_COLUMNS):
        original_key = normalized_keys.get(column_name)
        if original_key:
            return _safe_float(row.get(original_key))

    ranked_candidates = sorted(
        normalized_keys.items(),
        key=lambda item: (
            0
            if "arena" in item[0] and any(token in item[0] for token in ("elo", "rating", "score"))
            else 1,
            item[0],
        ),
    )
    for normalized_key, original_key in ranked_candidates:
        if "arena" not in normalized_key:
            continue
        if not any(token in normalized_key for token in ("elo", "rating", "score")):
            continue
        score = _safe_float(row.get(original_key))
        if score is not None:
            return score
    return None


def _parse_chatbot_arena_csv(text: str) -> dict[str, float]:
    rows = csv.DictReader(io.StringIO(text))
    scores: dict[str, float] = {}
    for row in rows:
        _update_score(scores, _chatbot_arena_model_name(row), _chatbot_arena_score(row))
    return scores


def _parse_chatbot_arena_snapshot(payload: Any) -> dict[str, float]:
    if isinstance(payload, dict):
        direct_scores: dict[str, float] = {}
        for model_name, value in payload.items():
            if not isinstance(model_name, str):
                continue
            if isinstance(value, int | float):
                _update_score(direct_scores, model_name, float(value))
                continue
            if isinstance(value, dict):
                for key in _ARENA_ELO_COLUMNS:
                    score = _safe_float(value.get(key))
                    if score is not None:
                        _update_score(direct_scores, model_name, score)
                        break
                else:
                    for key, candidate in value.items():
                        if not isinstance(key, str):
                            continue
                        normalized_key = key.strip().lower()
                        if normalized_key in _ARENA_ELO_COLUMNS:
                            _update_score(direct_scores, model_name, _safe_float(candidate))
                            break
        if direct_scores:
            return direct_scores

        nested_scores: dict[str, float] = {}
        for value in payload.values():
            nested_scores.update(_parse_chatbot_arena_snapshot(value))
        return nested_scores

    if isinstance(payload, list | tuple):
        scores: dict[str, float] = {}
        for item in payload:
            scores.update(_parse_chatbot_arena_snapshot(item))
        return scores

    return {}


async def fetch_chatbot_arena_scores(
    client: httpx.AsyncClient,
) -> dict[str, float]:
    tree_response = await client.get(_ARENA_TREE_URL)
    tree_response.raise_for_status()
    items = tree_response.json()
    if not isinstance(items, list):
        return {}

    elo_snapshot_paths = _matching_paths(
        items,
        prefix="elo_results_",
        suffix=".pkl",
    )
    for elo_snapshot_path in reversed(elo_snapshot_paths):
        response = await client.get(_ARENA_RAW_URL.format(path=elo_snapshot_path))
        response.raise_for_status()
        try:
            parsed = _parse_chatbot_arena_snapshot(pickle.loads(response.content))
        except Exception:
            parsed = {}
        if parsed:
            return parsed

    leaderboard_table_paths = _matching_paths(
        items,
        prefix="leaderboard_table_",
        suffix=".csv",
    )
    for leaderboard_table_path in reversed(leaderboard_table_paths):
        response = await client.get(_ARENA_RAW_URL.format(path=leaderboard_table_path))
        response.raise_for_status()
        parsed = _parse_chatbot_arena_csv(response.text)
        if parsed:
            return parsed

    arena_hard_paths = _matching_paths(
        items,
        prefix="arena_hard_auto_leaderboard_",
        suffix=".csv",
    )
    for arena_hard_path in reversed(arena_hard_paths):
        response = await client.get(_ARENA_RAW_URL.format(path=arena_hard_path))
        response.raise_for_status()
        parsed = _parse_chatbot_arena_csv(response.text)
        if parsed:
            return parsed

    return {}


async def _fetch_open_llm_rows_page(
    client: httpx.AsyncClient,
    *,
    offset: int,
    length: int,
) -> list[dict[str, Any]]:
    response = await client.get(
        _OPEN_LLM_ROWS_URL,
        params={
            "dataset": _OPEN_LLM_DATASET,
            "config": "default",
            "split": "train",
            "offset": offset,
            "length": length,
        },
    )
    response.raise_for_status()
    payload = response.json()
    return [row for row in payload.get("rows", []) if isinstance(row, dict)]


async def fetch_open_llm_scores(
    client: httpx.AsyncClient,
    *,
    page_size: int = 500,
) -> dict[str, float]:
    size_response = await client.get(_OPEN_LLM_SIZE_URL, params={"dataset": _OPEN_LLM_DATASET})
    size_response.raise_for_status()
    size_payload = size_response.json()
    num_rows = int(size_payload.get("size", {}).get("dataset", {}).get("num_rows", 0) or 0)
    if num_rows <= 0:
        return {}

    scores: dict[str, float] = {}
    effective_page_size = max(min(int(page_size), _OPEN_LLM_MAX_ROWS_PAGE_SIZE), 1)
    for offset in range(0, num_rows, effective_page_size):
        try:
            rows = await _fetch_open_llm_rows_page(
                client,
                offset=offset,
                length=effective_page_size,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 422 or effective_page_size <= 1:
                raise
            effective_page_size = min(effective_page_size, _OPEN_LLM_MAX_ROWS_PAGE_SIZE)
            rows = await _fetch_open_llm_rows_page(
                client,
                offset=offset,
                length=effective_page_size,
            )

        for wrapped_row in rows:
            if not isinstance(wrapped_row, dict):
                continue
            row = wrapped_row.get("row", {})
            if not isinstance(row, dict):
                continue
            model_name = str(row.get("fullname") or row.get("eval_name") or "")
            _update_score(scores, model_name, _safe_float(row.get("Average ⬆️")))
    return scores


async def refresh_leaderboard_cache(db: Database, settings) -> dict[str, int]:
    source_results: dict[str, dict[str, float]] = {}
    source_failures: dict[str, str] = {}
    timeout = httpx.Timeout(
        timeout=float(settings.discovery_request_timeout_seconds),
        connect=float(settings.discovery_request_timeout_seconds),
    )

    runtime_log(
        logger,
        "benchmarks.refresh.started",
        verbosity="verbose",
        message="Refreshing leaderboard cache",
        chatbot_arena_enabled=bool(settings.discovery_leaderboard_chatbot_arena_enabled),
        open_llm_enabled=bool(settings.discovery_leaderboard_open_llm_enabled),
    )

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        tasks: list[tuple[str, asyncio.Task[dict[str, float]]]] = []
        if settings.discovery_leaderboard_chatbot_arena_enabled:
            if _source_cache_ready(
                db,
                column_name="chatbot_arena_elo",
                max_age_hours=settings.discovery_leaderboard_chatbot_arena_cache_hours,
            ):
                source_results["chatbot_arena"] = {}
            else:
                tasks.append(
                    ("chatbot_arena", asyncio.create_task(fetch_chatbot_arena_scores(client)))
                )
        if settings.discovery_leaderboard_open_llm_enabled:
            if _source_cache_ready(
                db,
                column_name="open_llm_avg_score",
                max_age_hours=settings.discovery_leaderboard_open_llm_cache_hours,
            ):
                source_results["open_llm"] = {}
            else:
                tasks.append(("open_llm", asyncio.create_task(fetch_open_llm_scores(client))))

        if tasks:
            results = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
            for (source_name, _), result in zip(tasks, results, strict=False):
                if isinstance(result, BaseException):
                    source_failures[source_name] = str(result)[:500]
                    runtime_log(
                        logger,
                        "benchmarks.source.failed",
                        verbosity="concise",
                        level=30,
                        message="Leaderboard source refresh failed",
                        source_name=source_name,
                        error=str(result)[:500],
                    )
                    continue
                source_results[source_name] = dict(result)

    merged: dict[str, dict[str, float | None]] = {}
    for key, score in source_results.get("chatbot_arena", {}).items():
        merged.setdefault(key, {})["chatbot_arena_elo"] = score
    for key, score in source_results.get("open_llm", {}).items():
        merged.setdefault(key, {})["open_llm_avg_score"] = score

    for key, values in merged.items():
        db.upsert_leaderboard_cache(
            key,
            chatbot_arena_elo=values.get("chatbot_arena_elo"),
            open_llm_avg_score=values.get("open_llm_avg_score"),
        )

    outcome = {
        "chatbot_arena_entries": max(
            len(source_results.get("chatbot_arena", {})),
            _count_cached_source_entries(
                db,
                column_name="chatbot_arena_elo",
                max_age_hours=settings.discovery_leaderboard_chatbot_arena_cache_hours,
            ),
        ),
        "open_llm_entries": max(
            len(source_results.get("open_llm", {})),
            _count_cached_source_entries(
                db,
                column_name="open_llm_avg_score",
                max_age_hours=settings.discovery_leaderboard_open_llm_cache_hours,
            ),
        ),
        "cache_updates": len(merged),
    }
    runtime_log(
        logger,
        "benchmarks.refresh.completed",
        verbosity="verbose",
        message="Leaderboard cache refresh completed",
        failures=source_failures,
        **outcome,
    )
    return outcome
