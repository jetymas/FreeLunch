# FreeLunch Agent Guide

This file gives repo-specific guidance to coding agents working in `FreeLunch`.

## Start Here

Read these files first, in this order:

1. `FREELUNCH_SPEC_v8.md`
2. `SPEC_GAP_REVIEW.md`
3. `TASKS.md`
4. `README.md`
5. `TESTING.md`
6. `RELEASE_VALIDATION_MATRIX.md`
7. `IMPLEMENTATION_GUIDE.md`
8. `OPERATIONS.md`
9. `CONTRIBUTING.md`

The spec is the target. The gap review is the current alignment snapshot. The task list is the actionable backlog.

## Repo Priorities

- Preserve the provider boundary: provider-specific logic belongs in `src/providers/*`, not in routing, health, or proxy orchestration.
- Prefer simple, low-overhead designs. This project explicitly values single-node reliability over clever concurrency.
- Treat SQLite carefully. All writes go through the writer thread in `src/db.py`.
- Keep timestamps canonical: UTC ISO 8601 with a `Z` suffix.
- If behavior diverges from the spec, update `SPEC_GAP_REVIEW.md` and `TASKS.md` in the same change.
- Treat `OPERATIONS.md` as the operator-facing source of truth for deployment, admin endpoints, logging interpretation, and live validation guidance.
- Active mission: harden and maintain the now-landed multi-provider platformization baseline.

## Current Code Map

- `src/main.py`: app lifespan, startup bootstrap, scheduler wiring
- `src/benchmarks.py`: external leaderboard fetch/refresh helpers plus shared benchmark name normalization
- `src/db.py`: schema, migrations, writer thread, DB helpers
- `src/discover.py`: discovery orchestration and model upserts
- `src/ranking.py`: composite score calculation
- `src/health.py`: passive health, probes, cooldowns
- `src/routing.py`: candidate selection
- `src/tokens.py`: request sizing with `tiktoken` for OpenAI-compatible families, Hugging Face `AutoTokenizer` exact counts for resolvable non-OpenAI families, plus heuristic fallback and multimodal content inspection
- `src/proxy.py`: HTTP endpoints, auth, request handling, failover, streaming
- `src/providers/base.py`: provider contracts and normalized error types
- `src/providers/openrouter.py`: current provider implementation
- `src/providers/openai_compatible.py`: shared OpenAI-compatible adapter behavior
- `src/providers/openai.py`: OpenAI adapter module bootstrap
- `src/providers/together.py`: Together adapter module bootstrap
- `src/providers/groq.py`: Groq adapter module bootstrap
- `src/providers/deepseek.py`: DeepSeek adapter module bootstrap
- `src/providers/xai.py`: xAI adapter module bootstrap
- `src/providers/cerebras.py`: Cerebras adapter module bootstrap
- `src/providers/perplexity.py`: Perplexity adapter module bootstrap
- `src/providers/nvidia.py`: Nvidia adapter module bootstrap
- `src/providers/registry.py`: provider registration
- `src/runtime_logging.py`: queue-backed JSON runtime logging with `concise` / `verbose` / `debug` gating

## Validation Commands

Run the smallest command set that proves your change:

```bash
python -m ruff check .
python -m mypy src
python -m pytest tests -q --basetemp .pytest_tmp_local -p no:cacheprovider
python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_cov -p no:cacheprovider
```

Agents must not push until the relevant local validation gate is green. For ordinary code changes, that means at minimum the commands above. For release-facing changes, installer changes, CI-sensitive scripting changes, startup/bootstrap changes, or broad cross-cutting edits, run the full gate before pushing:

```bash
python -m ruff check .
python -m mypy src
python -m pytest tests -q --basetemp .pytest_tmp_local -p no:cacheprovider
python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_cov -p no:cacheprovider
sh -n install.sh
sh -n uninstall.sh
pwsh -Command "[System.Management.Automation.Language.Parser]::ParseFile('install.ps1',[ref]$null,[ref]$null) | Out-Null"
pwsh -Command "[System.Management.Automation.Language.Parser]::ParseFile('uninstall.ps1',[ref]$null,[ref]$null) | Out-Null"
```

Release rule: push to `main`, wait for `main` CI to pass, and only then create/push the semver release tag.

Notes:

- Repo-wide Ruff is configured to ignore vendored local dependency folders such as `.pydeps`.
- Latest validated baseline is `418 passed` with `97.68%` total `src/` coverage; CI still enforces an 80% floor, so keep local/full-wave coverage checks in the workflow.
- Python 3.14 test runs intentionally apply narrowly scoped third-party warning filters in `pyproject.toml` for known upstream asyncio deprecations in `fastapi.routing` and `pytest_asyncio.plugin`; do not broaden those filters without justification.
- Pytest now sets `norecursedirs` for transient temp paths (`.pytest_tmp*`, `pytest_tmp*`, `pytest-cache-files-*`, `tests/tmp_*`) to avoid accidental local artifact collection failures.

## Multi-Agent Orchestration

Use parallel agents only when file ownership is clearly separable. The main integration risk in this repo is not conceptual overlap; it is multiple agents touching the same orchestration files and tracking docs at once.

### Recommended current split

Current phase: testing-depth hardening after hitting the 97% coverage target

Phase A: safe to run in parallel

- Agent `property-routing-tests`
  Ownership: `tests/test_property_routing.py`
  Current target: randomized routing invariants (bounded output, dedupe, fallback ordering, explicit-model precedence)
- Agent `property-token-tests`
  Ownership: `tests/test_property_tokens.py`
  Current target: token-estimation invariants (non-negative, monotonic with extra content, multimodal guardrails)
- Agent `stress-concurrency-tests`
  Ownership: `tests/test_stress_concurrency.py`
  Current target: probe budget and tokenizer-preload de-dup invariants under concurrent execution
- Agent `docs-coordinator`
  Ownership: `FREELUNCH_SPEC_v8.md`, `SPEC_GAP_REVIEW.md`, `TASKS.md`, `AGENTS.md`, relevant README/OPERATIONS sections
  Current target: keep testing roadmap/spec-gap/task state aligned with landed validation evidence

Phase B: run after Phase A lands or when those files are idle

- Agent `provider-live-smoke` (optional/manual)
  Ownership: manual/integration scripts and docs only
  Current target: gated non-CI smoke checks for provider modules with real API keys + outside-repo release matrix evidence

Do not run `stress-concurrency-tests` concurrently with other workers editing `tests/test_health.py`, `tests/test_tokens.py`, or scheduler-related suites.

### Ownership rules

- Assign one owner per file. If a task touches `src/config.py`, `src/main.py`, `src/db.py`, or `src/proxy.py`, treat it as high-conflict work.
- Treat `TASKS.md`, `SPEC_GAP_REVIEW.md`, `CHANGELOG.md`, and `AGENTS.md` as coordinator-owned files by default.
- Worker agents should report required doc updates, but the coordinating agent should usually apply the shared tracking-doc edits to avoid merge churn.
- If two tasks both need `README.md`, split by section only if the coordinator is explicitly managing the merge.

### Execution order

1. Spawn only agents whose ownership does not overlap.
2. Let each worker finish code and task-local tests first.
3. Integrate one worker at a time into shared docs and final validation.
4. Re-check `TASKS.md` after each merge before spawning the next wave; the backlog is now small enough that the optimal split changes quickly.

### Worker handoff format

Each worker should report:

- files changed
- tests run
- unresolved risks
- exact doc impacts
- whether `TASKS.md` / `SPEC_GAP_REVIEW.md` should change

If a worker hits a blocker, it should stop, document the blocker clearly, and release ownership of untouched files so another worker can proceed.

### Validation strategy in multi-agent runs

- Workers should run the smallest focused test set for their owned files.
- The coordinator should run repo-wide `ruff`, `mypy`, and the relevant combined pytest subset after merging worker changes.
- Run full-suite validation after every wave, not after every individual worker, unless the worker touched startup, routing, or persistence primitives.
- Run full-suite pytest and full-suite coverage commands serially (not in parallel) to avoid non-deterministic benchmark-test interference in shared local environments.

### Coordination pitfalls

- Do not let benchmark work and config-runtime work edit `src/config.py` concurrently.
- Do not let routing work and db-policy work edit `src/proxy.py` concurrently.
- Do not let multiple agents update the same tracking doc in parallel.
- Do not let installer/doc work silently redefine runtime behavior without handing off those changes to the coordinator for spec-gap review.
- Do not run more than one worker against `src/main.py`, `src/config.py`, `src/db.py`, or `src/providers/base.py` in the same wave.

## Best Practices For Changes

- Add or update tests for every behavior change, especially around routing, health, discovery, and streaming.
- Keep API and admin behavior backward-compatible unless the task explicitly calls for a breaking change.
- When touching startup/bootstrap code, verify both `/healthz` and `/readyz` behavior.
- When touching provider error handling, test retryable vs non-retryable paths and streaming vs non-streaming behavior separately.
- `tests/test_openrouter.py` now directly covers retry exhaustion, raw-body error fallback parsing, dev-stub chat/stream behavior, and stream transport failures; preserve that direct adapter coverage instead of relying only on indirect API tests.
- `tests/test_openai_compatible.py` and `tests/test_app.py` now cover cross-provider bootstrap/runtime gating and OpenAI-compatible adapter error-contract invariants; extend those suites rather than duplicating one-off provider tests.
- When touching config, update `config.yaml.example`, `README.md`, and any admin/config tests together.
- When touching schema or persistence behavior, add a migration-safe test in `tests/test_db.py`.
- Discovery currently deactivates provider models that disappear from later discovery runs; preserve that reconciliation behavior when modifying `src/discover.py`.
- Discovery also applies cached benchmark data from `leaderboard_cache` using normalized model-name matching; preserve that join behavior when modifying discovery or cache code.
- Discovery now performs best-effort external benchmark refresh before provider upserts; benchmark fetch failures should degrade to missing enrichment, not failed discovery.
- Benchmark refresh now respects per-source cache freshness and skips fetches when cached source data is still fresh; preserve that behavior if you touch `src/benchmarks.py`.
- Chatbot Arena refresh now attempts the newest parseable `elo_results_*.pkl` snapshot first, then newer-to-older `leaderboard_table_*.csv` files, then `arena_hard_auto_leaderboard_*.csv`.
- Open LLM refresh now needs to stay compatible with the current Hugging Face dataset-server row-page limit instead of assuming larger `rows` page sizes will be accepted.
- Open LLM refresh also adapts dynamically when dataset-server lowers the accepted `rows.length` value and advances offsets by actual returned row count; preserve that behavior to avoid skipped rows.
- App-heavy tests in `tests/conftest.py` and `tests/test_app.py` intentionally disable external leaderboard refresh and startup probes in their generated configs; preserve that isolation so CI runtime is not dominated by repeated network-bound startup work.
- Request requirement parsing now lives partly in `src/tokens.py`; keep vision detection and token estimation there instead of growing ad hoc parsing logic inside `src/proxy.py`.
- Routing now also re-checks context fit against each candidate's `tokenizer_family`; if you change request sizing, keep `src/tokens.py`, `src/proxy.py`, and `src/routing.py` aligned.
- OpenAI-compatible families now use `tiktoken` when a candidate exposes a compatible `provider_model_id` or tokenizer family such as `cl100k_base` / `o200k_base`; preserve that exact-count path and keep heuristic fallback limited to unsupported families.
- Non-OpenAI exact sizing now also tries Hugging Face `AutoTokenizer.from_pretrained(..., use_fast=True, trust_remote_code=False)` using the candidate `provider_model_id`; keep that lookup path safe, cacheable, and provider-model-driven instead of adding provider-specific conditionals elsewhere.
- The tokenizer resolver now also normalizes common provider-to-Hugging-Face naming differences, including Cohere Command-R aliases, DeepSeek / StepFun / Z.AI org aliases, Meta-Llama repo-name variants, NVIDIA Nemotron repo patterns, Mistral dated release suffixes, and mixed alphanumeric repo tokens such as `R1`, `32B`, and `A22B`; extend that mapping only with explicit tests.
- OpenAI-prefixed GPT model IDs are now normalized into `tiktoken`-compatible names and family fallbacks before dropping to heuristics; preserve that provider-prefix stripping when changing `src/tokens.py`.
- Successful Hugging Face tokenizer loads are cached in-process, but transient load failures are intentionally retried later instead of being cached as permanent misses; preserve that distinction when changing tokenizer loading behavior.
- Discovery now best-effort schedules Hugging Face tokenizer preloads in the background, and uncached request-path sizing is allowed to fall back heuristically while preload is pending; preserve that non-blocking behavior unless you intentionally redesign token sizing.
- Tokenizer preload scheduling now holds a single critical section for check+submit+future registration, so concurrent callers do not enqueue duplicate preloads; preserve that atomicity when editing `src/tokens.py`.
- Background tokenizer preloads can be cancelled during shutdown; treat those as expected debug-only events rather than warning-level failures, and keep real preload-failure logs descriptive enough to include the exception type.
- Request sizing now counts structured message metadata like `tool_calls`, `function_call`, `audio`, `name`, `tool_call_id`, and `refusal`; preserve that path when changing estimator behavior.
- Heuristic fallback sizing is now calibrated by tokenizer family and broad content type (`prose`, `code`, `json`); if you change fallback estimation, keep the classifier and profile tables aligned with the regression tests in `tests/test_tokens.py`.
- `request_log` now carries `selected_provider_model_id`, `selected_tokenizer_family`, `estimated_prompt_tokens`, and `selected_context_window`; keep those fields populated for tokenizer-review diagnostics when changing proxy logging.
- Discovery, ranking, and health scheduler intervals are runtime-configurable; if you change reload behavior, verify all three jobs are rescheduled consistently.
- Log retention is enforced by the `maintenance` scheduler job using `logging.request_log_retention_days`; preserve that path when modifying request logging or scheduler registration.
- `logging.request_log_enabled` and `logging.log_queue_size` now control low-priority client request logging without suppressing probe/bootstrap telemetry; preserve that distinction unless you intentionally redesign logging policy.
- Runtime logs are separate from SQLite request telemetry. Keep `src/runtime_logging.py`, the `runtime_logging` field in `GET /admin/health`, `README.md`, `CONTRIBUTING.md`, `config.yaml.example`, and the runtime logging tests aligned whenever `logging.runtime_*` behavior changes.
- Runtime logging uses a queue-backed listener thread and three verbosity levels (`concise`, `verbose`, `debug`); debug mode is expected to be very chatty and should retain detailed scheduler, routing, probe, and tokenizer-resolution events.
- The remaining tokenizer-family tail is now mostly closed-family models (Claude, Gemini, Grok, Nova, Router, plus unresolved `Other` entries) that expose official remote count APIs more readily than public local tokenizers; do not add request-path remote counter calls without an explicit design decision.
- Under the current repo policy, treat the local-only token-estimation pipeline as complete and do not add tokenizer prewarming by default. Future work in this area should be evidence-driven calibration or safe local mapping extensions, not remote token-count API integration.
- The DB writer queue is now bounded; low-priority client logs are still lossy, while reserved queue capacity plus blocking backpressure protect metadata writes. Preserve that priority split when changing `src/db.py`.
- `/admin/health` now includes `probe_budgets`, `probe_state`, `recent_probe_activity`, and `token_estimation_review`; keep those reports aligned with `get_provider_probe_usage()`, `get_probe_runtime_summary()`, `get_recent_probe_activity()`, `get_token_estimation_review_summary()`, and the probe candidate `reason` annotations from `_select_probe_candidates()`.
- Runtime overrides are also refreshed by the scheduled `config_refresh` job, not just admin config endpoints.
- `gateway.*` and `database.busy_timeout_ms` are now typed in `Settings`; if you change SQLite connection setup, keep `src/config.py`, `src/db.py`, and `tests/test_config.py` aligned.
- Provider gating is now implemented in `Settings`/`ProviderRegistry`; keep `providers.enabled`, provider `enabled`, `discovery_enabled`, and `inference_enabled` semantics aligned across `src/config.py`, `src/main.py`, `src/providers/registry.py`, and `tests/test_app.py`.
- Runtime probe controls are now provider-agnostic maps (`providers.<id>.active_probe_enabled` and `health.daily_request_budget_by_provider.<id>`). Preserve generic parsing/override behavior in `src/config.py` and `src/health.py`.
- Active probe budget usage is now reserved atomically before awaiting probe calls; keep that lock-protected reservation path intact so concurrent probe batches cannot overshoot daily provider budgets.
- Startup discovery-pipeline failures now degrade startup instead of terminating the process; preserve this behavior in `src/main.py` so restricted-egress/provider outages do not crash the gateway.
- Provider onboarding is now module-driven; keep adding provider-specific logic inside `src/providers/*` instead of core orchestration paths.
- Prefer shared OpenAI-compatible adapter abstractions for API-key providers over copy-paste adapter forks.
- Keep OpenRouter behavior backward-compatible while platformization lands; treat regressions in current OpenRouter startup/discovery/routing behavior as P0 issues.
- OpenRouter discovery now treats models as stream-capable by default because `supported_parameters` does not consistently include `"stream"` even when streaming works; preserve this unless OpenRouter publishes a stronger explicit capability contract.
- Stream error parsing in `src/proxy.py` is now provider-agnostic via provider categorization callbacks; do not reintroduce direct provider-specific imports into proxy orchestration.
- Ranking/discovery now support neutral `provider_rank` with legacy `openrouter_rank` fallback/backfill; preserve migration compatibility until legacy cleanup is explicitly approved.
- OpenRouter readiness now depends on runtime capability, not just persisted rows: if there is no real API key and explicit dev-stub mode is off, startup/reload should deactivate OpenRouter rows and keep the registry non-routable.
- The no-key OpenRouter stub is now explicit dev-only behavior controlled by `providers.openrouter.dev_stub_enabled` and `APP_ENV=dev`; do not reintroduce implicit no-key stub activation.
- `mark_success()` now maintains rolling latency/TTFB metrics with `ROLLING_METRIC_ALPHA` in `src/health.py`; preserve smoothing behavior unless you intentionally redesign ranking inputs.
- The no-key OpenRouter stub now returns the same fallback identity as `ranking.fallback_model` (`openrouter/openrouter/free`); keep config and stub discovery aligned if either changes.
- `get_token_estimation_review_summary()` should prefer request-time `selected_context_window` snapshots over live model metadata when analyzing context-failover recoveries; preserve that historical behavior when changing health review queries.
- Benchmark-name normalization now lives in `src/benchmarks.py`; reuse it for cache refresh and discovery joins instead of reintroducing duplicate normalization logic.
- Installer assets now live at repo root (`install.sh`, `uninstall.sh`, `install.ps1`, `uninstall.ps1`) and CI smoke-tests them with env-driven non-interactive inputs against a fake Docker shim; keep those env override paths working when changing installer prompts.
- Installer runtime smoke also now supports local-image validation via `FREELUNCH_SKIP_PULL=true`; preserve that opt-in path in both shell and PowerShell installers for real-daemon CI/local smoke checks.
- The Docker image smoke test intentionally sets `APP_ENV=dev` plus `OPENROUTER_DEV_STUB_ENABLED=true` so `/healthz` can validate app bootstrap without real provider credentials; do not silently change that CI assumption without updating the workflow and docs together.
- Local secret files are intentionally ignored (`.env`, `.env.*`, `.testkey`, `.envrc`) while `.env.example` remains tracked; preserve that guardrail.
- Manual provider validation now uses `scripts/provider_smoke.py`; keep it non-CI, budget-aware, and independent from app startup behavior.
- Dependency warning hygiene is now tied to upgraded baselines in `requirements.txt`/`requirements-dev.txt`; when changing those pins, re-check Python 3.14 warning behavior before adjusting filters.

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
- Testing strategy: `TESTING.md`
- Release sign-off matrix: `RELEASE_VALIDATION_MATRIX.md`
- Release sign-off evidence: `RELEASE_VALIDATION_EVIDENCE.md`
- Dev workflow: `CONTRIBUTING.md`
- Runtime defaults: `config.yaml.example`, `.env.example`
- Operator runbook: `OPERATIONS.md`
- Manual live-provider smoke harness: `scripts/provider_smoke.py`
- Behavior examples: `tests/test_api.py`, `tests/test_app.py`, `tests/test_health.py`, `tests/test_routing.py`
- Property/stress examples: `tests/test_property_routing.py`, `tests/test_property_tokens.py`, `tests/test_stress_concurrency.py`
- Provider-boundary examples: `tests/test_openrouter.py`
