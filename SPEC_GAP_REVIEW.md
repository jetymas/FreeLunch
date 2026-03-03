# FreeLunch Spec Gap Review (against `FREELUNCH_SPEC_v8.md`)

## Overall status

The codebase is now an **MVP-plus scaffold**: core request flow works end-to-end, but several spec-level production requirements are still pending.

## What is already in place

- FastAPI app bootstrap with lifespan startup/shutdown wiring.
- SQLite initialization plus a dedicated writer thread abstraction.
- Provider registry with an OpenRouter adapter that supports discovery + chat completions.
- Core endpoints implemented: `/healthz`, `/readyz`, `/v1/models`, `/v1/chat/completions`.
- Bounded multi-candidate routing with capability filters and failover attempts.
- Startup discovery/ranking/health bootstrap and recurring scheduler jobs.
- Admin endpoints present: `/admin/models`, `/admin/health`, `/admin/refresh`.
- `/admin/health` now includes richer diagnostics (bootstrap state, queue depth, model/provider stats, scheduler job status, recent errors).

## Major gaps still to implement

### 1) Data model still partial vs full spec (high priority)

- `models` still lacks some spec fields and full normalization requirements.
- `request_log` includes key telemetry but still misses parts of the complete schema/semantics.
- `leaderboard_cache` and `config_overrides` remain simplified vs spec reference.
- Additional indexes and stricter timestamp conventions should be aligned to spec wording.

### 2) Provider abstraction depth is still limited (high priority)

- Only OpenRouter is currently integrated; the boundary exists but extensibility is still lightly tested.
- Error normalization exists but should be expanded to fully match spec categories and retry semantics.
- Streaming pass-through behavior should be upgraded from minimal relay shape to provider-accurate chunk handling.

### 3) Proxy/API parity gaps (partially closed, medium priority)

- ✅ Added `/admin/models/{id}`, `/admin/models/{id}/disable`, `/admin/models/{id}/enable`, and `/admin/logs`.
- ✅ `/admin/refresh` now executes under an app-level async lock to prevent overlapping manual refresh runs and performs immediate orchestration.
- Remaining: deeper auth/readiness edge-case parity validation and full spec-aligned error/status mapping under all provider failure classes.

### 4) Health/ranking strategy sophistication (high priority)

- ✅ Failure handling now applies adaptive exponential cooldown windows via `backoff_level`/`cooldown_until`, and success resets backoff state.
- Remaining: full passive-first + adaptive active-probe policy still needs deeper implementation.
- ✅ Ranking now blends benchmark signals with request telemetry (success rate, observed latency, and sample-size confidence) when recomputing `composite_score`.
- Remaining: probe budgets/cooldown policy tuning and observability still need deeper implementation.

### 5) Config and override model completeness (medium priority)

- Env + basic `config.yaml` loading works, but full typed config surface remains incomplete.
- Runtime override precedence exists only partially and is not fully admin-wired.

### 6) Test/CI and repository standards (high priority)

- Basic pytest coverage is green, but spec-level module and behavior coverage remains incomplete.
- CI matrix currently lacks complete lint/typecheck/release checks described in the spec.
- Required project docs/workflows (e.g., `CONTRIBUTING.md`, `CHANGELOG.md`, release workflow details) still need completion.

## Suggested implementation order (updated)

1. **Deepen health + ranking**: passive telemetry weighting, adaptive probes, cooldown policy.
2. **Finalize schema alignment**: fill remaining `models`/`request_log`/cache fields + indexes.
3. **Harden provider boundary**: richer error mapping and streaming behavior correctness.
4. **Complete config precedence**: full `config.yaml` surface and runtime override wiring.
5. **Raise quality bar**: broaden tests, lint/typecheck/release workflows, and docs.

## Pending changes note

This document has been refreshed to reflect implemented work from the recent admin-health observability PR and to re-baseline remaining gaps.


## Blockers

- `TASKS.md` is not present in the repository at this time, so explicit task-by-task completion tracking against that file is currently blocked.
