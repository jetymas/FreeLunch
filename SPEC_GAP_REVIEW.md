# FreeLunch Spec Gap Review (against `FREELUNCH_SPEC_v8.md`)

## Overall status

The current codebase is an **early scaffold / MVP stub** and does not yet implement most of the production behaviors required by the spec.

## What is already in place

- FastAPI app bootstrap with lifespan startup/shutdown wiring.
- SQLite initialization plus a dedicated writer thread abstraction.
- Basic provider registry with an OpenRouter adapter placeholder.
- Core endpoints present: `/healthz`, `/readyz`, `/v1/models`, `/v1/chat/completions`.
- Minimal discovery/ranking/health pass flow invoked at startup.

## Major gaps still to implement

### 1) Data model mismatch vs spec (high priority)

The database schema is significantly smaller than the spec’s required schema.

- `models` table currently lacks many required identity/capability/performance/cooldown/composite-score fields.
- `request_log` lacks required telemetry fields (attempt index, fallback flags, token usage, request source, etc.).
- `leaderboard_cache` and `config_overrides` structures are simplified and don’t match required keys.
- Required indexes and canonical timestamp conventions are not fully implemented.

### 2) Provider abstraction is incomplete (high priority)

- `src/providers/base.py` only defines a minimal `Protocol`; typed result/error model required by spec is missing.
- OpenRouter adapter is a stub (hardcoded model discovery + echo response), not real OpenRouter API integration.
- Provider error normalization and retryability classification are not implemented.

### 3) Routing/failover behavior not implemented to spec (high priority)

- Routing currently picks one model only; bounded multi-candidate failover is missing.
- Capability filtering is minimal (`tools` only) and missing full required constraints (vision, streaming, structured output, context window, output limits, cooldown).
- No dynamic request-time preference overrides from config/admin paths.

### 4) Proxy/API behavior is incomplete (high priority)

- `/v1/chat/completions` does not implement streaming behavior.
- Readiness failure handling does not return spec-required `Retry-After` semantics.
- Gateway auth handling is not enforced.
- Admin API endpoints described by spec are missing (model inspection, refresh triggers, health/status detail endpoints).

### 5) Background jobs and health strategy are mostly unimplemented (high priority)

- Scheduler is created but no periodic jobs are registered.
- Health logic is a startup-only optimistic mark-healthy pass; passive-first telemetry and adaptive probes are missing.
- Ranking is simplistic score adjustment and does not use benchmark cache + request telemetry weighting from spec.

### 6) Config system is underspecified (medium priority)

- Env loading exists, but full typed `config.yaml` support and override precedence model are missing.
- Runtime override table integration (`config_overrides`) is not wired.

### 7) Test suite and quality gates are below spec expectations (high priority)

- Current tests include import/API contract mismatches and fail at collection.
- Spec-required module-level coverage (db/discover/ranking/routing/health/proxy/scheduler/providers) is not present.
- CI currently runs only basic pytest; lint/typecheck matrix and container checks are incomplete.

### 8) Repository/infrastructure docs and workflows are incomplete (medium priority)

- Missing required files from spec: `CONTRIBUTING.md`, `CHANGELOG.md`, release workflow.
- Docker and compose definitions are minimal and differ from the spec’s production/deployment guidance.
- `pyproject.toml` is partial vs required project metadata + lint configuration.

## Suggested implementation order

1. **Stabilize contracts first**: finalize provider base types/errors + schema migrations matching spec.
2. **Implement real OpenRouter adapter**: discovery, inference, error mapping, token extraction.
3. **Build routing candidate pipeline**: capability filters, cooldown checks, failover attempts.
4. **Finish proxy compatibility**: streaming + consistent OpenAI-style responses + readiness/auth semantics.
5. **Add scheduler jobs**: discovery/ranking/health/admin refresh tasks.
6. **Backfill observability**: request log completeness, admin endpoints, leaderboard cache ingestion.
7. **Raise quality bar**: rewrite tests to match implemented contracts and add lint/typecheck/release workflows.

## Immediate blocker to address first

`pytest` currently fails during collection because `tests/test_api.py` imports `ChatResult` and `ProviderRetryableError` that do not exist in `src/providers/base.py`. This indicates the tests and runtime contracts have diverged and should be reconciled before further feature work.
