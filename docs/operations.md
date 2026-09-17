# Operations

This is the living operator runbook. The implemented setting names are defined
by [`config.yaml.example`](../config.yaml.example); endpoint behavior is defined
by [`src/proxy.py`](../src/proxy.py) and [`src/admin_ui.py`](../src/admin_ui.py).

## Deployment modes

Docker and the shell/PowerShell installers are supported convenience paths.
They are optional: a native Python deployment can run `uvicorn src.main:app`
with the same configuration and persistence model. Keep one process and one
SQLite database per deployment unless the application is deliberately redesigned
for shared state.

For Docker:

```bash
cp config.yaml.example config.yaml
cp .env.example .env
docker compose up -d
```

For native execution:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

Configure at least one real provider key. The OpenRouter no-key stub is allowed
only with `APP_ENV=dev` and explicit `providers.openrouter.dev_stub_enabled=true`.
Never use it as evidence of production provider readiness.

## First checks

```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
curl http://localhost:8000/v1/models
curl http://localhost:8000/admin/health
```

`/healthz` is liveness. `/readyz` requires an active, healthy, routable model.
If readiness is `503`, inspect `/admin/health`, `/admin/models`, startup logs,
provider credentials, inference gates, and discovery results.

## Configuration and secrets

Use `config.yaml.example` for current keys and defaults. Provider credential
resolution and provider gates are implemented in `src/config.py` and
`src/providers/registry.py`; do not duplicate provider lists here.

Keep `.env` and populated config files out of version control. Prefer the
encrypted managed secret vault for runtime provider keys. Gateway auth is a
separate control: enable it before exposing client or admin endpoints beyond a
trusted local network. Rotate leaked provider or gateway keys immediately.

## Admin and observability

`/admin/ui` is a lightweight single-node operator console. The JSON admin API
provides model inspection, health/scheduler state, effective configuration and
overrides, durable request telemetry, gateway-auth controls, and managed secret
operations. Admin responses must never be treated as a place to retrieve raw
secrets.

Runtime logs are ephemeral JSON process output controlled by
`logging.runtime_enabled`, `logging.runtime_verbosity`, and
`logging.runtime_queue_size`. Durable `request_log` telemetry is separate and
is controlled by the request-log settings. Queue pressure may drop low-priority
client logs; it must not bypass the database writer policy for metadata.

Useful runtime verbosity:

- `concise` for production;
- `verbose` for integration work;
- `debug` while investigating scheduler, routing, health, or tokenizer behavior.

## Discovery, health, and routing

Discovery reconciles provider model rows and can enrich them from best-effort
Chatbot Arena and Open LLM leaderboard refreshes. Upstream artifact failures
should reduce enrichment quality, not stop the gateway. Ranking, health, and
discovery intervals are configurable and reloadable.

Routing filters for active/healthy state, capability compatibility, cooldown,
context window, and output capacity before using score and request preferences.
Failover is bounded. `CONTEXT_EXCEEDED` can select an alternate, but exhausted
context-only attempts return `400` and do not count as a health penalty.
Streaming cannot fail over after partial output has been sent.

## Token estimation

Token estimation is local-only by policy: use safe exact local tokenizers where
available and calibrated heuristics for unresolved families. The request path
does not call remote token-count APIs. Background tokenizer preload is best
effort and may be cancelled during shutdown. Inspect `token_estimation_review`
in `/admin/health` when investigating context failures or estimate drift.

## Persistence and lifecycle

SQLite uses WAL and a busy timeout. All normal application writes go through the
writer thread. The maintenance job enforces request-log retention. Preserve the
database file and configuration directory during upgrades; take a backup before
migrations or host-level cleanup.

On startup the app initializes migrations, starts the writer, applies overrides,
registers providers, runs discovery/ranking/health bootstrap, computes
readiness, and registers recurring jobs. On shutdown it stops scheduler and
background work cleanly. A provider outage should degrade readiness rather than
crash the process.

## Validation

Run the local gate from the repository root:

```bash
python -m ruff check .
python -m mypy src
python -m pytest tests -q --basetemp .pytest_tmp_local -p no:cacheprovider
python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_cov -p no:cacheprovider
```

For a minimal keyed provider check, use `python scripts/provider_smoke.py --json`.
CI also exercises installer syntax and Docker runtime smoke; the authoritative
commands are in `.github/workflows/ci.yml`.

## Common incidents

| Symptom | First checks |
| --- | --- |
| `/readyz` is `503` | credentials, provider gates, `/admin/health`, discovery rows |
| no models | provider discovery logs, API limits, benchmark refresh warnings |
| poor ranking | health/cooldown state, benchmark cache freshness, ranking settings |
| context failures | candidate context windows and `token_estimation_review` |
| log queue pressure | runtime verbosity, sink health, dropped-record counters |
| vault/admin concern | gateway auth mode, vault lock state, network exposure |

For code-level diagnosis, follow the boundaries in
[`architecture.md`](./architecture.md) and inspect the linked source rather than
adding another operational copy of implementation details here.
