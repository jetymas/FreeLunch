from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.scheduler as scheduler_module


class _DummyWriter:
    def __init__(self) -> None:
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1


class _DummyDb:
    def __init__(self) -> None:
        self.writer = _DummyWriter()
        self.prune_calls: list[int] = []

    def prune_old_logs(self, *, retention_days: int) -> None:
        self.prune_calls.append(retention_days)


class _DummyJob:
    def __init__(self, func, trigger) -> None:
        self.func = func
        self.trigger = trigger


class _DummyScheduler:
    def __init__(self) -> None:
        self.jobs: dict[str, _DummyJob] = {}

    def add_job(self, func, trigger, **kwargs) -> None:
        job_id = kwargs["id"]
        self.jobs[job_id] = _DummyJob(func, trigger)

    def get_job(self, job_id: str) -> _DummyJob | None:
        return self.jobs.get(job_id)


def test_track_job_success_updates_status_and_clears_error() -> None:
    job_status: dict[str, dict[str, object]] = {"ranking": {"run_count": 0, "last_error": "old"}}

    result = scheduler_module._track_job(job_status, "ranking", lambda: {"ok": True})

    assert result == {"ok": True}
    entry = job_status["ranking"]
    assert entry["run_count"] == 1
    assert entry["last_error"] is None
    assert isinstance(entry["last_started_at"], str)
    assert isinstance(entry["last_success_at"], str)


def test_track_job_failure_reraises_and_tracks_last_error() -> None:
    message = "x" * 600
    job_status: dict[str, dict[str, object]] = {"maintenance": {"run_count": "bad"}}

    with pytest.raises(RuntimeError, match="x{20}"):
        scheduler_module._track_job(
            job_status, "maintenance", lambda: (_ for _ in ()).throw(RuntimeError(message))
        )

    entry = job_status["maintenance"]
    assert entry["run_count"] == 1
    assert entry["last_error"] == message[:500]
    assert "last_success_at" not in entry

    result = scheduler_module._track_job(job_status, "maintenance", lambda: "recovered")
    assert result == "recovered"
    assert entry["run_count"] == 2
    assert entry["last_error"] is None
    assert isinstance(entry["last_success_at"], str)


@pytest.mark.asyncio
async def test_track_job_async_success_updates_status_and_clears_error() -> None:
    job_status: dict[str, dict[str, object]] = {"health": {"run_count": "bad", "last_error": "old"}}

    async def _run() -> int:
        return 7

    result = await scheduler_module._track_job_async(job_status, "health", _run)

    assert result == 7
    entry = job_status["health"]
    assert entry["run_count"] == 1
    assert entry["last_error"] is None
    assert isinstance(entry["last_started_at"], str)
    assert isinstance(entry["last_success_at"], str)


@pytest.mark.asyncio
async def test_track_job_async_failure_reraises_and_tracks_last_error() -> None:
    message = "failure-" * 100
    job_status: dict[str, dict[str, object]] = {"discovery": {"run_count": 1}}

    async def _fail() -> None:
        raise ValueError(message)

    with pytest.raises(ValueError, match="failure-"):
        await scheduler_module._track_job_async(job_status, "discovery", _fail)

    entry = job_status["discovery"]
    assert entry["run_count"] == 2
    assert entry["last_error"] == message[:500]

    async def _recover() -> str:
        return "ok"

    result = await scheduler_module._track_job_async(job_status, "discovery", _recover)
    assert result == "ok"
    assert entry["run_count"] == 3
    assert entry["last_error"] is None
    assert isinstance(entry["last_success_at"], str)


def test_register_jobs_sync_wrappers_use_tracking_and_expected_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _DummyScheduler()
    db = _DummyDb()
    registry = object()
    settings = SimpleNamespace(
        discovery_interval_minutes=15,
        ranking_interval_minutes=5,
        health_probe_interval_minutes=2,
        logging_request_log_retention_days=14,
    )

    reload_calls = 0

    def _reload_settings() -> None:
        nonlocal reload_calls
        reload_calls += 1

    app_state = SimpleNamespace(
        settings=settings,
        job_status={},
        recompute_readiness=lambda: True,
        force_discovery=False,
        reload_settings=_reload_settings,
    )

    tracked_jobs: list[str] = []
    ranking_calls: list[tuple[object, object]] = []

    def _fake_track_job(job_status, name, fn):
        tracked_jobs.append(name)
        assert job_status is app_state.job_status
        return fn()

    def _fake_recompute_ranking(db_arg, *, settings):
        ranking_calls.append((db_arg, settings))
        return 3

    monkeypatch.setattr(scheduler_module, "_track_job", _fake_track_job)
    monkeypatch.setattr(scheduler_module, "recompute_ranking", _fake_recompute_ranking)

    scheduler_module.register_jobs(scheduler, db, registry, app_state)

    assert app_state.config_refresh_runner is not None
    scheduler.get_job("ranking").func()
    scheduler.get_job("maintenance").func()
    app_state.config_refresh_runner()

    assert tracked_jobs == ["ranking", "maintenance", "config_refresh"]
    assert ranking_calls == [(db, settings)]
    assert db.writer.flush_count == 1
    assert db.prune_calls == [14]
    assert reload_calls == 1
    assert set(app_state.job_status.keys()) == {
        "discovery",
        "ranking",
        "health",
        "maintenance",
        "config_refresh",
    }


@pytest.mark.asyncio
async def test_register_jobs_health_wrapper_uses_async_tracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _DummyScheduler()
    db = _DummyDb()
    registry = object()
    settings = SimpleNamespace(
        discovery_interval_minutes=15,
        ranking_interval_minutes=5,
        health_probe_interval_minutes=2,
        logging_request_log_retention_days=14,
    )

    readiness_recompute_calls = 0

    def _recompute_readiness() -> bool:
        nonlocal readiness_recompute_calls
        readiness_recompute_calls += 1
        return True

    app_state = SimpleNamespace(
        settings=settings,
        job_status={},
        recompute_readiness=_recompute_readiness,
        force_discovery=False,
        reload_settings=lambda: settings,
    )

    tracked_jobs: list[str] = []
    health_calls: list[tuple[object, object, object]] = []

    async def _fake_track_job_async(job_status, name, fn):
        tracked_jobs.append(name)
        assert job_status is app_state.job_status
        return await fn()

    async def _fake_run_health_checks(db_arg, registry_arg, settings_arg) -> None:
        health_calls.append((db_arg, registry_arg, settings_arg))

    monkeypatch.setattr(scheduler_module, "_track_job_async", _fake_track_job_async)
    monkeypatch.setattr(scheduler_module, "run_health_checks", _fake_run_health_checks)

    scheduler_module.register_jobs(scheduler, db, registry, app_state)

    health_job = scheduler.get_job("health")
    assert health_job is not None
    await health_job.func()

    assert tracked_jobs == ["health"]
    assert health_calls == [(db, registry, settings)]
    assert db.writer.flush_count == 1
    assert readiness_recompute_calls == 1
