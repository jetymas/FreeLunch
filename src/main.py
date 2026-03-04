from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

from src.config import Settings
from src.db import Database
from src.providers.registry import ProviderRegistry
from src.proxy import build_router
from src.scheduler import build_scheduler, register_jobs, run_discovery_pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    db = Database(settings.database_url)
    db.init()
    db.writer.start()

    settings.apply_overrides(db.get_overrides())

    registry = ProviderRegistry()
    registry.register_openrouter(
        api_key=settings.openrouter_api_key, api_base=settings.openrouter_api_base
    )

    app.state.db = db
    app.state.registry = registry
    app.state.settings = settings
    app.state.scheduler = build_scheduler()
    app.state.ready = False
    app.state.force_discovery = False
    app.state.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    app.state.job_status = {}
    app.state.discovery_lock = asyncio.Lock()

    def reload_settings() -> Settings:
        new_settings = Settings.from_env()
        new_settings.apply_overrides(db.get_overrides())
        registry.register_openrouter(
            api_key=new_settings.openrouter_api_key,
            api_base=new_settings.openrouter_api_base,
        )
        app.state.settings = new_settings
        health_job = app.state.scheduler.get_job("health")
        if health_job is not None:
            app.state.scheduler.reschedule_job(
                "health",
                trigger=IntervalTrigger(minutes=new_settings.health_probe_interval_minutes),
            )
        return new_settings

    def recompute_readiness() -> bool:
        with db.read_conn() as conn:
            routable_count = conn.execute(
                "SELECT COUNT(*) FROM models WHERE is_healthy=1 AND is_active=1"
            ).fetchone()[0]
        app.state.ready = routable_count > 0
        return bool(app.state.ready)

    app.state.recompute_readiness = recompute_readiness
    app.state.reload_settings = reload_settings

    await run_discovery_pipeline(
        db,
        registry,
        settings=settings,
        recompute_readiness=recompute_readiness,
    )

    register_jobs(app.state.scheduler, db, registry, app.state)
    app.state.scheduler.start()
    try:
        yield
    finally:
        app.state.scheduler.shutdown(wait=False)
        db.writer.stop()


app = FastAPI(title="FreeLunch", version="0.2.0", lifespan=lifespan)
app.include_router(build_router())
