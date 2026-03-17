from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

from src.admin_ui import build_admin_ui_router
from src.config import Settings
from src.db import Database
from src.providers.registry import ProviderRegistry
from src.proxy import build_router
from src.runtime_logging import (
    configure_runtime_logging,
    get_logger,
    runtime_log,
    shutdown_runtime_logging,
)
from src.scheduler import build_scheduler, register_jobs, run_discovery_pipeline
from src.secret_store import GatewayAuthConfig, ManagedSecretStore, SecretVaultConfig
from src.tokens import shutdown_tokenizer_preloads

logger = get_logger(__name__)


def _sync_registry_runtime_gating(settings: Settings, registry: ProviderRegistry) -> None:
    for registered in registry.all_registered():
        configured_discovery_enabled = settings.is_provider_discovery_enabled(registered.name)
        configured_inference_enabled = settings.is_provider_inference_enabled(registered.name)
        runtime_state = registered.adapter.runtime_state()
        registered.discovery_enabled = (
            configured_discovery_enabled and runtime_state.discovery_available
        )
        registered.inference_enabled = (
            configured_inference_enabled and runtime_state.inference_available
        )


def _configure_registry(settings: Settings, registry: ProviderRegistry) -> None:
    registry.register_configured(settings)
    _sync_registry_runtime_gating(settings, registry)


def _configure_database_logging(settings: Settings, db: Database) -> None:
    db.configure_logging(
        request_log_enabled=settings.logging_request_log_enabled,
        request_log_queue_size=settings.logging_log_queue_size,
    )


def _configure_runtime_logger(settings: Settings) -> None:
    configure_runtime_logging(
        enabled=settings.logging_runtime_enabled,
        verbosity=settings.logging_runtime_verbosity,
        queue_size=settings.logging_runtime_queue_size,
    )


def _get_secret_vault_config(db: Database) -> SecretVaultConfig | None:
    row = db.get_secret_vault_config()
    if row is None:
        return None
    return SecretVaultConfig(
        salt_b64=str(row["salt_b64"]),
        verifier_encrypted=str(row["verifier_encrypted"]),
    )


def _load_managed_secrets(
    db: Database,
    *,
    secret_store: ManagedSecretStore | None,
    vault_config: SecretVaultConfig | None,
) -> tuple[dict[str, str], dict[str, object]]:
    encrypted = db.get_managed_secret_values()
    status: dict[str, object] = {
        "configured": vault_config is not None,
        "unlocked": secret_store is not None,
        "stored_secret_count": len(encrypted),
        "loaded_secret_count": 0,
        "decrypt_failures": [],
    }
    if secret_store is None:
        return {}, status

    secrets, failures = secret_store.decrypt_mapping(encrypted)
    status["loaded_secret_count"] = len(secrets)
    status["decrypt_failures"] = failures
    return secrets, status


def _get_gateway_auth_config(db: Database) -> dict[str, object]:
    row = db.get_gateway_auth_config()
    if row is None:
        return {
            "mode": "inherit",
            "enabled": False,
            "source": "disabled",
            "env_configured": False,
            "updated_at": None,
            "config": None,
        }

    mode = str(row["mode"] or "inherit")
    config: GatewayAuthConfig | None = None
    if mode == "enabled" and row["token_salt_b64"] and row["token_hash_b64"]:
        config = GatewayAuthConfig(
            token_salt_b64=str(row["token_salt_b64"]),
            token_hash_b64=str(row["token_hash_b64"]),
        )
    return {
        "mode": mode,
        "enabled": mode == "enabled" and config is not None,
        "source": "managed" if mode == "enabled" and config is not None else "disabled",
        "env_configured": False,
        "updated_at": row["updated_at"],
        "config": config,
    }


def _resolve_gateway_auth_state(settings: Settings, db: Database) -> dict[str, object]:
    env_key = settings.gateway_api_key.strip()
    managed = _get_gateway_auth_config(db)
    mode = str(managed["mode"])
    if mode == "enabled" and managed["config"] is not None:
        return {
            "mode": mode,
            "enabled": True,
            "source": "managed",
            "env_configured": bool(env_key),
            "updated_at": managed["updated_at"],
            "config": managed["config"],
            "env_key": None,
        }
    if mode == "disabled":
        return {
            "mode": mode,
            "enabled": False,
            "source": "disabled",
            "env_configured": bool(env_key),
            "updated_at": managed["updated_at"],
            "config": None,
            "env_key": None,
        }
    return {
        "mode": "inherit",
        "enabled": bool(env_key),
        "source": "env" if env_key else "disabled",
        "env_configured": bool(env_key),
        "updated_at": managed["updated_at"],
        "config": None,
        "env_key": env_key or None,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    _configure_runtime_logger(settings)
    runtime_log(
        logger,
        "app.starting",
        verbosity="concise",
        message="Starting FreeLunch application",
        app_env=settings.app_env,
        runtime_log_verbosity=settings.logging_runtime_verbosity,
    )
    db = Database(
        settings.database_url,
        busy_timeout_ms=settings.database_busy_timeout_ms,
    )
    db.init()
    db.writer.start()

    settings.apply_overrides(db.get_overrides())
    secret_vault_config = _get_secret_vault_config(db)
    secret_store = None
    managed_secrets, secret_status = _load_managed_secrets(
        db,
        secret_store=secret_store,
        vault_config=secret_vault_config,
    )
    settings.apply_managed_secrets(managed_secrets)
    _configure_database_logging(settings, db)
    gateway_auth = _resolve_gateway_auth_state(settings, db)

    registry = ProviderRegistry()
    _configure_registry(settings, registry)

    app.state.db = db
    app.state.registry = registry
    app.state.settings = settings
    app.state.secret_vault_config = secret_vault_config
    app.state.secret_store = secret_store
    app.state.secret_management_status = secret_status
    app.state.gateway_auth = gateway_auth
    app.state.scheduler = build_scheduler()
    app.state.ready = False
    app.state.force_discovery = False
    app.state.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    app.state.job_status = {}
    app.state.discovery_lock = asyncio.Lock()

    def apply_provider_runtime_state(current_settings: Settings) -> None:
        with db.read_conn() as conn:
            active_provider_rows = conn.execute(
                """
                SELECT DISTINCT provider_id
                FROM models
                WHERE is_active=1
                """
            ).fetchall()

        runtime_enabled_by_provider = {
            registered.name: bool(registered.inference_enabled)
            for registered in registry.all_registered()
        }
        provider_ids = set(runtime_enabled_by_provider.keys())
        provider_ids.update(
            str(row[0])
            for row in active_provider_rows
            if row[0] is not None and str(row[0]).strip()
        )
        for provider_id in current_settings.known_provider_ids:
            if current_settings.is_provider_inference_enabled(provider_id):
                provider_ids.add(provider_id)

        disabled_provider_ids: list[str] = []
        for provider_id in sorted(provider_ids):
            if runtime_enabled_by_provider.get(provider_id, False):
                continue
            disabled_provider_ids.append(provider_id)
            db.writer.enqueue(
                "UPDATE models SET is_active=0 WHERE provider_id=? AND is_active=1",
                (provider_id,),
            )

        if disabled_provider_ids:
            db.writer.flush()
            for provider_id in disabled_provider_ids:
                registered = next(
                    (item for item in registry.all_registered() if item.name == provider_id),
                    None,
                )
                runtime_state = registered.adapter.runtime_state() if registered else None
                configured_inference_enabled = current_settings.is_provider_inference_enabled(
                    provider_id
                )
                runtime_log(
                    logger,
                    "provider.runtime_disabled",
                    verbosity="concise",
                    message="Provider models deactivated because runtime inference is unavailable",
                    provider_id=provider_id,
                    configured_inference_enabled=configured_inference_enabled,
                    registered=registered is not None,
                    runtime_inference_available=runtime_state.inference_available
                    if runtime_state is not None
                    else False,
                )

    def reload_settings() -> Settings:
        new_settings = Settings.from_env()
        new_settings.apply_overrides(db.get_overrides())
        new_secret_vault_config = _get_secret_vault_config(db)
        new_secret_store = getattr(app.state, "secret_store", None)
        managed_secrets, secret_status = _load_managed_secrets(
            db,
            secret_store=new_secret_store,
            vault_config=new_secret_vault_config,
        )
        new_settings.apply_managed_secrets(managed_secrets)
        _configure_runtime_logger(new_settings)
        _configure_registry(new_settings, registry)
        _configure_database_logging(new_settings, db)
        app.state.settings = new_settings
        app.state.secret_vault_config = new_secret_vault_config
        app.state.secret_store = new_secret_store
        app.state.secret_management_status = secret_status
        app.state.gateway_auth = _resolve_gateway_auth_state(new_settings, db)
        apply_provider_runtime_state(new_settings)
        discovery_job = app.state.scheduler.get_job("discovery")
        if discovery_job is not None:
            app.state.scheduler.reschedule_job(
                "discovery",
                trigger=IntervalTrigger(minutes=new_settings.discovery_interval_minutes),
            )
        ranking_job = app.state.scheduler.get_job("ranking")
        if ranking_job is not None:
            app.state.scheduler.reschedule_job(
                "ranking",
                trigger=IntervalTrigger(minutes=new_settings.ranking_interval_minutes),
            )
        health_job = app.state.scheduler.get_job("health")
        if health_job is not None:
            app.state.scheduler.reschedule_job(
                "health",
                trigger=IntervalTrigger(minutes=new_settings.health_probe_interval_minutes),
            )
        app.state.recompute_readiness()
        runtime_log(
            logger,
            "config.reloaded",
            verbosity="verbose",
            message="Reloaded runtime settings",
            discovery_interval_minutes=new_settings.discovery_interval_minutes,
            ranking_interval_minutes=new_settings.ranking_interval_minutes,
            health_interval_minutes=new_settings.health_probe_interval_minutes,
            runtime_log_verbosity=new_settings.logging_runtime_verbosity,
        )
        return new_settings

    def recompute_readiness() -> bool:
        apply_provider_runtime_state(app.state.settings)
        previous_ready = bool(getattr(app.state, "ready", False))
        with db.read_conn() as conn:
            routable_count = conn.execute(
                "SELECT COUNT(*) FROM models WHERE is_healthy=1 AND is_active=1"
            ).fetchone()[0]
        app.state.ready = routable_count > 0
        if bool(app.state.ready) != previous_ready:
            runtime_log(
                logger,
                "readiness.changed",
                verbosity="concise",
                message="Gateway readiness changed",
                ready=bool(app.state.ready),
                routable_count=int(routable_count or 0),
            )
        return bool(app.state.ready)

    app.state.recompute_readiness = recompute_readiness
    app.state.reload_settings = reload_settings
    apply_provider_runtime_state(settings)

    try:
        startup_outcome = await run_discovery_pipeline(
            db,
            registry,
            settings=settings,
            recompute_readiness=recompute_readiness,
        )
    except Exception:
        recompute_readiness()
        startup_outcome = {
            "discovered": 0,
            "ranking_updates": 0,
            "probed_models": 0,
            "ready": bool(app.state.ready),
        }
        runtime_log(
            logger,
            "app.startup_pipeline_failed",
            verbosity="concise",
            level=40,
            message="Startup discovery pipeline failed; continuing in degraded mode",
            ready=bool(app.state.ready),
            exc_info=True,
        )
    recompute_readiness()
    runtime_log(
        logger,
        "app.started",
        verbosity="concise",
        message="FreeLunch startup completed",
        ready=bool(app.state.ready),
        discovered=startup_outcome.get("discovered"),
        ranking_updates=startup_outcome.get("ranking_updates"),
        probed_models=startup_outcome.get("probed_models"),
    )

    register_jobs(app.state.scheduler, db, registry, app.state)
    app.state.scheduler.start()
    try:
        yield
    finally:
        runtime_log(
            logger,
            "app.stopping",
            verbosity="concise",
            message="Stopping FreeLunch application",
        )
        app.state.scheduler.shutdown(wait=False)
        db.writer.stop()
        shutdown_tokenizer_preloads()
        runtime_log(
            logger,
            "app.stopped",
            verbosity="concise",
            message="FreeLunch application stopped",
        )
        shutdown_runtime_logging()


app = FastAPI(title="FreeLunch", version="0.4.0", lifespan=lifespan)
app.include_router(build_router())
app.include_router(build_admin_ui_router())
