# FreeLunch Implementation Guide

This document is the technical implementation reference for FreeLunch.
Use this for internals, architecture, and subsystem behavior.

For day-1 usage and installation, use `README.md`.
For operations, use `OPERATIONS.md`.
For target behavior and scope policy, use `FREELUNCH_SPEC_v8.md`.

## 1. Scope And Intent

FreeLunch is a single-node OpenAI-compatible gateway with:

- provider discovery
- health-aware routing
- bounded failover
- SQLite-backed telemetry and config overrides
- scheduler-driven maintenance loops

Current implementation is OpenRouter-first with module-based onboarding for first-wave API-key OpenAI-compatible providers:

- openai
- together
- groq
- deepseek
- xai
- cerebras
- perplexity
- nvidia

## 2. Runtime Architecture

Core process model:

- one FastAPI process
- one SQLite DB
- one DB writer thread (authoritative write path)
- one runtime logging listener thread
- APScheduler for recurring jobs

Primary modules:

- `src/main.py`: app lifecycle, bootstrap, scheduler wiring, readiness transitions
- `src/proxy.py`: public/admin HTTP surface, request orchestration, failover, streaming relay
- `src/providers/registry.py`: provider registration, descriptor/factory bootstrap, runtime gating
- `src/providers/base.py`: adapter contract and normalized provider error model
- `src/discover.py`: provider model discovery/upsert/deactivation
- `src/ranking.py`: composite ranking score computation
- `src/health.py`: passive/active health, cooldowns, probe budget/policy summaries
- `src/tokens.py`: request sizing pipeline (exact + heuristic)
- `src/db.py`: schema, migrations, DB helpers, writer queue
- `src/runtime_logging.py`: queue-backed JSON runtime event emission
- `src/benchmarks.py`: external benchmark refresh/parsing and normalization

## 3. Provider Platformization

Provider bootstrap is module-driven:

- `ProviderRegistry.register_configured(settings)` loads known providers
- built-ins and module descriptors are resolved through `src.providers.<provider_id>`
- supported entry points in provider modules:
  - `PROVIDER_BOOTSTRAP_DESCRIPTOR`
  - `build_provider_adapter(context)`

Provider orchestration remains generic in core paths:

- routing/proxy/health do not contain provider-specific branching
- stream error categorization is callback-driven via provider adapters
- provider runtime capability controls discovery/inference gating

## 4. Data Model (SQLite)

Main tables:

- `models`: discovered routing pool state and ranking metadata
- `request_log`: durable request/probe telemetry
- `leaderboard_cache`: cached benchmark source snapshots
- `config_overrides`: runtime override key/value store

Important model-row semantics:

- `provider_rank` is the provider-neutral ranking source field
- legacy `openrouter_rank` compatibility is preserved through migration/backfill logic
- `is_active` and `is_healthy` jointly gate candidate eligibility

Write policy:

- application writes flow through DB writer queue
- low-priority request logs are intentionally lossy under saturation
- metadata writes are protected by reserved queue capacity/backpressure

## 5. Request Lifecycle

### 5.1 Non-streaming chat

1. Parse/validate request
2. Estimate prompt/token requirements
3. Select candidate set (capability + health + context-fit + score)
4. Call provider adapter with bounded attempts
5. On success:
   - update health success signals
   - persist request telemetry
6. On failure:
   - classify as retryable/fatal
   - optionally fail over
   - persist failure telemetry

### 5.2 Streaming chat

1. Same candidate selection and attempt bounds
2. Relay SSE bytes while suppressing keepalive noise
3. Handle pre-first-byte and transport errors with bounded failover
4. Parse provider-side stream errors using provider categorization contract
5. Track TTFB and completion/failure telemetry

## 6. Token Estimation Pipeline

Token sizing policy is local-only:

- OpenAI-compatible exact path via `tiktoken` when resolvable
- non-OpenAI exact path via Hugging Face fast tokenizer when resolvable and safe
- calibrated heuristic fallback by tokenizer family and content type (`prose`, `code`, `json`)

Additional coverage:

- multimodal/vision detection
- structured metadata counting (`tool_calls`, `function_call`, `audio`, `name`, `tool_call_id`, `refusal`)
- provider-model alias normalization for common HF naming mismatches

Observability:

- `request_log` stores sizing evidence fields
- `GET /admin/health` exposes a `token_estimation_review` object with threshold-based manual review flags

## 7. Discovery, Ranking, And Health Jobs

Recurring scheduler jobs:

- discovery
- ranking
- health
- maintenance
- config_refresh

Discovery behaviors:

- refresh benchmark cache best-effort
- upsert discovered provider models
- deactivate provider rows missing from latest provider inventory

Ranking behavior:

- weighted composite score
- blends benchmark, real usage, latency, availability, context, feature support

Health behavior:

- passive telemetry first
- conservative active probe selection/budgets
- cooldown with backoff
- rolling latency/TTFB smoothing

## 8. Benchmark Ingestion Hardening

Chatbot Arena path:

- attempt newest parseable `elo_results_*.pkl` first
- then `leaderboard_table_*.csv`
- then `arena_hard_auto_leaderboard_*.csv`

Open LLM path:

- dataset-server rows API
- dynamic row-length cap adaptation when server lowers allowed `length`
- offset progression based on returned row count (avoid skip windows)
- score/model column fallback parsing (`Average ⬆️`, `Average`, `average`; `fullname`/`eval_name`/`model`)

Failure posture:

- enrichment degradation, not outage

## 9. Runtime Logging And Telemetry

Two systems:

- runtime logs: ephemeral JSON events, queue-backed, verbosity-gated
- request telemetry: durable SQLite request/probe records

Runtime verbosity levels:

- `concise`
- `verbose`
- `debug`

Runtime logging is intentionally decoupled from request handling path.

## 10. Config And Runtime Overrides

Core config sources:

- `config.yaml`
- environment variables
- DB overrides (`config_overrides`)

Provider controls:

- `providers.enabled`
- `providers.<id>.enabled`
- `providers.<id>.discovery_enabled`
- `providers.<id>.inference_enabled`
- `providers.<id>.active_probe_enabled`

OpenRouter dev stub:

- explicit dev-only behavior
- disabled by default
- ignored outside `APP_ENV=dev`

## 11. Test Surface

High-value suites:

- `tests/test_app.py`: startup/bootstrap/readiness/runtime-gating integration
- `tests/test_api.py`: endpoint and failover/stream integration behavior
- `tests/test_openrouter.py`: direct adapter realism + error/stream edge behavior
- `tests/test_openai_compatible.py`: shared adapter contract behavior
- `tests/test_benchmarks.py`: external source parser/fetch resilience
- `tests/test_tokens.py`: request sizing accuracy and fallback behavior
- `tests/test_provider_smoke.py`: manual live-provider smoke harness behavior

Validation baseline:

```bash
python -m ruff check .
python -m mypy src
python -m pytest tests -q --basetemp .pytest_tmp_local -p no:cacheprovider
python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_cov -p no:cacheprovider
```

## 12. Manual Live-Provider Smoke Harness

Script:

- `scripts/provider_smoke.py`

Purpose:

- optional, non-CI live provider checks
- pass/skip/fail per provider
- JSON output support

Current smoke mode:

- discovery-based minimal check

## 13. Implementation Caveats

- benchmark sources are not strongly contracted and may drift
- provider API behaviors vary over time; keep adapter parsing defensive
- Python 3.14 async deprecation noise is currently upstream; pytest warnings are narrowly filtered for known third-party signatures
- if dependency versions change, re-verify warning behavior and filter necessity

## 14. Documentation Map

- `README.md`: product and user onboarding guide
- `IMPLEMENTATION_GUIDE.md`: technical implementation detail (this file)
- `OPERATIONS.md`: deployment and runtime operations
- `CONTRIBUTING.md`: developer workflow and quality gates
- `FREELUNCH_SPEC_v8.md`: target behavior and accepted policy boundaries
- `SPEC_GAP_REVIEW.md`: implementation-vs-spec alignment snapshot
- `TASKS.md`: actionable backlog
- `AGENTS.md`: repo-specific agent rules
