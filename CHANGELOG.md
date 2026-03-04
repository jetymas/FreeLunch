# Changelog

## [Unreleased]
- Added automatic external benchmark refresh during discovery, including Open LLM ingestion and best-effort Chatbot Arena cache population.
- Made request context sizing aware of candidate tokenizer families during routing.
- Added direct OpenRouter adapter tests for discovery parsing, usage extraction, and error normalization.
- Added typed `gateway` / `database` settings parsing and applied SQLite `busy_timeout_ms` to runtime connections.
- Updated CI to enforce the 80% coverage floor and added a Docker `/healthz` smoke test after image build.
- Expanded CI test coverage to include Python 3.14.
- Expanded the release workflow to build multi-arch images with cache, publish richer semver tags, and create GitHub releases.
- Expanded the README with concrete client setup guidance and clearer local development commands.
- Aligned the no-key OpenRouter stub fallback model with the configured routing fallback identity.
- Replaced last-sample latency/TTFB overwrites with rolling health metrics for model success updates.
- Fixed SQLite connection lifecycle handling for read/init paths so short-lived connections are closed deterministically.
- Discovery now deactivates provider models that disappear from later discovery runs.
- Discovery now applies cached benchmark scores from `leaderboard_cache` via normalized model-name matching.
- Added lightweight request token estimation, structured multimodal vision detection, and better exhausted `CONTEXT_EXCEEDED` handling in the proxy path.
- Made discovery and ranking scheduler intervals configurable and reloadable at runtime, alongside health interval changes.
- Added configurable request-log retention with daily pruning maintenance.
- Added probe-budget usage and remaining-budget reporting to `/admin/health`.
- Added periodic config override refresh via a scheduled `config_refresh` job.
- Excluded local vendored dependency trees from repo-wide lint/typecheck commands and added Docker build-context hygiene.
- Added `TASKS.md` and `AGENTS.md` to separate actionable backlog tracking from spec-gap review notes.
- Refreshed project documentation to reflect the current review baseline.

## [0.2.0] - 2026-03-03
- Expanded gateway implementation toward FREELUNCH spec:
  - spec-aligned schema expansion
  - provider contracts and error types
  - routing candidate selection + failover path
  - admin endpoints and streaming response support
  - scheduler job registration
  - config overrides integration
