from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.db import Database, utc_now_iso


class NoHealthyModelsError(Exception):
    """Raised when no healthy, capability-compatible models are available."""


@dataclass(slots=True)
class RoutingPreferences:
    preference: str = "balanced"
    max_latency_ms: int | None = None
    min_context_tokens: int | None = None


@dataclass(slots=True)
class RoutingRequirements:
    requested_model: str | None = None
    requires_tools: bool = False
    requires_vision: bool = False
    requires_streaming: bool = False
    requires_structured_output: bool = False
    requires_system_messages: bool = True
    min_context_window: int = 0
    min_output_tokens: int = 0


def _serialize_candidate(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0],
        "provider_id": row[1],
        "provider_model_id": row[2],
        "composite_score": float(row[3] or 0.0),
        "avg_latency_ms": float(row[4] or 0.0),
        "avg_ttfb_ms": float(row[5] or 0.0),
        "context_window": int(row[6] or 0),
        "consecutive_failures": int(row[7] or 0),
        "backoff_level": int(row[8] or 0),
    }


def _load_eligible_candidates(db: Database, req: RoutingRequirements) -> list[dict[str, Any]]:
    now = utc_now_iso()
    with db.read_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, provider_id, provider_model_id, composite_score, avg_latency_ms, avg_ttfb_ms,
                   context_window, consecutive_failures, backoff_level
            FROM models
            WHERE is_active=1
              AND is_healthy=1
              AND (cooldown_until IS NULL OR cooldown_until < ?)
              AND (? = 0 OR supports_tools = 1)
              AND (? = 0 OR supports_vision = 1)
              AND (? = 0 OR supports_streaming = 1)
              AND (? = 0 OR supports_structured_output = 1)
              AND (? = 0 OR supports_system_messages = 1)
              AND context_window >= ?
              AND (max_output_tokens IS NULL OR max_output_tokens >= ?)
            ORDER BY composite_score DESC
            """,
            (
                now,
                1 if req.requires_tools else 0,
                1 if req.requires_vision else 0,
                1 if req.requires_streaming else 0,
                1 if req.requires_structured_output else 0,
                1 if req.requires_system_messages else 0,
                req.min_context_window,
                req.min_output_tokens,
            ),
        ).fetchall()
    return [_serialize_candidate(row) for row in rows]


def _match_explicit_model(
    candidates: list[dict[str, Any]], requested_model: str
) -> list[dict[str, Any]]:
    exact = [
        candidate
        for candidate in candidates
        if candidate["id"] == requested_model or candidate["provider_model_id"] == requested_model
    ]
    if exact:
        return exact
    if requested_model.endswith(":free"):
        base_model = requested_model.removesuffix(":free")
        return [
            candidate
            for candidate in candidates
            if candidate["id"] == base_model or candidate["provider_model_id"] == base_model
        ]
    return []


def _preference_score(
    candidate: dict[str, Any], preferences: RoutingPreferences | None
) -> tuple[float, float, float, float]:
    if preferences is None:
        return (candidate["composite_score"], 0.0, 0.0, 0.0)

    latency_ms = candidate["avg_ttfb_ms"] or candidate["avg_latency_ms"] or 999999.0
    context_window = float(candidate["context_window"])
    reliability = float(candidate["consecutive_failures"] + candidate["backoff_level"])
    bonus = 0.0

    if preferences.max_latency_ms is not None and latency_ms > preferences.max_latency_ms:
        bonus -= 25.0
    if (
        preferences.min_context_tokens is not None
        and context_window >= preferences.min_context_tokens
    ):
        bonus += 10.0

    if preferences.preference == "quality":
        bonus += candidate["composite_score"] * 0.20
    elif preferences.preference == "latency":
        bonus += max(0.0, 5000.0 - latency_ms) / 100.0
    elif preferences.preference == "context":
        bonus += context_window / 1000.0
    elif preferences.preference == "reliability":
        bonus -= reliability * 15.0
        bonus += max(0.0, 5000.0 - latency_ms) / 500.0
    else:
        bonus += max(0.0, 5000.0 - latency_ms) / 500.0
        bonus += context_window / 10000.0
        bonus -= reliability * 2.0

    return (
        candidate["composite_score"] + bonus,
        candidate["composite_score"],
        -latency_ms,
        context_window,
    )


def pick_candidates(
    db: Database,
    req: RoutingRequirements,
    *,
    preferences: RoutingPreferences | None = None,
    fallback_model_id: str | None = None,
    limit: int = 3,
) -> list[dict[str, str]]:
    effective_min_context = req.min_context_window
    if preferences and preferences.min_context_tokens is not None:
        effective_min_context = max(effective_min_context, preferences.min_context_tokens)
    effective_req = RoutingRequirements(
        requested_model=req.requested_model,
        requires_tools=req.requires_tools,
        requires_vision=req.requires_vision,
        requires_streaming=req.requires_streaming,
        requires_structured_output=req.requires_structured_output,
        requires_system_messages=req.requires_system_messages,
        min_context_window=effective_min_context,
        min_output_tokens=req.min_output_tokens,
    )

    candidates = _load_eligible_candidates(db, effective_req)
    if not candidates:
        raise NoHealthyModelsError("No healthy, capability-compatible models are available")

    requested_model = effective_req.requested_model or "auto"
    if requested_model != "auto":
        explicit = _match_explicit_model(candidates, requested_model)
        if explicit:
            ordered = explicit + [
                candidate
                for candidate in candidates
                if candidate["id"] not in {c["id"] for c in explicit}
            ]
        else:
            ordered = sorted(
                candidates,
                key=lambda candidate: _preference_score(candidate, preferences),
                reverse=True,
            )
    else:
        ordered = sorted(
            candidates,
            key=lambda candidate: _preference_score(candidate, preferences),
            reverse=True,
        )

    if fallback_model_id:
        fallback = next(
            (candidate for candidate in candidates if candidate["id"] == fallback_model_id), None
        )
        if fallback is not None and all(
            candidate["id"] != fallback_model_id for candidate in ordered[:limit]
        ):
            ordered = ordered[: max(limit - 1, 0)] + [fallback] + ordered[max(limit - 1, 0) :]

    limited = ordered[:limit]
    return [
        {
            "id": candidate["id"],
            "provider_id": candidate["provider_id"],
            "provider_model_id": candidate["provider_model_id"],
        }
        for candidate in limited
    ]
