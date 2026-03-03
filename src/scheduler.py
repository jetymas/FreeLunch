from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.discover import run_discovery
from src.health import startup_health_pass
from src.ranking import recompute_ranking


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
    entry["run_count"] = int(entry.get("run_count", 0)) + 1


def _mark_job_failure(entry: dict[str, object], exc: Exception) -> None:
    entry["last_error"] = str(exc)[:500]
    entry["run_count"] = int(entry.get("run_count", 0)) + 1


def _track_job(job_status: dict[str, dict[str, object]], name: str, fn: Callable[[], object]) -> object:
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
    def rank_job() -> None:
        _track_job(
            app_state.job_status,
            "ranking",
            lambda: (recompute_ranking(db), db.writer.flush()),
        )

    def health_job() -> None:
        _track_job(
            app_state.job_status,
            "health",
            lambda: (startup_health_pass(db, max_models=3), db.writer.flush()),
        )

    async def discovery_job() -> None:
        async def _run() -> None:
            await run_discovery(db, registry)
            db.writer.flush()
            if app_state.force_discovery:
                app_state.force_discovery = False
            recompute_ranking(db)
            db.writer.flush()
            startup_health_pass(db, max_models=3)
            db.writer.flush()

        await _track_job_async(app_state.job_status, "discovery", _run)

    scheduler.add_job(discovery_job, IntervalTrigger(minutes=30), id="discovery", replace_existing=True)
    scheduler.add_job(rank_job, IntervalTrigger(minutes=15), id="ranking", replace_existing=True)
    scheduler.add_job(health_job, IntervalTrigger(minutes=20), id="health", replace_existing=True)
