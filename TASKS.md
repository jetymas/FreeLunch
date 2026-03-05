# FreeLunch Task List

This file is the actionable backlog derived from the current codebase review.

Use it together with:
- `FREELUNCH_SPEC_v8.md` for the authoritative target behavior
- `SPEC_GAP_REVIEW.md` for the current spec-alignment snapshot
- `AGENTS.md` for repo-specific execution guidance

## P0 Testing Excellence (97% Quality Target)

- [x] Adopt `TESTING.md` as canonical testing guide and keep it synchronized with CI and contributor workflows.
  Files: `TESTING.md`, `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `OPERATIONS.md`
- [x] Raise total `src/` coverage to **97%+** while preserving signal quality (not line-only inflation).
  Files: `tests/*`, `src/*`, `.github/workflows/ci.yml`, `pyproject.toml`
- [x] Close Wave A high-impact runtime failover/auth edge branches in proxy/routing (stream pre-first-byte failure, midstream unexpected exception containment, invalid bearer rejection, routing preference/alias/fallback branch depth).
  Files: `tests/test_api.py`, `tests/test_app.py`, `tests/test_routing.py`, `tests/test_health.py`
- [x] Expand Wave A provider contract depth (invalid JSON normalization, retry transport-cause preservation, stream EOF/keepalive parsing, registry import/sanitization edges).
  Files: `tests/test_openai_compatible.py`, `tests/test_openrouter.py`, `tests/test_app.py`, `tests/test_api.py`
- [x] Expand Wave B benchmark/scheduler/token branch depth and fixture realism (including deterministic brittle-upstream fixtures).
  Files: `tests/test_benchmarks.py`, `tests/test_tokens.py`, `tests/test_db.py`, `tests/fixtures/benchmarks/*`
- [x] Add hard-test modes: property-based invariant tests and concurrency/fault-injection stress tests for routing/probes/tokenizer-preload/scheduler behavior.
  Files: new `tests/test_property_*.py` / `tests/test_stress_*.py` suites, test dependencies/config
- [x] Harden outside-repo validation: real-daemon Linux installer smoke, auth-on smoke, compose runtime smoke, expanded Docker smoke (`/readyz`, `/v1/models`, tiny chat non-stream + stream).
  Files: `.github/workflows/ci.yml`, `OPERATIONS.md`
- [x] Define a manual release validation matrix (native Windows/macOS installer paths + restricted-egress + low-budget live provider checks).
  Files: `RELEASE_VALIDATION_MATRIX.md`, `OPERATIONS.md`, `TESTING.md`
- [x] Execute the manual release validation matrix and archive evidence with pass/fail notes.
  Files: `RELEASE_VALIDATION_MATRIX.md`, `RELEASE_VALIDATION_EVIDENCE.md`
  Note: M1/M2/M4/M5/M6/M7/M8 are executed and recorded; M3 (macOS host path) is accepted as blocked/waived by project owner due host unavailability.

Wave A verification snapshot (latest run):
- `python -m pytest tests -q --basetemp .pytest_tmp_waveA_full -p no:cacheprovider` -> `275 passed`
- `python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_waveA_cov2 -p no:cacheprovider` -> `275 passed`, total coverage `90.37%`

Wave B verification snapshot (latest run):
- `python -m pytest tests -q --basetemp .pytest_tmp_waveB_full -p no:cacheprovider` -> `320 passed`
- `python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_waveB_cov -p no:cacheprovider` -> `320 passed`, total coverage `93.05%`

Wave C verification snapshot (latest run):
- `python -m ruff check src tests` -> all checks passed
- `python -m mypy src` -> success, no issues
- `python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_waveC_cov -p no:cacheprovider` -> `380 passed`, total coverage `97.61%`

Wave D verification snapshot (latest run):
- `python -m pytest tests/test_property_routing.py tests/test_property_tokens.py tests/test_stress_concurrency.py -q --basetemp .pytest_tmp_waveD_hard -p no:cacheprovider` -> `36 passed`
- `python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_waveD_cov -p no:cacheprovider` -> `418 passed`, total coverage `97.68%`
- `.github/workflows/ci.yml` docker smoke now validates `/healthz`, `/readyz`, auth-on `/v1/models` (`401/200`), and tiny authenticated non-stream + stream chat in dev-stub mode.
- `.github/workflows/ci.yml` installer-runtime-smoke now validates real-daemon `install.sh` + `docker compose` runtime path and uninstall cleanup on Linux using local image + `FREELUNCH_SKIP_PULL=true`.

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

## P2 Pre-Public Validation (March 2026)

- [x] Run final multi-agent pre-public validation wave across runtime tests, static checks, installer behavior, docs consistency, and secret hygiene.
  Files: `src/*`, `tests/*`, installer scripts, docs
- [x] Confirm quality gates on current branch (`ruff`, `mypy`, focused runtime pytest sweep, full-suite pytest, coverage report).
  Files: repository-wide validation commands
- [x] Add `.env` ignore guardrails and key-handling docs notes.
  Files: `.gitignore`, `.env.example`, `README.md`
- [x] Normalize admin-health field-path wording and runtime-config guidance across docs.
  Files: `README.md`, `OPERATIONS.md`, `IMPLEMENTATION_GUIDE.md`, `CONTRIBUTING.md`
- [x] Ensure pytest collection ignores transient local `tests/tmp_*` artifacts.
  Files: `pyproject.toml`
