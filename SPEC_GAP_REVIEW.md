# FreeLunch Spec Gap Review (against `FREELUNCH_SPEC_v8.md`)

## Overall status

The codebase is now a **feature-complete implementation for the accepted OpenRouter-first scope** and a **landed first pass of multi-provider onboarding**: bounded failover, admin/config APIs, scheduled discovery/ranking/health jobs, queue-backed runtime logging, installer assets, descriptor/factory provider bootstrap, and a green automated test suite.

The approved module-only provider-platform objective is now implemented for the first API-key wave: providers can be onboarded via `src/providers/<id>.py` plus config enablement without adding provider-specific logic to `src/main.py`, `src/proxy.py`, or `src/health.py`.

March 2026 pre-public validation pass: `ruff`/`mypy` are clean, focused runtime integration tests pass, and full-suite + coverage validation now passes at `418 passed` with `97.68%` total `src/` line coverage.
Wave A and Wave B testing expansions landed (runtime/proxy/routing/provider/registry edge branches, benchmark fixture corpus + benchmark edge branches, scheduler branch-depth suite, token edge-gate/fallback coverage, discovery edge/invariant coverage). Wave C coverage-closure then landed for `proxy`, `openai_compatible`, `config`, `health`, and `runtime_logging`, followed by Wave D hard-test additions (property and stress/concurrency suites).

Quality target update (March 2026): repository policy targets **97%+** line coverage plus deeper hard-test modes (fault injection, property-based/stress, and stronger outside-repo validation). The coverage target is now met; remaining work is depth/realism oriented.

## What is already in place

- FastAPI app bootstrap with lifespan startup/shutdown wiring, readiness gating, and scheduler registration.
- Forward-only SQLite migrations with canonical UTC `Z` timestamps plus a dedicated writer thread.
- Core schema coverage for `models`, `request_log`, `leaderboard_cache`, and `config_overrides`.
- Provider registry plus an OpenRouter adapter for discovery, chat completions, streaming, probing, and normalized error mapping.
- Core API surface implemented: `/healthz`, `/readyz`, `/v1/models`, `/v1/chat/completions`.
- Admin API surface implemented: `/admin/models`, `/admin/models/{id}`, enable/disable endpoints, `/admin/health`, `/admin/config`, `/admin/refresh`, and `/admin/logs`.
- Bounded multi-candidate routing with capability filters, fallback insertion, request-preference headers, and retryable failover.
- Streaming relay hardening for pre-first-byte failover, keepalive suppression, TTFB capture, and terminal `[DONE]` handling.
- Health and ranking loops that use passive request telemetry plus conservative active probes, exponential cooldown backoff, and composite scoring.
- Test coverage across DB, health, ranking, routing, API, streaming, admin behavior, config override flows, benchmark refresh, runtime logging, token estimation, and direct OpenRouter adapter parsing.
- CI and release workflows are present instead of missing entirely.

## Remaining gaps and intentional boundaries

### 0) Module-only API-key provider onboarding (implemented baseline, hardening ongoing)

Landed in code:

- `src/main.py` now uses descriptor/factory-driven provider bootstrap (`registry.register_configured(settings)`).
- `src/providers/registry.py` loads providers through built-in and module descriptors (`src.providers.<provider_id>` with `build_provider_adapter` / `PROVIDER_BOOTSTRAP_DESCRIPTOR`).
- Shared OpenAI-compatible adapter support is implemented in `src/providers/openai_compatible.py`.
- First provider wave modules are present: OpenAI, Together, Groq, DeepSeek, xAI, Cerebras, Perplexity, Nvidia.
- Provider-contract behavior is covered directly in `tests/test_openai_compatible.py` and startup/config integration tests.

Residual work:

- Continue using and refining the non-CI live-provider smoke harness (`scripts/provider_smoke.py`) as provider coverage broadens.
- Keep operator docs synchronized as provider defaults, keys, and onboarding posture evolve.

### 1) Benchmark ingestion remains best-effort against unstable upstream artifacts (maintenance risk)

- Discovery now refreshes external benchmark cache data before provider model upserts, and then joins cached benchmark entries into discovered model rows via normalized name matching.
- Open LLM leaderboard ingestion is now automated from the Hugging Face dataset API.
- Benchmark refresh now respects per-source cache-hour freshness and skips external fetches when cached source data is still fresh.
- Chatbot Arena refresh now attempts direct `elo_results_*.pkl` snapshot parsing first, then falls back through older parseable snapshots, then `leaderboard_table_*.csv`, and only then to `arena_hard_auto_leaderboard_*.csv`.
- Open LLM refresh now adapts to the current Hugging Face dataset-server row-page limit instead of assuming larger page sizes are always accepted.
- Open LLM refresh now also adapts dynamically when the server-enforced `length` limit is lower than expected and advances offsets by returned-row count to avoid page skips.
- Open LLM score parsing now falls back from `Average ⬆️` to `Average`/`average`-like columns and supports model-name fallbacks (`fullname` -> `eval_name` -> `model`).
- Remaining risk: the public ELO snapshot and dataset-row schemas are not strongly contracted, so this path is still best-effort rather than guaranteed against upstream shape shifts.

### 2) Token estimation is complete for current policy, but some families remain heuristic by design (intentional boundary)

- The proxy now performs structured multimodal vision detection and `max_completion_tokens` parsing, and routing re-checks context fit against each candidate's tokenizer metadata instead of relying on a single request-wide guess.
- Request sizing now uses `tiktoken` for OpenAI-compatible encodings when the candidate exposes a compatible `provider_model_id` or tokenizer family such as `cl100k_base` / `o200k_base`.
- Non-OpenAI families now also try exact counts through Hugging Face `AutoTokenizer` when the routed `provider_model_id` resolves cleanly to a fast tokenizer with `trust_remote_code=False`.
- The Hugging Face resolution path now also tries repo-id aliases for common OpenRouter-to-HF mismatches, including Cohere Command-R aliases, DeepSeek / StepFun / Z.AI org aliases, Meta-Llama repo-name variants, NVIDIA Nemotron repo patterns, Mistral dated release suffixes, and mixed alphanumeric repo tokens such as `R1`, `32B`, and `A22B`.
- Request sizing also accounts for structured message metadata such as `tool_calls`, `function_call`, `audio`, `name`, `tool_call_id`, and `refusal`.
- Heuristic fallback sizing is now calibrated by tokenizer family and broad content type (`prose`, `code`, `json`) instead of relying on one punctuation-heavy generic profile for every unresolved family.
- The gateway now records durable token-review evidence in `request_log`, including selected provider model, selected tokenizer family, estimated prompt tokens, selected context window, and provider-reported `prompt_tokens` when available.
- `/admin/health` now exposes a 7-day `token_estimation_review` summary with threshold-based manual review flags for context failures, context-failover recoveries, and estimate-vs-usage mismatch. That summary is diagnostic only and does not auto-enable new tokenizer support.
- Failover-recovery review now uses request-time context-window snapshots from `request_log`, so historical analysis does not drift when model metadata changes later.
- Successful Hugging Face tokenizer loads are cached in-process, transient load failures are retried on later requests instead of being memoized forever as misses, and discovery now best-effort schedules tokenizer preloads in the background so the first request does not need to block waiting for every uncached family.
- OpenAI-prefixed GPT model IDs now normalize into `tiktoken`-compatible model names and family fallbacks, so newer OpenAI slugs such as `openai/gpt-5.3-chat` no longer fall straight to heuristics just because the provider prefix obscured the tokenizer mapping.
- Under the accepted local-only policy, the remaining closed/tokenizer-API-only tail (Claude, Gemini, Grok, Nova, Router, plus unresolved `Other` models) is intentionally handled by calibrated family/content-type heuristics and review telemetry rather than remote provider-native counters.
- Tokenizer prewarming is intentionally not enabled by default because the measured memory cost was not justified for the current deployment model.
- `CONTEXT_EXCEEDED` now returns a final `400` when that is the exhausted cause and no longer penalizes model health, but the retry decision is still not explicitly driven by context-size comparisons between alternates.
- Capability-aware routing is therefore materially better than before, and the remaining tradeoff in this area is now a conscious local-only policy choice rather than a missing pipeline component.

### 3) Health and observability are largely complete, with optional sophistication still open (optional depth)

- Success updates now maintain rolling `avg_latency_ms` / `avg_ttfb_ms` values instead of overwriting them with the newest sample, which better matches ranking/health intent without expanding the schema.
- Probe selection covers cooldown recovery, never-probed models, and stale models, but provider-specific probe policy objects and optional exploration sampling are still absent.
- Probe execution now reserves provider budget atomically under concurrency, preventing budget overshoot in parallel probe batches.
- `/admin/health` now exposes probe-budget usage, probe policy/runtime state, next-candidate previews with explicit probe reasons, and recent probe/bootstrap activity in addition to bootstrap state, queue depth, provider summaries, scheduler job status, and recent model errors.
- Request-log retention is now configurable and pruned by a daily maintenance job, and `logging.request_log_enabled` / `logging.log_queue_size` now affect low-priority client request logging at runtime.
- Queue-backed JSON runtime logging now runs through a separate listener thread with `concise`, `verbose`, and `debug` modes, and `/admin/health` surfaces `runtime_logging` status including queue depth and dropped-record counts.
- Tokenizer preload scheduling now uses an atomic check+submit critical section, preventing duplicate preload submissions under concurrent request pressure.
- The remaining gap in this area is now mostly about optional probe-policy sophistication rather than missing operator visibility.

### 4) Configuration/runtime parity is effectively in place for the currently shipped feature set (low residual risk)

- `config.yaml.example` and `Settings` now cover the implemented gateway, discovery, routing, ranking, health, logging, and database knobs, and SQLite `busy_timeout_ms` is now applied to real connections.
- Provider gating semantics are now implemented for `providers.enabled`, `providers.<name>.enabled`, `providers.<name>.discovery_enabled`, and `providers.<name>.inference_enabled`.
- OpenRouter rows are now deactivated when inference is not runtime-capable, including the case where no API key is present and explicit dev-stub mode is off.
- Runtime overrides are now applied at startup, on admin mutation, and via a periodic config-refresh job.
- The DB writer queue is now bounded, low-priority client logs are explicitly lossy, and reserved queue capacity plus blocking backpressure protect higher-priority metadata writes.

### 5) Provider realism is accepted for current shipped scope, with one explicit dev-only escape hatch (intentional boundary)

- The OpenRouter adapter now keeps stub discovery/chat/stream behavior only behind explicit development-only mode. It is disabled by default and ignored outside `APP_ENV=dev`, which closes the main readiness/auth realism concern from the earlier review.
- Runtime startup no longer stays ready off stale OpenRouter rows when credentials are missing, and real discovery no longer synthesizes a fake fallback row when `/models` returns no eligible entries.
- The repository still carries a dev-only stub path, but this is now a deliberate development policy choice rather than a hidden production realism defect.

### 6) Repository and docs are materially stronger, though some polish remains ongoing (maintenance)

- CI is now present and useful, but it still does not fully match the spec’s repo-quality bar:
  - installer coverage is still a lightweight fake-Docker smoke path rather than a full live-daemon install test
- CI now enforces the 80% coverage floor and includes a Docker `/healthz` smoke test after image build.
- CI also now exercises Python 3.14 explicitly alongside the older supported versions.
- Test/dependency hygiene on Python 3.14 was improved by upgrading FastAPI/Starlette/pytest/pytest-asyncio baselines and applying narrowly scoped pytest warning filters for known third-party asyncio deprecations still present upstream.
- CI now validates installer assets syntactically (`sh -n`, ShellCheck, and PowerShell parser checks) and runs non-interactive shell/PowerShell smoke tests against a fake Docker shim.
- Docker smoke in CI now runs auth-on, budget-zero checks for `/healthz`, `/readyz`, `/v1/models` (401 and 200 behavior), and tiny authenticated non-stream + stream chat requests in dev-stub mode.
- CI now also runs a real-daemon Linux installer runtime smoke (`install.sh` + compose runtime + authenticated API checks + uninstall cleanup) using a local built image and `FREELUNCH_SKIP_PULL=true`.
- Current measured line coverage is now **97.68%** (`418 passed` full-suite coverage run), above both the original 80% floor and the updated 97% target.
- `release.yml` now performs a multi-arch GHCR build with GHA cache, semver tag expansion, and GitHub release publishing.
- `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, and the new `OPERATIONS.md` now give materially better operator and maintainer coverage than earlier revisions.
- Section 15 installer assets are now present: `install.sh`, `uninstall.sh`, `install.ps1`, `uninstall.ps1`.
- Installer defaults and docs are now aligned on lowercase GHCR image names, and PowerShell install now fails fast on `docker pull` / `docker compose up` failure rather than printing false-success summaries.
- Secret-hygiene guardrails now explicitly ignore `.env` / `.env.*` (while preserving `.env.example`) and add key-handling guidance in user-facing docs.
- Pytest now ignores transient `tests/tmp_*` folders to avoid accidental local temp-directory collection failures.

### 7) Test-depth and runtime-realism follow-through (closed for current release)

- Coverage target remains met (`97.68%`), and high-risk core modules have materially deeper edge-path coverage.
- Property-based routing/token invariant suites and stress/concurrency suites are now landed, and they surfaced/fixed real concurrency bugs in probe budgeting and tokenizer-preload de-duplication.
- Manual sign-off checklist is codified in `RELEASE_VALIDATION_MATRIX.md`.
- Execution evidence lives in `RELEASE_VALIDATION_EVIDENCE.md`: M1/M2/M4/M5/M6/M7/M8 are complete, and M3 (macOS installer path) is explicitly accepted as blocked/waived by project owner due host unavailability.
- Dedicated execution plan remains in `TESTING.md` for future cross-host follow-through.

## Suggested implementation order (re-baselined)

1. **Testing Excellence Wave A**: runtime + provider hard-branch closures.
2. **Testing Excellence Wave B**: benchmark/scheduler/token branch-depth expansion.
3. **Testing Excellence Wave C**: property-based and stress/fault-injection suites (landed).
4. **Testing Excellence Wave D**: outside-repo runtime/installer/auth validation hardening (landed for this release; macOS run explicitly waived).

## Notes

- This review treats provider bootstrap/factory completion and first-wave module onboarding as landed.
- `TASKS.md` is now the working backlog and should be pruned whenever a spec-facing item lands.
