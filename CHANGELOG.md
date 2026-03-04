# Changelog

This project loosely follows Keep a Changelog and uses semantic version tags for releases.

## [Unreleased]

### Added

- Queue-backed JSON runtime logging on a separate listener thread, with `concise`, `verbose`, and `debug` verbosity modes plus `/admin/health.runtime_logging` status reporting.
- Probe-budget usage, probe runtime summaries, recent probe/bootstrap activity, and token-estimation review summaries in `/admin/health`.
- Periodic config override refresh via a scheduled `config_refresh` job.
- Configurable request-log retention with daily pruning maintenance.
- Installer scripts for Linux/macOS and PowerShell, plus CI syntax validation and non-interactive smoke coverage for installer assets.
- `TASKS.md` and `AGENTS.md` to keep actionable backlog tracking and repo-specific implementation rules separate from the spec-gap snapshot.
- `tiktoken`-backed request sizing for OpenAI-compatible model families.
- Hugging Face `AutoTokenizer` exact request sizing for resolvable non-OpenAI model families, with `use_fast=True` and `trust_remote_code=False`.
- Additional Hugging Face repo-id normalization for non-OAI sizing, including DeepSeek aliases, StepFun / Z.AI aliases, NVIDIA Nemotron patterns, Mistral dated suffixes, Cohere Command-R aliases, and mixed alphanumeric model tokens such as `R1`, `32B`, and `A22B`.
- Family- and content-type-calibrated local heuristic token sizing for unresolved tokenizer families.
- Token-estimation review summaries in `/admin/health`, including 7-day manual-review flags for context failures, failover recoveries, and estimate-vs-usage mismatch.
- More robust benchmark ingestion that walks backward through parseable Chatbot Arena artifacts and respects the current Open LLM dataset-server row-page limit.
- Stronger direct OpenRouter adapter coverage for retry exhaustion, raw-body error parsing, dev-stub behavior, and streaming transport/setup failures.
- A dedicated `OPERATIONS.md` runbook covering deployment posture, admin endpoint interpretation, runtime logging, token-estimation review, and live validation guidance.

### Changed

- Discovery now performs best-effort external benchmark refresh before provider upserts and joins cached benchmark data into discovered model rows using normalized model names.
- Benchmark refresh now honors per-source cache freshness and prefers richer parseable Chatbot Arena artifacts before weaker fallback CSVs.
- Request sizing now accounts for structured message metadata and uses exact tokenizer counts for OpenAI-compatible families plus resolvable non-OpenAI Hugging Face families instead of relying only on heuristics.
- Successful Hugging Face tokenizer loads are now cached in-process while transient tokenizer-load failures are retried on later requests instead of being memoized as permanent misses.
- Discovery now best-effort schedules Hugging Face tokenizer preloads in the background so uncached non-OAI exact sizing can warm asynchronously instead of forcing the request path to wait for first-use loads.
- Local-only heuristic token sizing is now calibrated strongly enough by tokenizer family and content type to treat the token-estimation pipeline as complete under the current project policy.
- The project spec now explicitly defines token-estimation review signals and manual thresholds, and requires any no-key OpenRouter stub to be gated behind explicit dev mode.
- The OpenRouter no-key stub is now explicit dev-only behavior, disabled by default and ignored outside `APP_ENV=dev`.
- Client request logs now persist tokenizer-review evidence (`selected_provider_model_id`, `selected_tokenizer_family`, `estimated_prompt_tokens`, and `selected_context_window`) for later diagnostics.
- Low-priority client request logging now honors `logging.request_log_enabled` and `logging.log_queue_size` at runtime.
- The DB writer queue is now bounded and reserves capacity for metadata writes while leaving low-priority client logs lossy under saturation.
- Discovery, ranking, and health scheduler intervals are configurable and reloadable at runtime, alongside periodic config refresh.
- The release workflow now builds multi-arch images with cache, publishes richer semver tags, and creates GitHub releases.
- The README, contributing guide, and config example now describe the implemented operator workflow, runtime logging model, token-estimation pipeline, benchmark resilience, provider gating semantics, and current production posture in more depth.
- `FREELUNCH_SPEC_v8.md` now reflects the current repository scope and accepted policies instead of older aspirational multi-section build guidance.

### Fixed

- CI lint/install validation now passes current Ruff `UP038` and ShellCheck expectations in `src/benchmarks.py` and `install.sh`.
- App-heavy tests now disable external leaderboard refresh and startup probes in their generated configs so CI runtime is not dominated by repeated network-bound startup work.
- The Docker smoke job now boots the image under explicit dev-stub mode instead of expecting no-key startup to succeed without configured runtime capability.
- The no-key OpenRouter stub fallback model is aligned with the configured routing fallback identity.
- OpenRouter startup no longer remains ready on stale persisted rows when credentials are missing and explicit dev-stub mode is off.
- Real OpenRouter discovery no longer synthesizes a fake fallback model when `/models` returns no eligible entries.
- Token-review failover analysis now uses request-time context-window snapshots instead of drifting with later model metadata changes.
- Additional non-OAI tokenizer families now resolve to exact counts instead of heuristic fallback when their provider model IDs differ from Hugging Face naming only by predictable aliases or casing.
- Health success updates now maintain rolling latency/TTFB metrics instead of overwriting them with the most recent sample.
- SQLite connection lifecycle handling for read/init paths now closes short-lived connections deterministically.
- Discovery now deactivates provider models that disappear from later discovery runs.
- Discovery now applies cached benchmark scores from `leaderboard_cache` via normalized model-name matching.
- The proxy now handles exhausted `CONTEXT_EXCEEDED` failures more cleanly.
- Cancelled background Hugging Face tokenizer preloads during shutdown no longer emit misleading warning-level failure logs with empty error text.

### Infrastructure

- Typed `gateway` and `database` settings parsing was added and SQLite `busy_timeout_ms` is now applied to runtime connections.
- Provider gating semantics were added for `providers.enabled`, provider `enabled`, `discovery_enabled`, and `inference_enabled`.
- CI now enforces the 80% coverage floor and includes a Docker `/healthz` smoke test after image build.
- CI now tests Python 3.14 in addition to earlier supported versions.
- Local vendored dependency trees are excluded from repo-wide lint/typecheck commands, and Docker build-context hygiene was improved.

## [0.2.0] - 2026-03-03

### Added

- Spec-aligned schema expansion.
- Provider contracts and normalized error types.
- Routing candidate selection with failover behavior.
- Admin endpoints and streaming response support.
- Scheduler job registration.
- Config override integration.
