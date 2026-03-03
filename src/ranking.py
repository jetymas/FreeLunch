from __future__ import annotations

from dataclasses import dataclass

from src.db import Database, utc_now_iso


@dataclass(slots=True)
class TelemetrySnapshot:
    total_requests: int = 0
    success_rate: float = 0.0
    avg_latency_ms: float = 0.0


def _load_telemetry_by_model(db: Database) -> dict[str, TelemetrySnapshot]:
    with db.read_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                selected_model_id,
                COUNT(*) AS total_requests,
                AVG(CASE WHEN success = 1 THEN 1.0 ELSE 0.0 END) AS success_rate,
                AVG(COALESCE(latency_ms, 0)) AS avg_latency_ms
            FROM request_log
            WHERE request_source = 'client'
            GROUP BY selected_model_id
            """
        ).fetchall()

    telemetry: dict[str, TelemetrySnapshot] = {}
    for model_id, total_requests, success_rate, avg_latency_ms in rows:
        telemetry[model_id] = TelemetrySnapshot(
            total_requests=int(total_requests or 0),
            success_rate=float(success_rate or 0.0),
            avg_latency_ms=float(avg_latency_ms or 0.0),
        )
    return telemetry


def recompute_ranking(db: Database) -> int:
    telemetry_by_model = _load_telemetry_by_model(db)

    with db.read_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, is_healthy, COALESCE(chatbot_arena_elo, 0), COALESCE(open_llm_score, 0),
                   COALESCE(avg_latency_ms, 0), COALESCE(consecutive_failures, 0)
            FROM models
            WHERE is_active=1
            """
        ).fetchall()

    updates = 0
    now = utc_now_iso()
    for model_id, healthy, elo, llm, latency, failures in rows:
        telemetry = telemetry_by_model.get(model_id, TelemetrySnapshot())
        observed_latency_ms = telemetry.avg_latency_ms if telemetry.total_requests > 0 else float(latency)

        benchmark_score = (elo * 0.05) + (llm * 10.0)
        health_score = (15 if healthy else -100) - (failures * 5)
        telemetry_score = (
            (telemetry.success_rate * 35.0)
            - (observed_latency_ms / 150.0)
            + min(telemetry.total_requests, 50) * 0.1
        )

        score = benchmark_score + health_score + telemetry_score
        db.writer.enqueue(
            "UPDATE models SET composite_score=?, score_updated_at=? WHERE id=?",
            (float(score), now, model_id),
        )
        updates += 1
    return updates
