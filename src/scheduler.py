from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from .discover import run_discovery
from .health import run_startup_health_pass
from .ranking import recompute_rankings


def start_scheduler(app) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_discovery, "interval", minutes=60, args=[app.state.db, app.state.registry])
    scheduler.add_job(recompute_rankings, "interval", minutes=60, args=[app.state.db])
    scheduler.add_job(run_startup_health_pass, "interval", minutes=180, args=[app.state.db])
    scheduler.start()
    return scheduler
