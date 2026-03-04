# Operations Runbook

This document is the operator-focused companion to [FREELUNCH_SPEC_v8.md](C:/Users/19Jes/dev/FreeLunch/FREELUNCH_SPEC_v8.md). It explains how to run, validate, observe, and maintain a FreeLunch deployment using the repository as it exists today.

## 1. Operational Model

FreeLunch is a single-node service with:

- one FastAPI application process
- one SQLite database
- one dedicated SQLite writer thread
- one queued runtime logging listener
- one APScheduler instance
- one production provider integration: OpenRouter

This is an intentionally simple operational model. FreeLunch is not designed around horizontal scaling, distributed state, or multiple provider integrations at once.

## 2. Deployment Modes

### 2.1 Recommended

Use Docker or the provided installer scripts.

Recommended artifacts:

- `docker-compose.yml`
- `install.sh`
- `install.ps1`

### 2.2 Development

Run the app directly for development and testing when Docker is not necessary.

Important development distinction:

- the OpenRouter no-key stub is only available when `APP_ENV=dev` and `providers.openrouter.dev_stub_enabled=true`
- real deployments should leave the stub disabled

## 3. Minimal Production Checklist

Before considering a deployment healthy:

1. Set a real `OPENROUTER_API_KEY`.
2. Keep `providers.openrouter.dev_stub_enabled: false`.
3. Confirm `/healthz` returns `200`.
4. Confirm `/readyz` returns `200`.
5. Confirm `/v1/models` returns at least one active model.
6. Confirm `/admin/health` shows a routable provider summary.
7. Confirm the runtime logger is enabled at the desired verbosity.
8. Confirm request-log retention is set appropriately for the deployment.

## 4. Startup Expectations

At startup, FreeLunch should:

1. initialize SQLite and apply migrations
2. start the DB writer thread
3. apply config overrides
4. configure provider runtime state
5. run discovery
6. recompute ranking
7. perform bootstrap health work
8. compute readiness from actual routable rows
9. register recurring jobs

Expected concise runtime log sequence:

- `app.starting`
- potentially `provider.runtime_disabled`
- `readiness.changed` when models become routable
- `app.started`

Expected shutdown sequence:

- `app.stopping`
- `app.stopped`

## 5. Health Endpoints

### 5.1 `/healthz`

Use for liveness only.

It answers: is the process up?

### 5.2 `/readyz`

Use for readiness / routing capability.

It answers: is the gateway currently able to route to at least one active, healthy model?

Common causes of `503` readiness:

- no provider credentials
- provider disabled for inference
- discovery returned no eligible models
- all models inactive or unhealthy

## 6. Admin Endpoints

### 6.1 `/admin/models`

Use to inspect the normalized routing pool.

Look for:

- `is_active`
- `is_healthy`
- `composite_score`
- capability flags
- benchmark enrichment

### 6.2 `/admin/health`

This is the most important operational endpoint.

Key sections:

- `bootstrap`
  - startup and readiness state
- `db`
  - writer queue depth and related DB status
- `models`
  - model summary and provider state
- `scheduler`
  - recurring job status, counts, last success/failure
- `runtime_logging`
  - runtime logger enablement, verbosity, queue depth, dropped records
- `probe_budgets`
  - per-provider budget usage
- `probe_state`
  - health probe policy and likely next candidates
- `recent_probe_activity`
  - recent probe/bootstrap request telemetry
- `token_estimation_review`
  - evidence-driven summary for token-estimation accuracy

### 6.3 `/admin/config`

Use to inspect effective config and DB-backed overrides.

### 6.4 `/admin/logs`

Use for durable request telemetry review.

This endpoint reflects `request_log`, not the process runtime logger.

## 7. Logging Model

FreeLunch has two distinct logging systems.

### 7.1 Runtime Logs

Runtime logs are:

- ephemeral
- JSON-line process output
- emitted from a queue-backed listener thread
- filtered by verbosity

Verbosity levels:

- `concise`
  - major lifecycle and operator-relevant events
- `verbose`
  - richer operational detail
- `debug`
  - very chatty; includes tokenizer, scheduler, routing, and health internals

Runtime logs are best for:

- startup/shutdown traces
- scheduler activity
- benchmark refresh events
- routing/failover traces
- tokenizer-resolution behavior
- health-probe traces

### 7.2 Durable Request Telemetry

`request_log` is persisted in SQLite.

It is best for:

- request outcome review
- latency/TTFB analysis
- probe accounting
- health and ranking input
- token-estimation evidence
- admin log inspection

### 7.3 Important Distinction

Do not confuse a quiet `/admin/logs` feed with a quiet runtime.

- `/admin/logs` shows persisted request telemetry
- runtime logger output shows process events

## 8. Runtime Logging Guidance

Recommended verbosity by environment:

- local development: `verbose` or `debug`
- integration / staging: `verbose`
- production: `concise`

Use `debug` only when actively investigating:

- scheduler behavior
- tokenizer resolution
- failover edge cases
- probe selection and cooldown behavior

If runtime logger queue depth grows or dropped records rise unexpectedly:

1. reduce verbosity
2. inspect event volume
3. confirm the deployment log sink is not blocked

## 9. Provider Credentials And Dev Stub

### 9.1 Real Deployments

Use a real `OPENROUTER_API_KEY`.

Production guidance:

- `APP_ENV=prod`
- `providers.openrouter.dev_stub_enabled=false`

### 9.2 Development Stub

The no-key OpenRouter stub exists only for development convenience.

It is:

- explicit
- disabled by default
- ignored outside `APP_ENV=dev`

It should not be used to validate production readiness behavior.

## 10. Discovery And Benchmark Operations

### 10.1 Discovery Behavior

Each discovery run:

- optionally refreshes benchmark cache
- calls provider discovery
- upserts normalized models
- deactivates provider rows that disappeared from the latest response

### 10.2 Benchmark Enrichment

Benchmark enrichment is best-effort.

Current hardening includes:

- freshness windows per source
- backward walking across parseable Chatbot Arena artifacts
- Open LLM row-page limit compatibility

### 10.3 Operator Expectations

Benchmark refresh failure should not make the gateway unusable.

It should degrade to:

- missing benchmark enrichment
- stale cache usage
- warning-level operational visibility

### 10.4 Maintenance Risk

The largest ongoing maintenance risk is upstream schema drift in public benchmark artifacts.

When that happens:

1. inspect runtime logs around benchmark refresh
2. reproduce via targeted tests in `tests/test_benchmarks.py`
3. update parsing logic in `src/benchmarks.py`
4. refresh docs if behavior changes materially

## 11. Routing And Failover Behavior

### 11.1 Candidate Filtering

Routing only considers candidates that satisfy:

- active state
- healthy state
- cooldown eligibility
- capability compatibility
- context-window fit
- output-token fit

### 11.2 Failover

Failover is bounded.

Expected retryable categories:

- `RATE_LIMITED`
- `PROVIDER_UNAVAILABLE`
- `CONTEXT_EXCEEDED`

Important nuance:

- `CONTEXT_EXCEEDED` remains retryable inside bounded failover, but if the request exhausts all candidates due to context, the gateway returns `400` rather than a generic `502`
- context-exceeded outcomes do not count as health penalties

### 11.3 Streaming Semantics

Streaming behavior should:

- preserve provider frames
- suppress keepalive/comment frames
- record first-byte timing
- allow pre-first-byte failover
- not attempt mid-stream failover after partial output has been emitted

## 12. Token Estimation Operations

### 12.1 Current Policy

The token-estimation pipeline is considered complete under the accepted local-only policy.

This means:

- use exact local tokenizers when safely available
- otherwise use calibrated heuristics
- do not call remote token-count APIs
- do not auto-enable new families based on telemetry

### 12.2 Exact Local Paths

Exact counts currently use:

- `tiktoken` for OpenAI-compatible families
- Hugging Face `AutoTokenizer` for safely resolvable non-OAI families

### 12.3 Heuristic Tail

Closed or unresolved families still use calibrated heuristics.

This is intentional.

### 12.4 Token Review Endpoint

Use `/admin/health.token_estimation_review` to decide whether heuristic drift is becoming operationally significant.

Review signals include:

- repeated `CONTEXT_EXCEEDED`
- estimate-vs-usage mismatch
- failover recoveries caused by context-window differences

### 12.5 Background Preloads

Discovery can schedule tokenizer preloads in the background.

Important operational interpretation:

- preload failures do not necessarily imply routing failure
- preload cancellation during shutdown is expected
- the request path can still fall back heuristically while preload is pending

## 13. Database Operations

### 13.1 SQLite Expectations

FreeLunch uses SQLite with WAL and a configured busy timeout.

### 13.2 Writes

All normal application writes should pass through the DB writer thread.

### 13.3 Queue Saturation

If the write queue is pressured:

- low-priority client request logs may be dropped
- higher-priority metadata writes should continue to be protected by reserved queue capacity

### 13.4 Retention

Request-log retention is enforced by the `maintenance` job using `logging.request_log_retention_days`.

## 14. Scheduler Operations

Registered jobs:

- `discovery`
- `ranking`
- `health`
- `maintenance`
- `config_refresh`

Operational checks:

1. verify the scheduler reports all expected jobs in `/admin/health`
2. inspect `run_count`, `last_started_at`, `last_success_at`, and failures
3. confirm interval overrides reschedule correctly after config changes

## 15. Live Validation Strategy

When a low-cost OpenRouter key is available, use the smallest possible live checks.

Recommended order:

1. authenticated discovery only
2. one tiny non-streaming completion against a free model
3. one tiny streaming completion against a free model
4. one app-level `/readyz` and `/v1/chat/completions` check

Guidelines:

- prefer free models only
- prefer `max_tokens=1`
- do not run broad matrices unless there is a concrete reason

## 16. Incident Patterns

### 16.1 `/readyz` returns `503`

Likely causes:

- missing OpenRouter key
- inference disabled in config
- no eligible models discovered
- all models deactivated or unhealthy

Primary checks:

- `/admin/health`
- `/admin/models`
- runtime logs around startup and discovery

### 16.2 Discovery succeeds but ranking looks poor

Check:

- benchmark cache freshness
- benchmark enrichment presence on model rows
- health penalties
- cooldown state
- ranking weights

### 16.3 Token-estimation review flags appear

Check:

- which tokenizer families are flagged
- whether flagged families are exact or heuristic
- request-time context-window evidence
- real provider `prompt_tokens`

### 16.4 Runtime logs show tokenizer preload issues

Interpretation:

- `preload_cancelled`: expected during shutdown
- `preload_failed`: real background preload failure
- `load_failed`: candidate repo-resolution failure during lookup attempts

Use `debug` runtime logging if deeper tokenizer diagnostics are needed.

## 17. Repository Maintenance

When behavior changes materially, update:

- `FREELUNCH_SPEC_v8.md`
- `SPEC_GAP_REVIEW.md`
- `TASKS.md`
- `AGENTS.md`
- any operator-facing docs affected by the change

## 18. Recommended Validation Commands

Use:

```bash
python -m ruff check .
python -m mypy src
python -m pytest tests -q --basetemp .pytest_tmp_local -p no:cacheprovider
python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_cov -p no:cacheprovider
```

For live-provider smoke tests, keep commands focused and budget-aware.
