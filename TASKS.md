# FreeLunch Task List

This file is the actionable backlog derived from the current codebase review.

Use it together with:
- `FREELUNCH_SPEC_v8.md` for the authoritative target behavior
- `SPEC_GAP_REVIEW.md` for the current spec-alignment snapshot
- `AGENTS.md` for repo-specific execution guidance

## P0 Provider Platformization

- [x] Introduce provider-agnostic adapter/registry contract hooks (`runtime_state`, generic error categorization, generic registration API) to decouple core orchestration from OpenRouter-specific primitives.
  Files: `src/providers/base.py`, `src/providers/registry.py`, `src/providers/openrouter.py`, `tests/test_openrouter.py`, `tests/test_app.py`
- [x] Complete provider factory/bootstrap wiring so providers can be registered from provider descriptors without adding provider-specific code in `src/main.py`.
  Files: `src/main.py`, `src/providers/registry.py`, provider factory modules, `tests/test_app.py`
- [x] Move stream error categorization behind provider abstractions so `src/proxy.py` no longer imports OpenRouter-specific categorization.
  Files: `src/providers/base.py`, `src/providers/openrouter.py`, `src/proxy.py`, `tests/test_api.py`, `tests/test_openrouter.py`
- [x] Generalize provider runtime enablement, discovery enablement, inference enablement, and active probe gating in a provider-agnostic way.
  Files: `src/config.py`, `src/main.py`, `src/health.py`, `config.yaml.example`, `tests/test_config.py`, `tests/test_app.py`, `tests/test_health.py`

## P1 Schema And Ranking Neutrality

- [x] Generalize `openrouter_rank` semantics to provider-neutral rank metadata while preserving backward-compatible migration behavior.
  Files: `src/db.py`, `src/discover.py`, `src/ranking.py`, `tests/test_db.py`, `tests/test_ranking.py`

## P1 Provider Additions (API-Key Only)

- [x] Add an OpenAI-compatible shared adapter base/factory for API-key providers.
  Files: `src/providers/*`, `tests/test_*provider*.py`
- [x] Add first-wave API-key providers through modules and config wiring only (OpenAI, Together, Groq, DeepSeek, xAI, Cerebras, Perplexity, Nvidia).
  Files: `src/providers/*`, `src/config.py`, `config.yaml.example`, `tests/test_app.py`, provider-specific tests
- [x] Add provider-contract coverage for discovery/chat/stream/probe/error-normalization invariants.
  Files: `tests/`

## P2 Reliability And Maintenance

- [x] Harden Open LLM benchmark ingestion for dynamic dataset-server row limits and score-column naming fallback.
  Files: `src/benchmarks.py`, `tests/test_benchmarks.py`
- [x] Keep benchmark-ingestion tests and parsing logic current as upstream Arena/Open LLM artifacts drift (current maintenance cycle complete; continue as recurring ops hygiene).
  Files: `src/benchmarks.py`, `tests/test_benchmarks.py`, `SPEC_GAP_REVIEW.md`, `OPERATIONS.md`
- [x] Expand multi-provider regression coverage around startup/readiness/routing with mixed provider enablement and runtime-key availability.
  Files: `tests/test_app.py`, `tests/test_api.py`, provider adapter suites
- [x] Add optional manual live-provider smoke harness (non-CI) for configured API-key providers.
  Files: `scripts/provider_smoke.py`, `tests/test_provider_smoke.py`, `OPERATIONS.md`, `README.md`
- [x] Reduce Python 3.14 async deprecation warning noise through dependency modernization plus narrowly scoped third-party warning filters.
  Files: `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`

## P2 Documentation

- [x] Keep `SPEC_GAP_REVIEW.md` current whenever a spec-facing feature lands.
- [x] Keep `OPERATIONS.md`, `README.md`, and `config.yaml.example` synchronized with runtime behavior when provider-platform/config/logging behavior changes.
- [x] Keep this task list pruned and execution-oriented.
