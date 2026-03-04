from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config import Settings
from src.db import Database, utc_now_iso


def _iso_after_minutes(minutes: int) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(minutes=minutes))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _stale_before_iso(minutes: int) -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(minutes=minutes))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def get_provider_probe_usage(db: Database, provider_id: str, utc_day: str) -> int:
    with db.read_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM request_log
            WHERE provider_id = ?
              AND request_source IN ('probe', 'bootstrap')
              AND substr(timestamp, 1, 10) = ?
            """,
            (provider_id, utc_day),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def mark_failure(db: Database, model_id: str, error: str, settings: Settings | None = None) -> None:
    settings = settings or Settings()
    now = utc_now_iso()

    with db.read_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(backoff_level, 0) FROM models WHERE id=?",
            (model_id,),
        ).fetchone()

    if row is None:
        return

    current_backoff = int(row[0])
    next_backoff = min(current_backoff + 1, settings.health_max_backoff_exponent)
    cooldown_minutes = settings.health_cooldown_minutes * (2**next_backoff)
    cooldown_until = _iso_after_minutes(cooldown_minutes)

    db.writer.enqueue(
        """
        UPDATE models
        SET consecutive_failures=consecutive_failures+1,
            last_failure_at=?,
            last_error=?,
            backoff_level=?,
            cooldown_until=?,
            is_healthy=CASE
                WHEN consecutive_failures+1 >= ? THEN 0
                ELSE is_healthy
            END
        WHERE id=?
        """,
        (
            now,
            error[:500],
            next_backoff,
            cooldown_until,
            settings.health_consecutive_failures_threshold,
            model_id,
        ),
    )


def mark_success(
    db: Database,
    model_id: str,
    *,
    latency_ms: float | None = None,
    ttfb_ms: float | None = None,
    probed_at: str | None = None,
) -> None:
    now = utc_now_iso()
    db.writer.enqueue(
        """
        UPDATE models
        SET consecutive_failures=0,
            backoff_level=0,
            cooldown_until=NULL,
            is_healthy=1,
            last_error=NULL,
            last_success_at=?,
            last_routed_at=?,
            last_health_check=?,
            last_probe_at=COALESCE(?, last_probe_at),
            avg_latency_ms=COALESCE(?, avg_latency_ms),
            avg_ttfb_ms=COALESCE(?, avg_ttfb_ms)
        WHERE id=?
        """,
        (now, now, now, probed_at, latency_ms, ttfb_ms, model_id),
    )


def _provider_probe_enabled(settings: Settings, provider_id: str) -> bool:
    if provider_id == "openrouter":
        return settings.openrouter_active_probe_enabled
    return True


def _select_bootstrap_candidates(
    db: Database,
    *,
    fallback_model: str,
    limit: int,
) -> list[dict[str, Any]]:
    with db.read_conn() as conn:
        candidates: list[tuple] = []
        if fallback_model:
            row = conn.execute(
                """
                SELECT id, provider_id, provider_model_id
                FROM models
                WHERE id=? AND is_active=1
                """,
                (fallback_model,),
            ).fetchone()
            if row:
                candidates.append(row)

        rows = conn.execute(
            """
            SELECT id, provider_id, provider_model_id
            FROM models
            WHERE is_active=1
            ORDER BY composite_score DESC
            LIMIT ?
            """,
            (max(limit, 0),),
        ).fetchall()
        candidates.extend(rows)

    seen: set[str] = set()
    out = []
    for model_id, provider_id, provider_model_id in candidates:
        if model_id in seen:
            continue
        seen.add(model_id)
        out.append(
            {
                "id": model_id,
                "provider_id": provider_id,
                "provider_model_id": provider_model_id,
            }
        )
        if len(out) >= limit:
            break
    return out


def _select_probe_candidates(db: Database, settings: Settings) -> list[dict[str, Any]]:
    now_iso = utc_now_iso()
    stale_before = _stale_before_iso(settings.health_stale_after_minutes)

    with db.read_conn() as conn:
        cooldown_rows = conn.execute(
            """
            SELECT id, provider_id, provider_model_id
            FROM models
            WHERE is_active=1
              AND cooldown_until IS NOT NULL
              AND cooldown_until < ?
            ORDER BY cooldown_until ASC, composite_score DESC
            LIMIT ?
            """,
            (now_iso, settings.health_max_probes_per_run),
        ).fetchall()
        never_probed_rows = conn.execute(
            """
            SELECT id, provider_id, provider_model_id
            FROM models
            WHERE is_active=1
              AND last_probe_at IS NULL
            ORDER BY composite_score DESC
            LIMIT ?
            """,
            (settings.health_max_probes_per_run,),
        ).fetchall()
        stale_rows = conn.execute(
            """
            SELECT id, provider_id, provider_model_id
            FROM models
            WHERE is_active=1
              AND (last_success_at IS NULL OR last_success_at < ?)
              AND (last_probe_at IS NULL OR last_probe_at < ?)
            ORDER BY composite_score DESC
            LIMIT ?
            """,
            (stale_before, stale_before, settings.health_top_n_stale_probe),
        ).fetchall()

    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for bucket in (cooldown_rows, never_probed_rows, stale_rows):
        for model_id, provider_id, provider_model_id in bucket:
            if model_id in seen:
                continue
            seen.add(model_id)
            candidates.append(
                {
                    "id": model_id,
                    "provider_id": provider_id,
                    "provider_model_id": provider_model_id,
                }
            )
            if len(candidates) >= settings.health_max_probes_per_run:
                return candidates
    return candidates


async def _run_probe_batch(
    db: Database,
    registry,
    settings: Settings,
    *,
    request_source: str,
    candidates: list[dict[str, Any]],
) -> dict[str, int]:
    counts = {"considered": len(candidates), "probed": 0, "recovered": 0, "failed": 0, "skipped": 0}
    if not candidates:
        return counts

    utc_day = _utc_day()
    budget_usage: dict[str, int] = {}
    semaphore = asyncio.Semaphore(settings.health_probe_concurrency)

    async def probe_one(candidate: dict[str, Any]) -> None:
        provider_id = str(candidate["provider_id"])
        if not _provider_probe_enabled(settings, provider_id):
            counts["skipped"] += 1
            return

        budget_limit = settings.health_daily_request_budget_by_provider.get(provider_id, 0)
        budget_usage.setdefault(provider_id, get_provider_probe_usage(db, provider_id, utc_day))
        if budget_limit <= budget_usage[provider_id]:
            counts["skipped"] += 1
            return

        provider = registry.get(provider_id)
        if not hasattr(provider, "probe"):
            counts["skipped"] += 1
            return

        async with semaphore:
            counts["probed"] += 1
            request_id = f"{request_source}-{candidate['id']}-{counts['probed']}"
            try:
                result = await provider.probe(
                    candidate["provider_model_id"],
                    max_tokens=settings.health_probe_max_tokens,
                    timeout_seconds=settings.health_probe_timeout_seconds,
                )
                probed_at = utc_now_iso()
                budget_usage[provider_id] += 1
                mark_success(
                    db,
                    str(candidate["id"]),
                    latency_ms=result.latency_ms,
                    ttfb_ms=result.ttfb_ms,
                    probed_at=probed_at,
                )
                db.writer.enqueue(
                    """
                    INSERT INTO request_log(
                        request_id, timestamp, request_source, selected_model_id, provider_id,
                        latency_ms, ttfb_ms, success
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        request_id,
                        probed_at,
                        request_source,
                        candidate["id"],
                        provider_id,
                        result.latency_ms,
                        result.ttfb_ms,
                    ),
                )
                counts["recovered"] += 1
            except Exception as exc:
                budget_usage[provider_id] += 1
                mark_failure(db, str(candidate["id"]), str(exc), settings=settings)
                db.writer.enqueue(
                    """
                    INSERT INTO request_log(
                        request_id, timestamp, request_source, selected_model_id, provider_id,
                        success, gateway_error_category, error_message
                    ) VALUES (?, ?, ?, ?, ?, 0, 'provider_error', ?)
                    """,
                    (
                        request_id,
                        utc_now_iso(),
                        request_source,
                        candidate["id"],
                        provider_id,
                        str(exc)[:500],
                    ),
                )
                counts["failed"] += 1

    await asyncio.gather(*(probe_one(candidate) for candidate in candidates))
    return counts


async def run_health_checks(db: Database, registry, settings: Settings) -> dict[str, int]:
    candidates = _select_probe_candidates(db, settings)
    return await _run_probe_batch(
        db, registry, settings, request_source="probe", candidates=candidates
    )


async def bootstrap_health_check(db: Database, registry, settings: Settings) -> dict[str, int]:
    candidates = _select_bootstrap_candidates(
        db,
        fallback_model=settings.ranking_fallback_model,
        limit=settings.health_startup_probe_limit,
    )
    return await _run_probe_batch(
        db, registry, settings, request_source="bootstrap", candidates=candidates
    )
