from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .config import get_settings
from .db import Database
from .discover import run_discovery
from .health import run_startup_health_pass
from .providers.registry import ProviderRegistry
from .proxy import router
from .ranking import recompute_rankings
from .scheduler import start_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db_path = Path(settings.database_url)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db = Database(str(db_path))
    db.migrate()
    db.start_writer()

    registry = ProviderRegistry(settings=settings)

    app.state.settings = settings
    app.state.db = db
    app.state.registry = registry
    app.state.ready = False

    run_discovery(db, registry)
    recompute_rankings(db)
    run_startup_health_pass(db)
    db.flush_writes()

    conn = db.connect()
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM models WHERE is_healthy = 1").fetchone()["c"]
        app.state.ready = count > 0
    finally:
        conn.close()

    app.state.scheduler = start_scheduler(app)

    try:
        yield
    finally:
        app.state.scheduler.shutdown(wait=False)
        db.stop_writer()


def create_app() -> FastAPI:
    application = FastAPI(title="FreeLunch", version="0.2.0", lifespan=lifespan)
    application.include_router(router)
    return application


app = create_app()
