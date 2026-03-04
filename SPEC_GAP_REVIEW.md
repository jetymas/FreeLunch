# FreeLunch Spec Gap Review (against `FREELUNCH_SPEC_v8.md`)

## Overall status

The codebase is now a **working OpenRouter-first MVP** with real routing, failover, admin/config endpoints, recurring jobs, and a green automated test suite. The largest remaining gaps are no longer basic endpoint scaffolding; they are mostly about **spec-accurate production behavior**, **config completeness**, and **repository/release parity**.

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
- Test coverage across DB, health, ranking, routing, API, streaming, admin behavior, config override flows, benchmark refresh, and direct OpenRouter adapter parsing.
- CI and release workflows are present instead of missing entirely.

## Major gaps still to implement

### 1) Discovery reconciliation and benchmark enrichment are still incomplete (high priority)

- Discovery now refreshes external benchmark cache data before provider model upserts, and then joins cached benchmark entries into discovered model rows via normalized name matching.
- Open LLM leaderboard ingestion is now automated from the Hugging Face dataset API.
- Chatbot Arena refresh is also automated, but it currently uses the public `arena_hard_auto_leaderboard` CSV `score` export as a benchmark proxy rather than parsing the richer ELO snapshot data from the source space.
- Cache-hour settings for leaderboard sources are now wired into configuration, but refresh execution still behaves as eager best-effort fetch on each discovery run rather than enforcing a stricter per-source TTL.

### 2) Request parsing and routing fidelity are still partial (high priority)

- The proxy now performs lightweight request token estimation, structured multimodal vision detection, and `max_completion_tokens` parsing, and routing now re-checks context fit against each candidate's `tokenizer_family` when that metadata is available.
- The remaining gap is that token sizing is still heuristic and family-based rather than backed by real tokenizer libraries or provider-native token counters.
- `CONTEXT_EXCEEDED` now returns a final `400` when that is the exhausted cause and no longer penalizes model health, but the retry decision is still not explicitly driven by context-size comparisons between alternates.
- Capability-aware routing is therefore materially better than before, but request-shape analysis still falls short of the spec’s ideal production depth.

### 3) Health and observability still stop short of the spec’s production depth (medium-high priority)

- Success updates now maintain rolling `avg_latency_ms` / `avg_ttfb_ms` values instead of overwriting them with the newest sample, which better matches ranking/health intent without expanding the schema.
- Probe selection covers cooldown recovery, never-probed models, and stale models, but provider-specific probe policy objects and optional exploration sampling are still absent.
- `/admin/health` now exposes probe-budget usage and remaining budget by provider in addition to bootstrap state, queue depth, provider summaries, scheduler job status, and recent model errors.
- Request-log retention is now configurable and pruned by a daily maintenance job, but the broader logging/config surface from the spec is still incomplete.

### 4) Configuration/runtime parity is still incomplete (high priority)

- `config.yaml.example` and `Settings` now cover the implemented gateway, discovery, routing, ranking, health, logging, and database knobs, and SQLite `busy_timeout_ms` is now applied to real connections.
- The remaining config/runtime gaps are narrower, but still meaningful:
  - `providers.enabled`
  - provider `enabled` / `discovery_enabled` / `inference_enabled`
  - logging knobs that are parsed but not yet used to change runtime behavior, such as `logging.request_log_enabled` / `logging.log_queue_size`
- Runtime overrides are now applied at startup, on admin mutation, and via a periodic config-refresh job.
- Writer-queue sizing/backpressure controls from the spec are also not implemented; the in-process queue is unbounded.

### 5) Provider realism is still below the spec in one important area (medium priority)

- The OpenRouter adapter provides stub discovery/chat/stream behavior when no API key is configured. That is useful for local tests, and the stub fallback model is now aligned with `ranking.fallback_model`, but the stub path still diverges from the production contract described in the spec and can mask readiness/auth problems.
- The initial-provider boundary is otherwise in good shape, but production behavior should be driven by the real provider contract rather than a silent local fallback path.

### 6) Repository, docs, and release parity still trail the spec (medium priority)

- CI is now present and useful, but it still does not fully match the spec’s repo-quality bar:
  - no installer-validation job from Section 15
- CI now enforces the 80% coverage floor and includes a Docker `/healthz` smoke test after image build.
- CI also now exercises Python 3.14 explicitly alongside the older supported versions.
- Current measured line coverage is now **84%**, above the original 80% target, though `src/providers/openrouter.py` still remains the weakest-covered major module.
- `release.yml` now performs a multi-arch GHCR build with GHA cache, semver tag expansion, and GitHub release publishing.
- `README.md`, `CONTRIBUTING.md`, and `CHANGELOG.md` are present, but they are still lighter than the spec requires:
  - CONTRIBUTING lacks the fuller development/reporting guidance
  - changelog structure is minimal rather than a full Keep a Changelog style history
- Section 15 installer assets are absent: `install.sh`, `uninstall.sh`, `install.ps1`, `uninstall.ps1`.

## Suggested implementation order (re-baselined)

1. **Tighten benchmark enrichment**: honor cache TTLs more strictly and move Chatbot Arena refresh from the current CSV score proxy to richer ELO snapshot ingestion.
2. **Harden request analysis**: replace heuristic token estimation with model-aware/tokenizer-aware sizing.
3. **Complete config/runtime control**: fill out `config.yaml`/env coverage, provider enablement controls, and any remaining typed settings.
4. **Deepen health observability**: finish remaining probe-policy behavior and richer admin/runtime visibility.
5. **Close infrastructure/docs gaps**: CI quality gates, release workflow parity, README/CONTRIBUTING expansion, and Section 15 installer files.

## Notes

- This review replaces stale claims from the previous version that implied admin/config endpoints, CI workflows, and broad test coverage were still missing. Those areas now exist; the remaining work is narrower and more production-oriented.
- `TASKS.md` is now the working backlog and should be pruned whenever a spec-facing item lands.
