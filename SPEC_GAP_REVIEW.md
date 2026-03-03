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

### 3) Proxy/API parity gaps (high priority)

- `/admin/models/{id}`, enable/disable model endpoints, and `/admin/logs` are still missing.
- `/admin/refresh` currently queues intent; it should execute full refresh orchestration semantics as specified.
- Auth and readiness behavior exist but need broader compliance validation against all spec edge cases.

### 4) Health/ranking strategy sophistication (high priority)

- Health remains mostly simple and does not yet implement full passive-first + adaptive probe policy.
- Ranking formula is still basic and does not fully combine benchmark cache + request telemetry weighting as specified.
- Probe budgets/cooldown policy tuning and observability need deeper implementation.

### 5) Config and override model completeness (medium priority)

- Env + basic `config.yaml` loading works, but full typed config surface remains incomplete.
- Runtime override precedence exists only partially and is not fully admin-wired.

### 6) Test/CI and repository standards (high priority)

- Basic pytest coverage is green, but spec-level module and behavior coverage remains incomplete.
- CI matrix currently lacks complete lint/typecheck/release checks described in the spec.
- Required project docs/workflows (e.g., `CONTRIBUTING.md`, `CHANGELOG.md`, release workflow details) still need completion.

## Suggested implementation order (updated)

1. **Close admin API parity**: model detail/enable/disable/log endpoints + refresh orchestration.
2. **Deepen health + ranking**: passive telemetry weighting, adaptive probes, cooldown policy.
3. **Finalize schema alignment**: fill remaining `models`/`request_log`/cache fields + indexes.
4. **Harden provider boundary**: richer error mapping and streaming behavior correctness.
5. **Complete config precedence**: full `config.yaml` surface and runtime override wiring.
6. **Raise quality bar**: broaden tests, lint/typecheck/release workflows, and docs.

## Pending changes note

This document has been refreshed to reflect implemented work from the recent admin-health observability PR and to re-baseline remaining gaps.
