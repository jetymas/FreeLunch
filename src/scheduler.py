from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.discover import run_discovery
from src.health import bootstrap_health_check, run_health_checks
from src.ranking import recompute_ranking


async def run_discovery_pipeline(
    db,
    registry,
    *,
    settings,
    recompute_readiness: Callable[[], bool],
) -> dict[str, int | bool]:
    discovered = await run_discovery(db, registry, settings=settings)
    db.writer.flush()

    ranking_updates = recompute_ranking(db, settings=settings)
    db.writer.flush()

    health_outcome = await bootstrap_health_check(db, registry, settings)
    db.writer.flush()

    ready = recompute_readiness()
    return {
        "discovered": discovered,
        "ranking_updates": ranking_updates,
        "probed_models": health_outcome["probed"],
        "ready": ready,
    }


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    return scheduler


def _mark_job_start(job_status: dict[str, dict[str, object]], name: str) -> dict[str, object]:
    entry = job_status.setdefault(name, {"run_count": 0})
    entry["last_started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return entry


def _mark_job_success(entry: dict[str, object]) -> None:
    entry["last_success_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry["last_error"] = None
    run_count = entry.get("run_count", 0)
    entry["run_count"] = run_count + 1 if isinstance(run_count, int) else 1


def _mark_job_failure(entry: dict[str, object], exc: Exception) -> None:
    entry["last_error"] = str(exc)[:500]
    run_count = entry.get("run_count", 0)
    entry["run_count"] = run_count + 1 if isinstance(run_count, int) else 1


def _track_job(
    job_status: dict[str, dict[str, object]], name: str, fn: Callable[[], object]
) -> object:
    entry = _mark_job_start(job_status, name)
    try:
        result = fn()
        _mark_job_success(entry)
        return result
    except Exception as exc:
        _mark_job_failure(entry, exc)
        raise


async def _track_job_async(
    job_status: dict[str, dict[str, object]],
    name: str,
    fn: Callable[[], Awaitable[object]],
) -> object:
    entry = _mark_job_start(job_status, name)
    try:
        result = await fn()
        _mark_job_success(entry)
        return result
    except Exception as exc:
        _mark_job_failure(entry, exc)
        raise


def register_jobs(scheduler: AsyncIOScheduler, db, registry, app_state) -> None:
    for job_name in ("discovery", "ranking", "health", "maintenance", "config_refresh"):
        app_state.job_status.setdefault(job_name, {"run_count": 0})

    async def discovery_runner() -> dict[str, int | bool]:
        async def _run() -> dict[str, int | bool]:
            outcome = await run_discovery_pipeline(
                db,
                registry,
                settings=app_state.settings,
                recompute_readiness=app_state.recompute_readiness,
            )
            if app_state.force_discovery:
                app_state.force_discovery = False
            return outcome

        result = await _track_job_async(app_state.job_status, "discovery", _run)
        return result if isinstance(result, dict) else {}

    app_state.discovery_runner = discovery_runner

    def rank_job() -> None:
        _track_job(
            app_state.job_status,
            "ranking",
            lambda: (recompute_ranking(db, settings=app_state.settings), db.writer.flush()),
        )

    async def health_job() -> None:
        async def _run() -> None:
            await run_health_checks(db, registry, app_state.settings)
            db.writer.flush()
            app_state.recompute_readiness()

        await _track_job_async(app_state.job_status, "health", _run)

    def maintenance_job() -> None:
        _track_job(
            app_state.job_status,
            "maintenance",
            lambda: db.prune_old_logs(
                retention_days=app_state.settings.logging_request_log_retention_days
            ),
        )

    def config_refresh_job() -> None:
        _track_job(
            app_state.job_status,
            "config_refresh",
            app_state.reload_settings,
        )

    app_state.config_refresh_runner = config_refresh_job

    scheduler.add_job(
        discovery_runner,
        IntervalTrigger(minutes=app_state.settings.discovery_interval_minutes),
        id="discovery",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        rank_job,
        IntervalTrigger(minutes=app_state.settings.ranking_interval_minutes),
        id="ranking",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        health_job,
        IntervalTrigger(minutes=app_state.settings.health_probe_interval_minutes),
        id="health",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        maintenance_job,
        IntervalTrigger(hours=24),
        id="maintenance",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        config_refresh_job,
        IntervalTrigger(minutes=5),
        id="config_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
