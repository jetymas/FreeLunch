from __future__ import annotations

from contextlib import asynccontextmanager
import time

from fastapi import FastAPI

from src.config import Settings
from src.db import Database
from src.discover import run_discovery
from src.health import startup_health_pass
from src.providers.registry import ProviderRegistry
from src.proxy import build_router
from src.ranking import recompute_ranking
from src.scheduler import build_scheduler, register_jobs


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    db = Database(settings.database_url)
    db.init()
    db.writer.start()

    settings.apply_overrides(db.get_overrides())

    registry = ProviderRegistry()
    registry.register_openrouter(api_key=settings.openrouter_api_key, api_base=settings.openrouter_api_base)

    app.state.db = db
    app.state.registry = registry
    app.state.settings = settings
    app.state.scheduler = build_scheduler()
    app.state.ready = False
    app.state.force_discovery = False
    app.state.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    app.state.job_status = {}

    discovered = await run_discovery(db, registry)
    db.writer.flush()

    if discovered:
        recompute_ranking(db)
        db.writer.flush()
        startup_health_pass(db, max_models=settings.startup_probe_limit)
        db.writer.flush()

    with db.read_conn() as conn:
        routable_count = conn.execute(
            "SELECT COUNT(*) FROM models WHERE is_healthy=1 AND is_active=1"
        ).fetchone()[0]
    app.state.ready = routable_count > 0

    register_jobs(app.state.scheduler, db, registry, app.state)
    app.state.scheduler.start()
    try:
        yield
    finally:
        app.state.scheduler.shutdown(wait=False)
        db.writer.stop()


app = FastAPI(title="FreeLunch", version="0.2.0", lifespan=lifespan)
app.include_router(build_router())
