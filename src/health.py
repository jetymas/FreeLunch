from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from src.config import Settings
from src.db import Database, utc_now_iso
from src.runtime_logging import get_logger, runtime_log

ROLLING_METRIC_ALPHA = 0.3
TOKEN_REVIEW_WINDOW_DAYS = 7
TOKEN_CONTEXT_FAILURE_MIN_COUNT = 5
TOKEN_CONTEXT_FAILURE_RATE_THRESHOLD = 0.10
TOKEN_MISMATCH_MIN_SAMPLES = 20
TOKEN_MISMATCH_MEDIAN_RATIO_THRESHOLD = 1.25
TOKEN_FAILOVER_RECOVERY_MIN_COUNT = 3

logger = get_logger(__name__)


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


def _review_since_iso(review_days: int) -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(days=max(review_days, 1)))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _request_log_columns(db: Database) -> set[str]:
    with db.read_conn() as conn:
        rows = conn.execute("PRAGMA table_info(request_log)").fetchall()
    return {str(row["name"]) for row in rows}


def _tokenizer_family_label(value: Any) -> str:
    normalized = str(value or "").strip()
    return normalized if normalized else "unknown"


def _provider_model_label(provider_model_id: Any, selected_model_id: Any) -> str:
    normalized = str(provider_model_id or "").strip()
    if normalized:
        return normalized
    fallback = str(selected_model_id or "").strip()
    return fallback if fallback else "unknown"


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


def get_probe_budget_summary(db: Database, settings: Settings) -> list[dict[str, int | str]]:
    utc_day = _utc_day()
    summary: list[dict[str, int | str]] = []
    for provider_id, limit in sorted(settings.health_daily_request_budget_by_provider.items()):
        used = get_provider_probe_usage(db, provider_id, utc_day)
        summary.append(
            {
                "provider_id": provider_id,
                "utc_day": utc_day,
                "limit": int(limit),
                "used": used,
                "remaining": max(int(limit) - used, 0),
            }
        )
    return summary


def get_recent_probe_activity(
    db: Database,
    *,
    lookback_hours: int = 24,
) -> dict[str, Any]:
    since = (
        (datetime.now(timezone.utc) - timedelta(hours=max(lookback_hours, 1)))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    with db.read_conn() as conn:
        totals_row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_requests,
                SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS successes,
                SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failures,
                MAX(timestamp) AS last_probe_at
            FROM request_log
            WHERE request_source IN ('probe', 'bootstrap')
              AND timestamp >= ?
            """,
            (since,),
        ).fetchone()
        source_rows = conn.execute(
            """
            SELECT
                request_source,
                COUNT(*) AS total_requests,
                SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS successes,
                SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failures
            FROM request_log
            WHERE request_source IN ('probe', 'bootstrap')
              AND timestamp >= ?
            GROUP BY request_source
            ORDER BY request_source
            """,
            (since,),
        ).fetchall()
        provider_rows = conn.execute(
            """
            SELECT
                provider_id,
                COUNT(*) AS total_requests,
                SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS successes,
                SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failures,
                MAX(timestamp) AS last_probe_at
            FROM request_log
            WHERE request_source IN ('probe', 'bootstrap')
              AND timestamp >= ?
            GROUP BY provider_id
            ORDER BY provider_id
            """,
            (since,),
        ).fetchall()

    return {
        "lookback_hours": max(lookback_hours, 1),
        "since": since,
        "total_requests": int(totals_row[0] or 0) if totals_row else 0,
        "successes": int(totals_row[1] or 0) if totals_row else 0,
        "failures": int(totals_row[2] or 0) if totals_row else 0,
        "last_probe_at": totals_row[3] if totals_row else None,
        "by_source": [
            {
                "request_source": row[0],
                "total_requests": int(row[1] or 0),
                "successes": int(row[2] or 0),
                "failures": int(row[3] or 0),
            }
            for row in source_rows
        ],
        "by_provider": [
            {
                "provider_id": row[0],
                "total_requests": int(row[1] or 0),
                "successes": int(row[2] or 0),
                "failures": int(row[3] or 0),
                "last_probe_at": row[4],
            }
            for row in provider_rows
        ],
    }


def get_token_estimation_review_summary(
    db: Database,
    *,
    review_days: int = TOKEN_REVIEW_WINDOW_DAYS,
) -> dict[str, Any]:
    since = _review_since_iso(review_days)
    request_log_columns = _request_log_columns(db)
    tokenizer_expr = (
        "COALESCE(NULLIF(TRIM(rl.selected_tokenizer_family), ''), "
        "NULLIF(TRIM(m.tokenizer_family), ''), 'unknown')"
        if "selected_tokenizer_family" in request_log_columns
        else "COALESCE(NULLIF(TRIM(m.tokenizer_family), ''), 'unknown')"
    )
    provider_model_expr = (
        "COALESCE(NULLIF(TRIM(rl.selected_provider_model_id), ''), "
        "NULLIF(TRIM(m.provider_model_id), ''), rl.selected_model_id)"
        if "selected_provider_model_id" in request_log_columns
        else "COALESCE(NULLIF(TRIM(m.provider_model_id), ''), rl.selected_model_id)"
    )
    context_window_expr = (
        "COALESCE(rl.selected_context_window, m.context_window, 0)"
        if "selected_context_window" in request_log_columns
        else "COALESCE(m.context_window, 0)"
    )

    with db.read_conn() as conn:
        family_rows = conn.execute(
            f"""
            SELECT
                {tokenizer_expr} AS tokenizer_family,
                COUNT(*) AS total_requests,
                SUM(CASE
                    WHEN rl.gateway_error_category='CONTEXT_EXCEEDED' THEN 1
                    ELSE 0
                END) AS context_exceeded_failures
            FROM request_log rl
            LEFT JOIN models m ON m.id = rl.selected_model_id
            WHERE rl.request_source='client'
              AND rl.timestamp >= ?
            GROUP BY {tokenizer_expr}
            ORDER BY context_exceeded_failures DESC, total_requests DESC, tokenizer_family
            """,
            (since,),
        ).fetchall()
        model_rows = conn.execute(
            f"""
            SELECT
                {provider_model_expr} AS provider_model_id,
                {tokenizer_expr} AS tokenizer_family,
                COUNT(*) AS total_requests,
                SUM(CASE
                    WHEN rl.gateway_error_category='CONTEXT_EXCEEDED' THEN 1
                    ELSE 0
                END) AS context_exceeded_failures
            FROM request_log rl
            LEFT JOIN models m ON m.id = rl.selected_model_id
            WHERE rl.request_source='client'
              AND rl.timestamp >= ?
            GROUP BY {provider_model_expr}, {tokenizer_expr}
            ORDER BY context_exceeded_failures DESC, total_requests DESC, provider_model_id
            """,
            (since,),
        ).fetchall()
        failover_rows = conn.execute(
            f"""
            SELECT
                rl.id,
                rl.request_id,
                rl.attempt_index,
                rl.success,
                rl.gateway_error_category,
                rl.selected_model_id,
                {provider_model_expr} AS provider_model_id,
                {tokenizer_expr} AS tokenizer_family,
                {context_window_expr} AS context_window
            FROM request_log rl
            LEFT JOIN models m ON m.id = rl.selected_model_id
            WHERE rl.request_source='client'
              AND rl.timestamp >= ?
              AND rl.request_id IS NOT NULL
            ORDER BY rl.request_id, rl.attempt_index ASC, rl.id ASC
            """,
            (since,),
        ).fetchall()

        mismatch_rows: list[Any] = []
        if "estimated_prompt_tokens" in request_log_columns:
            mismatch_rows = conn.execute(
                f"""
                SELECT
                    {tokenizer_expr} AS tokenizer_family,
                    rl.prompt_tokens,
                    rl.estimated_prompt_tokens
                FROM request_log rl
                LEFT JOIN models m ON m.id = rl.selected_model_id
                WHERE rl.request_source='client'
                  AND rl.timestamp >= ?
                  AND rl.prompt_tokens IS NOT NULL
                  AND rl.estimated_prompt_tokens IS NOT NULL
                  AND rl.estimated_prompt_tokens > 0
                ORDER BY tokenizer_family
                """,
                (since,),
            ).fetchall()

    review_flags_by_family: dict[str, set[str]] = defaultdict(set)
    review_flags_by_model: dict[str, set[str]] = defaultdict(set)

    context_by_family: list[dict[str, Any]] = []
    for row in family_rows:
        tokenizer_family = _tokenizer_family_label(row["tokenizer_family"])
        total_requests = int(row["total_requests"] or 0)
        context_exceeded_failures = int(row["context_exceeded_failures"] or 0)
        failure_rate = (
            float(context_exceeded_failures) / float(total_requests)
            if total_requests > 0
            else 0.0
        )
        flagged = (
            context_exceeded_failures >= TOKEN_CONTEXT_FAILURE_MIN_COUNT
            and failure_rate >= TOKEN_CONTEXT_FAILURE_RATE_THRESHOLD
        )
        if flagged:
            review_flags_by_family[tokenizer_family].add("context_exceeded_rate")
        context_by_family.append(
            {
                "tokenizer_family": tokenizer_family,
                "total_requests": total_requests,
                "context_exceeded_failures": context_exceeded_failures,
                "failure_rate": failure_rate,
                "flagged_for_review": flagged,
            }
        )

    context_by_model: list[dict[str, Any]] = []
    for row in model_rows:
        provider_model_id = _provider_model_label(row["provider_model_id"], None)
        tokenizer_family = _tokenizer_family_label(row["tokenizer_family"])
        total_requests = int(row["total_requests"] or 0)
        context_exceeded_failures = int(row["context_exceeded_failures"] or 0)
        failure_rate = (
            float(context_exceeded_failures) / float(total_requests)
            if total_requests > 0
            else 0.0
        )
        flagged = (
            context_exceeded_failures >= TOKEN_CONTEXT_FAILURE_MIN_COUNT
            and failure_rate >= TOKEN_CONTEXT_FAILURE_RATE_THRESHOLD
        )
        if flagged:
            review_flags_by_model[provider_model_id].add("context_exceeded_rate")
        context_by_model.append(
            {
                "provider_model_id": provider_model_id,
                "tokenizer_family": tokenizer_family,
                "total_requests": total_requests,
                "context_exceeded_failures": context_exceeded_failures,
                "failure_rate": failure_rate,
                "flagged_for_review": flagged,
            }
        )

    failover_by_family_counts: dict[str, int] = defaultdict(int)
    failover_by_model_counts: dict[tuple[str, str], int] = defaultdict(int)
    recovered_request_ids = 0
    current_request_id: str | None = None
    request_rows: list[Any] = []

    def _flush_request(rows: list[Any]) -> None:
        nonlocal recovered_request_ids
        if not rows:
            return
        success_rows = [row for row in rows if int(row["success"] or 0) == 1]
        failed_rows = [
            row
            for row in rows
            if int(row["success"] or 0) == 0
            and str(row["gateway_error_category"] or "") == "CONTEXT_EXCEEDED"
        ]
        recovered_families: set[str] = set()
        recovered_models: set[tuple[str, str]] = set()
        for failed in failed_rows:
            failed_attempt_index = int(failed["attempt_index"] or 0)
            failed_context_window = int(failed["context_window"] or 0)
            if any(
                int(success["attempt_index"] or 0) > failed_attempt_index
                and int(success["context_window"] or 0) > failed_context_window
                for success in success_rows
            ):
                tokenizer_family = _tokenizer_family_label(failed["tokenizer_family"])
                provider_model_id = _provider_model_label(
                    failed["provider_model_id"],
                    failed["selected_model_id"],
                )
                recovered_families.add(tokenizer_family)
                recovered_models.add((provider_model_id, tokenizer_family))
        if recovered_families or recovered_models:
            recovered_request_ids += 1
        for tokenizer_family in recovered_families:
            failover_by_family_counts[tokenizer_family] += 1
        for provider_model_id, tokenizer_family in recovered_models:
            failover_by_model_counts[(provider_model_id, tokenizer_family)] += 1

    for row in failover_rows:
        request_id = str(row["request_id"])
        if current_request_id is None:
            current_request_id = request_id
        if request_id != current_request_id:
            _flush_request(request_rows)
            request_rows = []
            current_request_id = request_id
        request_rows.append(row)
    _flush_request(request_rows)

    failover_by_family: list[dict[str, Any]] = []
    for tokenizer_family, recovered_requests in sorted(
        failover_by_family_counts.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        flagged = recovered_requests >= TOKEN_FAILOVER_RECOVERY_MIN_COUNT
        if flagged:
            review_flags_by_family[tokenizer_family].add("context_failover_recoveries")
        failover_by_family.append(
            {
                "tokenizer_family": tokenizer_family,
                "recovered_requests": recovered_requests,
                "flagged_for_review": flagged,
            }
        )

    failover_by_model: list[dict[str, Any]] = []
    for (provider_model_id, tokenizer_family), recovered_requests in sorted(
        failover_by_model_counts.items(),
        key=lambda item: (-item[1], item[0][0], item[0][1]),
    ):
        flagged = recovered_requests >= TOKEN_FAILOVER_RECOVERY_MIN_COUNT
        if flagged:
            review_flags_by_model[provider_model_id].add("context_failover_recoveries")
        failover_by_model.append(
            {
                "provider_model_id": provider_model_id,
                "tokenizer_family": tokenizer_family,
                "recovered_requests": recovered_requests,
                "flagged_for_review": flagged,
            }
        )

    mismatch_summary: dict[str, Any] = {
        "available": "estimated_prompt_tokens" in request_log_columns,
        "sample_threshold": TOKEN_MISMATCH_MIN_SAMPLES,
        "median_ratio_threshold": TOKEN_MISMATCH_MEDIAN_RATIO_THRESHOLD,
        "entries": [],
    }
    if "estimated_prompt_tokens" not in request_log_columns:
        mismatch_summary["reason"] = "estimated_prompt_tokens column unavailable"
    else:
        ratios_by_family: dict[str, list[float]] = defaultdict(list)
        for row in mismatch_rows:
            estimated_prompt_tokens = int(row["estimated_prompt_tokens"] or 0)
            prompt_tokens = int(row["prompt_tokens"] or 0)
            if estimated_prompt_tokens <= 0:
                continue
            tokenizer_family = _tokenizer_family_label(row["tokenizer_family"])
            ratios_by_family[tokenizer_family].append(
                float(prompt_tokens) / float(estimated_prompt_tokens)
            )

        entries: list[dict[str, Any]] = []
        for tokenizer_family, ratios in sorted(
            ratios_by_family.items(),
            key=lambda item: (-len(item[1]), item[0]),
        ):
            median_ratio = float(median(ratios)) if ratios else 0.0
            sample_count = len(ratios)
            flagged = (
                sample_count >= TOKEN_MISMATCH_MIN_SAMPLES
                and median_ratio >= TOKEN_MISMATCH_MEDIAN_RATIO_THRESHOLD
            )
            if flagged:
                review_flags_by_family[tokenizer_family].add("prompt_token_median_ratio")
            entries.append(
                {
                    "tokenizer_family": tokenizer_family,
                    "sample_count": sample_count,
                    "median_ratio": median_ratio,
                    "flagged_for_review": flagged,
                }
            )
        mismatch_summary["entries"] = entries

    return {
        "review_window_days": max(review_days, 1),
        "since": since,
        "thresholds": {
            "context_failure_min_count": TOKEN_CONTEXT_FAILURE_MIN_COUNT,
            "context_failure_rate_threshold": TOKEN_CONTEXT_FAILURE_RATE_THRESHOLD,
            "mismatch_min_samples": TOKEN_MISMATCH_MIN_SAMPLES,
            "mismatch_median_ratio_threshold": TOKEN_MISMATCH_MEDIAN_RATIO_THRESHOLD,
            "failover_recovery_min_count": TOKEN_FAILOVER_RECOVERY_MIN_COUNT,
        },
        "context_exceeded_by_tokenizer_family": context_by_family,
        "context_exceeded_by_provider_model": context_by_model,
        "context_failover_recoveries": {
            "total_requests": recovered_request_ids,
            "by_tokenizer_family": failover_by_family,
            "by_provider_model": failover_by_model,
        },
        "estimation_mismatch_by_tokenizer_family": mismatch_summary,
        "review_flags": {
            "tokenizer_families": [
                {
                    "tokenizer_family": tokenizer_family,
                    "reasons": sorted(reasons),
                }
                for tokenizer_family, reasons in sorted(review_flags_by_family.items())
            ],
            "provider_models": [
                {
                    "provider_model_id": provider_model_id,
                    "reasons": sorted(reasons),
                }
                for provider_model_id, reasons in sorted(review_flags_by_model.items())
            ],
        },
    }


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
            avg_latency_ms=CASE
                WHEN ? IS NULL THEN avg_latency_ms
                WHEN avg_latency_ms IS NULL THEN ?
                ELSE ((avg_latency_ms * ?) + (? * ?))
            END,
            avg_ttfb_ms=CASE
                WHEN ? IS NULL THEN avg_ttfb_ms
                WHEN avg_ttfb_ms IS NULL THEN ?
                ELSE ((avg_ttfb_ms * ?) + (? * ?))
            END
        WHERE id=?
        """,
        (
            now,
            now,
            now,
            probed_at,
            latency_ms,
            latency_ms,
            1.0 - ROLLING_METRIC_ALPHA,
            latency_ms,
            ROLLING_METRIC_ALPHA,
            ttfb_ms,
            ttfb_ms,
            1.0 - ROLLING_METRIC_ALPHA,
            ttfb_ms,
            ROLLING_METRIC_ALPHA,
            model_id,
        ),
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
    for bucket_name, bucket in (
        ("cooldown_recovery", cooldown_rows),
        ("never_probed", never_probed_rows),
        ("stale", stale_rows),
    ):
        for model_id, provider_id, provider_model_id in bucket:
            if model_id in seen:
                continue
            seen.add(model_id)
            candidates.append(
                {
                    "id": model_id,
                    "provider_id": provider_id,
                    "provider_model_id": provider_model_id,
                    "probe_reason": bucket_name,
                }
            )
            if len(candidates) >= settings.health_max_probes_per_run:
                return candidates
    return candidates


def get_probe_runtime_summary(db: Database, settings: Settings) -> dict[str, Any]:
    now_iso = utc_now_iso()
    stale_before = _stale_before_iso(settings.health_stale_after_minutes)

    with db.read_conn() as conn:
        bucket_row = conn.execute(
            """
            SELECT
                SUM(CASE
                    WHEN is_active=1
                     AND cooldown_until IS NOT NULL
                     AND cooldown_until < ?
                    THEN 1 ELSE 0 END
                ) AS cooldown_recovery,
                SUM(CASE
                    WHEN is_active=1
                     AND last_probe_at IS NULL
                    THEN 1 ELSE 0 END
                ) AS never_probed,
                SUM(CASE
                    WHEN is_active=1
                     AND (last_success_at IS NULL OR last_success_at < ?)
                     AND (last_probe_at IS NULL OR last_probe_at < ?)
                    THEN 1 ELSE 0 END
                ) AS stale,
                SUM(CASE
                    WHEN is_active=1
                     AND cooldown_until IS NOT NULL
                     AND cooldown_until >= ?
                    THEN 1 ELSE 0 END
                ) AS active_cooldowns
            FROM models
            """
            ,
            (now_iso, stale_before, stale_before, now_iso),
        ).fetchone()
        provider_rows = conn.execute(
            """
            SELECT provider_id, COUNT(*) AS active_models
            FROM models
            WHERE is_active=1
            GROUP BY provider_id
            ORDER BY provider_id
            """
        ).fetchall()

    disabled_provider_ids = [
        str(row[0])
        for row in provider_rows
        if row[0] and not _provider_probe_enabled(settings, str(row[0]))
    ]
    budget_exhausted_provider_ids = [
        str(item["provider_id"])
        for item in get_probe_budget_summary(db, settings)
        if int(item["remaining"]) <= 0
    ]
    next_candidates = _select_probe_candidates(db, settings)

    return {
        "policy": {
            "max_probes_per_run": settings.health_max_probes_per_run,
            "probe_concurrency": settings.health_probe_concurrency,
            "stale_after_minutes": settings.health_stale_after_minutes,
            "startup_probe_limit": settings.health_startup_probe_limit,
        },
        "buckets": {
            "cooldown_recovery": int(bucket_row[0] or 0) if bucket_row else 0,
            "never_probed": int(bucket_row[1] or 0) if bucket_row else 0,
            "stale": int(bucket_row[2] or 0) if bucket_row else 0,
            "active_cooldowns": int(bucket_row[3] or 0) if bucket_row else 0,
        },
        "disabled_provider_ids": disabled_provider_ids,
        "budget_exhausted_provider_ids": budget_exhausted_provider_ids,
        "next_candidates": [
            {
                "model_id": str(candidate["id"]),
                "provider_id": str(candidate["provider_id"]),
                "provider_model_id": str(candidate["provider_model_id"]),
                "reason": str(candidate["probe_reason"]),
            }
            for candidate in next_candidates
        ],
    }


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

    runtime_log(
        logger,
        "health.probe_batch.started",
        verbosity="verbose",
        message="Starting health probe batch",
        request_source=request_source,
        candidate_count=len(candidates),
    )

    utc_day = _utc_day()
    budget_usage: dict[str, int] = {}
    semaphore = asyncio.Semaphore(settings.health_probe_concurrency)

    async def probe_one(candidate: dict[str, Any]) -> None:
        provider_id = str(candidate["provider_id"])
        if not _provider_probe_enabled(settings, provider_id):
            counts["skipped"] += 1
            runtime_log(
                logger,
                "health.probe.skipped",
                verbosity="debug",
                message="Skipped probe because provider probing is disabled",
                request_source=request_source,
                provider_id=provider_id,
                model_id=str(candidate["id"]),
                reason="provider_disabled",
            )
            return

        budget_limit = settings.health_daily_request_budget_by_provider.get(provider_id, 0)
        budget_usage.setdefault(provider_id, get_provider_probe_usage(db, provider_id, utc_day))
        if budget_limit <= budget_usage[provider_id]:
            counts["skipped"] += 1
            runtime_log(
                logger,
                "health.probe.skipped",
                verbosity="debug",
                message="Skipped probe because daily budget is exhausted",
                request_source=request_source,
                provider_id=provider_id,
                model_id=str(candidate["id"]),
                reason="budget_exhausted",
            )
            return

        try:
            provider = registry.get(provider_id)
        except KeyError:
            db.set_model_active(str(candidate["id"]), is_active=False)
            counts["skipped"] += 1
            runtime_log(
                logger,
                "health.probe.skipped",
                verbosity="verbose",
                message="Skipped probe because provider is no longer routable",
                request_source=request_source,
                provider_id=provider_id,
                model_id=str(candidate["id"]),
                reason="provider_unavailable",
            )
            return
        if not hasattr(provider, "probe"):
            counts["skipped"] += 1
            runtime_log(
                logger,
                "health.probe.skipped",
                verbosity="debug",
                message="Skipped probe because provider has no probe method",
                request_source=request_source,
                provider_id=provider_id,
                model_id=str(candidate["id"]),
                reason="probe_unsupported",
            )
            return

        async with semaphore:
            counts["probed"] += 1
            request_id = f"{request_source}-{candidate['id']}-{counts['probed']}"
            runtime_log(
                logger,
                "health.probe.started",
                verbosity="debug",
                message="Running model probe",
                request_source=request_source,
                provider_id=provider_id,
                model_id=str(candidate["id"]),
                provider_model_id=str(candidate["provider_model_id"]),
                request_id=request_id,
            )
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
                runtime_log(
                    logger,
                    "health.probe.succeeded",
                    verbosity="verbose",
                    message="Model probe succeeded",
                    request_source=request_source,
                    provider_id=provider_id,
                    model_id=str(candidate["id"]),
                    request_id=request_id,
                    latency_ms=result.latency_ms,
                    ttfb_ms=result.ttfb_ms,
                )
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
                runtime_log(
                    logger,
                    "health.probe.failed",
                    verbosity="concise",
                    level=30,
                    message="Model probe failed",
                    request_source=request_source,
                    provider_id=provider_id,
                    model_id=str(candidate["id"]),
                    request_id=request_id,
                    error=str(exc)[:500],
                )

    await asyncio.gather(*(probe_one(candidate) for candidate in candidates))
    runtime_log(
        logger,
        "health.probe_batch.completed",
        verbosity="verbose",
        message="Completed health probe batch",
        request_source=request_source,
        **counts,
    )
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
