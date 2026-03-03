from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.discover import run_discovery
from src.health import startup_health_pass
from src.ranking import recompute_ranking


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    return scheduler


def register_jobs(scheduler: AsyncIOScheduler, db, registry) -> None:
    async def discovery_job() -> None:
        await run_discovery(db, registry)
        db.writer.flush()

    def rank_job() -> None:
        recompute_ranking(db)
        db.writer.flush()

    def health_job() -> None:
        startup_health_pass(db, max_models=3)
        db.writer.flush()

    scheduler.add_job(discovery_job, IntervalTrigger(minutes=30), id="discovery", replace_existing=True)
    scheduler.add_job(rank_job, IntervalTrigger(minutes=15), id="ranking", replace_existing=True)
    scheduler.add_job(health_job, IntervalTrigger(minutes=20), id="health", replace_existing=True)
