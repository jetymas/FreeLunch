# FreeLunch Agent Guide

This file gives repo-specific guidance to coding agents working in `FreeLunch`.

## Start Here

Read these files first, in this order:

1. `FREELUNCH_SPEC_v8.md`
2. `SPEC_GAP_REVIEW.md`
3. `TASKS.md`
4. `README.md`
5. `CONTRIBUTING.md`

The spec is the target. The gap review is the current alignment snapshot. The task list is the actionable backlog.

## Repo Priorities

- Preserve the provider boundary: provider-specific logic belongs in `src/providers/*`, not in routing, health, or proxy orchestration.
- Prefer simple, low-overhead designs. This project explicitly values single-node reliability over clever concurrency.
- Treat SQLite carefully. All writes go through the writer thread in `src/db.py`.
- Keep timestamps canonical: UTC ISO 8601 with a `Z` suffix.
- If behavior diverges from the spec, update `SPEC_GAP_REVIEW.md` and `TASKS.md` in the same change.

## Current Code Map

- `src/main.py`: app lifespan, startup bootstrap, scheduler wiring
- `src/benchmarks.py`: external leaderboard fetch/refresh helpers plus shared benchmark name normalization
- `src/db.py`: schema, migrations, writer thread, DB helpers
- `src/discover.py`: discovery orchestration and model upserts
- `src/ranking.py`: composite score calculation
- `src/health.py`: passive health, probes, cooldowns
- `src/routing.py`: candidate selection
- `src/tokens.py`: lightweight request token estimation and multimodal content inspection
- `src/proxy.py`: HTTP endpoints, auth, request handling, failover, streaming
- `src/providers/base.py`: provider contracts and normalized error types
- `src/providers/openrouter.py`: current provider implementation
- `src/providers/registry.py`: provider registration

## Validation Commands

Run the smallest command set that proves your change:

```bash
python -m ruff check .
python -m mypy src
python -m pytest tests -q --basetemp .pytest_tmp_local -p no:cacheprovider
python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_cov -p no:cacheprovider
```

Notes:

- Repo-wide Ruff is configured to ignore vendored local dependency folders such as `.pydeps`.
- Coverage is informative today; the repo does not yet enforce the intended spec target automatically.

## Best Practices For Changes

- Add or update tests for every behavior change, especially around routing, health, discovery, and streaming.
- Keep API and admin behavior backward-compatible unless the task explicitly calls for a breaking change.
- When touching startup/bootstrap code, verify both `/healthz` and `/readyz` behavior.
- When touching provider error handling, test retryable vs non-retryable paths and streaming vs non-streaming behavior separately.
- When touching config, update `config.yaml.example`, `README.md`, and any admin/config tests together.
- When touching schema or persistence behavior, add a migration-safe test in `tests/test_db.py`.
- Discovery currently deactivates provider models that disappear from later discovery runs; preserve that reconciliation behavior when modifying `src/discover.py`.
- Discovery also applies cached benchmark data from `leaderboard_cache` using normalized model-name matching; preserve that join behavior when modifying discovery or cache code.
- Discovery now performs best-effort external benchmark refresh before provider upserts; benchmark fetch failures should degrade to missing enrichment, not failed discovery.
- Request requirement parsing now lives partly in `src/tokens.py`; keep vision detection and token estimation there instead of growing ad hoc parsing logic inside `src/proxy.py`.
- Routing now also re-checks context fit against each candidate's `tokenizer_family`; if you change request sizing, keep `src/tokens.py`, `src/proxy.py`, and `src/routing.py` aligned.
- Discovery, ranking, and health scheduler intervals are runtime-configurable; if you change reload behavior, verify all three jobs are rescheduled consistently.
- Log retention is enforced by the `maintenance` scheduler job using `logging.request_log_retention_days`; preserve that path when modifying request logging or scheduler registration.
- `/admin/health` now includes `probe_budgets`; keep that report aligned with `get_provider_probe_usage()` and the configured daily budgets.
- Runtime overrides are also refreshed by the scheduled `config_refresh` job, not just admin config endpoints.
- `gateway.*` and `database.busy_timeout_ms` are now typed in `Settings`; if you change SQLite connection setup, keep `src/config.py`, `src/db.py`, and `tests/test_config.py` aligned.
- `mark_success()` now maintains rolling latency/TTFB metrics with `ROLLING_METRIC_ALPHA` in `src/health.py`; preserve smoothing behavior unless you intentionally redesign ranking inputs.
- The no-key OpenRouter stub now returns the same fallback identity as `ranking.fallback_model` (`openrouter/openrouter/free`); keep config and stub discovery aligned if either changes.
- Benchmark-name normalization now lives in `src/benchmarks.py`; reuse it for cache refresh and discovery joins instead of reintroducing duplicate normalization logic.

## Common Pitfalls

- Do not add provider-specific conditionals to `src/routing.py`, `src/health.py`, or `src/proxy.py`.
- Do not bypass the DB writer thread for application writes.
- Do not assume the spec gap document is current; verify against code before editing it.
- Do not lint or typecheck vendored dependency trees as if they were project code.
- Do not let docs drift after spec-facing changes; update the gap review and task list when needed.

## Useful Resources

- Spec target: `FREELUNCH_SPEC_v8.md`
- Current alignment snapshot: `SPEC_GAP_REVIEW.md`
- Active backlog: `TASKS.md`
- Dev workflow: `CONTRIBUTING.md`
- Runtime defaults: `config.yaml.example`, `.env.example`
- Behavior examples: `tests/test_api.py`, `tests/test_app.py`, `tests/test_health.py`, `tests/test_routing.py`
- Provider-boundary examples: `tests/test_openrouter.py`
