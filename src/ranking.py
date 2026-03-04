from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.config import Settings
from src.db import Database, utc_now_iso


@dataclass(slots=True)
class TelemetrySnapshot:
    total_requests: int = 0
    success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    avg_ttfb_ms: float = 0.0
    rate_limit_ratio: float = 0.0


def _window_start_iso(days: int = 7) -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(days=days))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _load_telemetry_by_model(db: Database) -> dict[str, TelemetrySnapshot]:
    with db.read_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                selected_model_id,
                COUNT(*) AS total_requests,
                AVG(CASE WHEN success = 1 THEN 1.0 ELSE 0.0 END) AS success_rate,
                AVG(CASE WHEN latency_ms IS NOT NULL THEN latency_ms END) AS avg_latency_ms,
                AVG(CASE WHEN ttfb_ms IS NOT NULL THEN ttfb_ms END) AS avg_ttfb_ms,
                AVG(
                    CASE
                        WHEN error_code = '429' OR error_message LIKE '%429%' THEN 1.0
                        ELSE 0.0
                    END
                ) AS rate_limit_ratio
            FROM request_log
            WHERE request_source = 'client' AND timestamp >= ?
            GROUP BY selected_model_id
            """,
            (_window_start_iso(),),
        ).fetchall()

    telemetry: dict[str, TelemetrySnapshot] = {}
    for (
        model_id,
        total_requests,
        success_rate,
        avg_latency_ms,
        avg_ttfb_ms,
        rate_limit_ratio,
    ) in rows:
        telemetry[model_id] = TelemetrySnapshot(
            total_requests=int(total_requests or 0),
            success_rate=float(success_rate or 0.0),
            avg_latency_ms=float(avg_latency_ms or 0.0),
            avg_ttfb_ms=float(avg_ttfb_ms or 0.0),
            rate_limit_ratio=float(rate_limit_ratio or 0.0),
        )
    return telemetry


def _normalize(value: float, min_value: float, max_value: float) -> float:
    if max_value <= min_value:
        return 1.0
    return max(0.0, min(1.0, (value - min_value) / (max_value - min_value)))


def _normalize_inverse(value: float, min_value: float, max_value: float) -> float:
    return 1.0 - _normalize(value, min_value, max_value)


def _cold_start_usage_score(rank: int | None, max_rank: int) -> float:
    if rank is None:
        return 0.5
    rank_floor = max(max_rank, 1)
    return _normalize_inverse(float(rank), 1.0, float(rank_floor))


def recompute_ranking(db: Database, settings: Settings | None = None) -> int:
    settings = settings or Settings()
    telemetry_by_model = _load_telemetry_by_model(db)

    with db.read_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, is_healthy, COALESCE(chatbot_arena_elo, 0), COALESCE(open_llm_score, 0),
                   COALESCE(avg_latency_ms, 0), COALESCE(avg_ttfb_ms, 0), COALESCE(consecutive_failures, 0),
                   COALESCE(backoff_level, 0), cooldown_until, context_window, supports_tools,
                   supports_streaming, supports_vision, supports_structured_output, openrouter_rank,
                   chatbot_arena_elo IS NOT NULL, open_llm_score IS NOT NULL
            FROM models
            WHERE is_active=1
            """
        ).fetchall()

    if not rows:
        return 0

    elo_values = [float(row[2]) for row in rows if row[15]]
    llm_values = [float(row[3]) for row in rows if row[16]]
    context_values = [int(row[9] or 0) for row in rows]
    latency_candidates = []
    usage_ranks = [int(row[14]) for row in rows if row[14] is not None]
    now_iso = utc_now_iso()

    for row in rows:
        model_id = row[0]
        telemetry = telemetry_by_model.get(model_id, TelemetrySnapshot())
        latency_reference = (
            telemetry.avg_ttfb_ms or telemetry.avg_latency_ms or float(row[5] or row[4] or 0.0)
        )
        if latency_reference > 0:
            latency_candidates.append(float(latency_reference))

    min_elo = min(elo_values) if elo_values else 0.0
    max_elo = max(elo_values) if elo_values else 0.0
    min_llm = min(llm_values) if llm_values else 0.0
    max_llm = max(llm_values) if llm_values else 0.0
    min_context = min(context_values) if context_values else 0
    max_context = max(context_values) if context_values else 0
    min_latency = min(latency_candidates) if latency_candidates else 0.0
    max_latency = max(latency_candidates) if latency_candidates else 0.0
    max_rank = max(usage_ranks) if usage_ranks else 1

    updates = 0
    score_updated_at = utc_now_iso()
    weights = dict(settings.ranking_weights or Settings.DEFAULT_RANKING_WEIGHTS)

    for row in rows:
        model_id = row[0]
        healthy = bool(row[1])
        elo = float(row[2] or 0.0)
        llm = float(row[3] or 0.0)
        base_latency_ms = float(row[4] or 0.0)
        base_ttfb_ms = float(row[5] or 0.0)
        failures = int(row[6] or 0)
        backoff_level = int(row[7] or 0)
        cooldown_until = row[8]
        context_window = int(row[9] or 0)
        feature_count = int(row[10]) + int(row[11]) + int(row[12]) + int(row[13])
        openrouter_rank = int(row[14]) if row[14] is not None else None
        has_elo = bool(row[15])
        has_llm = bool(row[16])
        telemetry = telemetry_by_model.get(model_id, TelemetrySnapshot())

        benchmark_parts = []
        if has_elo:
            benchmark_parts.append(_normalize(elo, min_elo, max_elo))
        if has_llm:
            benchmark_parts.append(_normalize(llm, min_llm, max_llm))
        benchmark_score = sum(benchmark_parts) / len(benchmark_parts) if benchmark_parts else None

        if telemetry.total_requests >= 5:
            usage_score = max(
                0.0,
                min(
                    1.0,
                    (telemetry.success_rate * 0.6)
                    + (min(telemetry.total_requests, 50) / 50.0 * 0.25)
                    + ((1.0 - telemetry.rate_limit_ratio) * 0.15),
                ),
            )
        else:
            usage_score = _cold_start_usage_score(openrouter_rank, max_rank)

        latency_reference = (
            telemetry.avg_ttfb_ms or telemetry.avg_latency_ms or base_ttfb_ms or base_latency_ms
        )
        latency_score = None
        if latency_reference > 0:
            latency_score = _normalize_inverse(float(latency_reference), min_latency, max_latency)

        availability_penalty = 0.0
        if not healthy:
            availability_penalty += 0.45
        if cooldown_until and cooldown_until > now_iso:
            availability_penalty += 0.30
        availability_penalty += min(failures, settings.health_consecutive_failures_threshold) * 0.08
        availability_penalty += min(backoff_level, settings.health_max_backoff_exponent) * 0.05
        success_bonus = telemetry.success_rate * 0.20 if telemetry.total_requests > 0 else 0.10
        availability_score = max(0.0, min(1.0, 1.0 - availability_penalty + success_bonus))

        context_score = _normalize(float(context_window), float(min_context), float(max_context))
        feature_score = feature_count / 4.0

        factor_values: dict[str, float | None] = {
            "benchmark_score": benchmark_score,
            "real_world_usage": usage_score,
            "latency": latency_score,
            "availability": availability_score,
            "context_window": context_score,
            "feature_support": feature_score,
        }

        available_weight = sum(
            weights.get(name, 0.0) for name, value in factor_values.items() if value is not None
        )
        if available_weight <= 0:
            final_score = 0.0
        else:
            final_score = 0.0
            for name, value in factor_values.items():
                if value is None:
                    continue
                normalized_weight = weights.get(name, 0.0) / available_weight
                final_score += normalized_weight * float(value)

        db.writer.enqueue(
            "UPDATE models SET composite_score=?, score_updated_at=? WHERE id=?",
            (round(final_score * 100.0, 4), score_updated_at, model_id),
        )
        updates += 1

    return updates
