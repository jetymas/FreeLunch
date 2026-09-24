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
uvicorn src.main:app --host 127.0.0.1 --port 8000
```

### Native user service (systemd)

For a Linux host that uses systemd, the repository includes
[`deploy/freelunch.service.example`](../deploy/freelunch.service.example). It
runs one Uvicorn process as the logged-in user and listens on loopback. From the
repository checkout, create the environment and config if needed:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
test -e config.yaml || cp config.yaml.example config.yaml
mkdir -p "$HOME/.config/freelunch" "$HOME/.local/share/freelunch"
chmod 700 "$HOME/.config/freelunch" "$HOME/.local/share/freelunch"
touch "$HOME/.config/freelunch/freelunch.env"
chmod 600 "$HOME/.config/freelunch/freelunch.env"
```

Put provider keys and, when desired, `GATEWAY_API_KEY` in
`~/.config/freelunch/freelunch.env` (one `NAME=value` per line). Copy the unit
to `~/.config/systemd/user/freelunch.service`, replace both
`/absolute/path/to/FreeLunch` paths with the checkout's absolute path, then
enable it:

```bash
mkdir -p "$HOME/.config/systemd/user"
cp deploy/freelunch.service.example "$HOME/.config/systemd/user/freelunch.service"
systemctl --user daemon-reload
systemctl --user enable --now freelunch.service
systemctl --user status freelunch.service
```

The database is stored at `~/.local/share/freelunch/freelunch.db`; configuration
stays in the checkout, and secrets stay in the mode-600 environment file. Check
startup with `curl http://127.0.0.1:8000/healthz` and
`curl --fail http://127.0.0.1:8000/readyz`. Readiness requires a routable model,
so confirm provider keys and gates if it returns `503`. With gateway auth
enabled, send `Authorization: Bearer <your-gateway-key>` on authenticated API
requests. For a basic inference check, send a small request to
`/v1/chat/completions` as shown in the README.

For an upgrade, stop the service, back up the database and environment file,
update the checkout, refresh dependencies, then start the service again:

```bash
systemctl --user stop freelunch.service
sqlite3 "$HOME/.local/share/freelunch/freelunch.db" ".backup '$HOME/.local/share/freelunch/freelunch.db.bak'"
cp "$HOME/.config/freelunch/freelunch.env" "$HOME/.config/freelunch/freelunch.env.bak"
git pull --ff-only
.venv/bin/pip install -r requirements.txt
systemctl --user start freelunch.service
```

The application applies SQLite migrations at startup. Keep the database backup
until readiness and a request have succeeded. To uninstall while preserving
data, disable the unit and remove the service file; explicitly archive or
remove the config, secrets, and database directories according to your
retention needs:

```bash
systemctl --user disable --now freelunch.service
rm "$HOME/.config/systemd/user/freelunch.service"
systemctl --user daemon-reload
```

If the service must start after logout or system boot, enable user lingering
with the host's systemd login policy. Otherwise it runs only while the user
manager is active. Do not expose the bind beyond loopback without gateway auth
and suitable network controls.

Docker Compose and the installers likewise publish the gateway on loopback by
default. A non-loopback bind is an explicit deployment decision and requires a
gateway key plus suitable firewall or reverse-proxy controls.

For a checkout-based Docker deployment, stop Compose before a file-level
backup, copy `data/`, `config.yaml`, and `.env` to a protected location, then
update the checkout and rebuild:

```bash
docker compose stop
mkdir -m 700 ../freelunch-backup
cp -a data config.yaml .env ../freelunch-backup/
git pull --ff-only
docker compose up --build -d
```

Check `/healthz`, `/readyz`, and a small request before discarding backups.
`docker compose down` removes the service while keeping the bind-mounted data
and local configuration. Remove those files only after choosing a retention
policy. Installer deployments store the same kinds of files under their install
directory; inspect the uninstall script before removing that directory.

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

The `security` settings in `config.yaml.example` cap chat request bodies,
upstream responses, SSE events, and stream idle and total duration. Oversized
client chat bodies return `413`; upstream limit and stream deadline failures
enter the normal provider failure path. API documentation routes are disabled
when `APP_ENV=prod` or `app.env: prod`, unless `API_DOCS_ENABLED=true` is set
at process startup. Responses include browser security headers for the admin UI
and API.

Failed gateway bearer checks and vault unlocks have separate, in-memory
per-client limits. The defaults in `security` allow five failed attempts in a
300-second window and track at most 4,096 client entries per throttle. The
threshold attempt returns `429` with `Retry-After`; accepted valid credentials reset
that client's relevant counter. Throttles use the direct socket peer address,
not forwarded headers. Behind a reverse proxy, clients sharing its peer address
also share a bucket. Counters reset when the process restarts and are per
process, so keep the supported single-process deployment model when relying on
them.

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

Token counting executes locally: the base install uses `tiktoken` and calibrated
heuristics. Hugging Face `transformers` and `sentencepiece` are an optional
install from `requirements-tokenizers.txt`; without them, unsupported families
safely use heuristics. When enabled, background preload may download tokenizer
assets from Hugging Face unless they are already cached or offline mode is
configured. The request path does not call a remote token-count API. Preload is
best effort and may be cancelled during shutdown. Inspect
`token_estimation_review` in `/admin/health` when investigating context failures
or estimate drift.

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
