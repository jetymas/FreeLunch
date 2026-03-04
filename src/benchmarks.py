from __future__ import annotations

import asyncio
import csv
import io
import re
from typing import Any

import httpx

from src.db import Database

_NOISE_TOKENS = {"free", "instruct", "chat", "preview"}
_ARENA_TREE_URL = "https://huggingface.co/api/spaces/lmarena-ai/arena-leaderboard/tree/main"
_ARENA_RAW_URL = "https://huggingface.co/spaces/lmarena-ai/arena-leaderboard/resolve/main/{path}"
_OPEN_LLM_SIZE_URL = "https://datasets-server.huggingface.co/size"
_OPEN_LLM_ROWS_URL = "https://datasets-server.huggingface.co/rows"
_OPEN_LLM_DATASET = "open-llm-leaderboard/contents"


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


async def fetch_chatbot_arena_scores(
    client: httpx.AsyncClient,
) -> dict[str, float]:
    tree_response = await client.get(_ARENA_TREE_URL)
    tree_response.raise_for_status()
    items = tree_response.json()
    csv_paths = sorted(
        item["path"]
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and item["path"].startswith("arena_hard_auto_leaderboard_")
        and item["path"].endswith(".csv")
    )
    if not csv_paths:
        return {}

    response = await client.get(_ARENA_RAW_URL.format(path=csv_paths[-1]))
    response.raise_for_status()
    rows = csv.DictReader(io.StringIO(response.text))
    scores: dict[str, float] = {}
    for row in rows:
        model_name = str(row.get("model") or row.get("Model") or row.get("key") or "")
        _update_score(scores, model_name, _safe_float(row.get("score")))
    return scores


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
    for offset in range(0, num_rows, page_size):
        response = await client.get(
            _OPEN_LLM_ROWS_URL,
            params={
                "dataset": _OPEN_LLM_DATASET,
                "config": "default",
                "split": "train",
                "offset": offset,
                "length": page_size,
            },
        )
        response.raise_for_status()
        payload = response.json()
        for wrapped_row in payload.get("rows", []):
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
    timeout = httpx.Timeout(
        timeout=float(settings.discovery_request_timeout_seconds),
        connect=float(settings.discovery_request_timeout_seconds),
    )

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        tasks: list[tuple[str, asyncio.Task[dict[str, float]]]] = []
        if settings.discovery_leaderboard_chatbot_arena_enabled:
            tasks.append(("chatbot_arena", asyncio.create_task(fetch_chatbot_arena_scores(client))))
        if settings.discovery_leaderboard_open_llm_enabled:
            tasks.append(("open_llm", asyncio.create_task(fetch_open_llm_scores(client))))

        if tasks:
            results = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
            for (source_name, _), result in zip(tasks, results, strict=False):
                if isinstance(result, BaseException):
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

    return {
        "chatbot_arena_entries": len(source_results.get("chatbot_arena", {})),
        "open_llm_entries": len(source_results.get("open_llm", {})),
        "cache_updates": len(merged),
    }
