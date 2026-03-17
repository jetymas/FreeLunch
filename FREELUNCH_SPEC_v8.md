# FreeLunch
## Implementation-Aligned Project Specification

| Field | Value |
|---|---|
| Document Version | 1.4 |
| Date | March 4, 2026 |
| Status | Authoritative for current repository scope |
| Repository | `github.com/jetymas/FreeLunch` |
| Primary Runtime | Python 3.11+ |
| Initial Provider | OpenRouter |

## 1. Purpose

FreeLunch is a self-hosted, OpenAI-compatible LLM gateway that routes requests to the best currently available free model discovered through OpenRouter. It exists to give client applications one stable `/v1` endpoint while the gateway handles:

- model discovery
- capability filtering
- ranking
- health-aware routing
- bounded failover
- request telemetry
- operator observability

The repository is intentionally optimized for a single-node deployment with low operational overhead. It prefers simple, inspectable behavior over distributed or highly abstracted designs.

## 2. Scope

### 2.1 In Scope

- OpenAI-compatible HTTP surface for chat completions and model listing
- OpenRouter-backed model discovery and inference
- SQLite-backed persistent state
- queued request telemetry and queued runtime logging
- scheduled discovery, ranking, health, maintenance, and config-refresh jobs
- capability-aware routing for streaming, tools, vision, structured output, context size, and output-token limits
- local-only token estimation with exact counts where safe and heuristics where exact local tokenizers are not available
- installer scripts, CI workflows, release workflow, and operator documentation

### 2.2 Out of Scope

- local model inference
- horizontal multi-node coordination
- embeddings
- a browser dashboard
- remote provider-native token counting on the request path
- automatic self-modifying tokenizer support

## 3. Design Principles

### 3.1 Provider Boundary

Provider-specific logic belongs in `src/providers/*` only.

Routing, health orchestration, proxy logic, ranking, and persistence must operate on normalized model metadata rather than provider-specific conditionals.

### 3.2 Single-Node Simplicity

The system is designed around:

- one application process
- one SQLite database
- one SQLite writer thread
- bounded background concurrency
- conservative scheduled work

This is a deliberate tradeoff. FreeLunch values operational simplicity and debuggability over scale-oriented complexity.

### 3.3 Passive-First Observability

The gateway prefers request-path telemetry and operator-facing summaries over aggressive probing or hidden adaptive behavior.

### 3.4 Explicit Policy Decisions

The following are accepted repository policies:

- OpenRouter is the default and most battle-tested production provider path today.
- First-wave API-key provider modules are available for OpenAI-compatible providers (OpenAI, Together, Groq, DeepSeek, xAI, Cerebras, Perplexity, Nvidia).
- The no-key OpenRouter stub remains available only in explicit development mode.
- Token estimation remains local-only.
- Exact token counting is used only when the gateway can resolve a safe local tokenizer.
- Unresolved tokenizer families are handled by calibrated heuristics plus review telemetry, not remote counter APIs.
- Tokenizer prewarming is not enabled by default because memory cost is not justified for the current deployment model.
- Provider onboarding is module-driven via provider descriptors/factories; maintenance focus is regression hardening and docs/operations alignment.

## 4. Runtime Architecture

### 4.1 Major Components

- `src/main.py`
  - FastAPI lifespan bootstrap/shutdown
  - runtime logger configuration
  - provider registry configuration
  - readiness state computation
  - scheduler initialization

- `src/proxy.py`
  - public API endpoints
  - admin endpoints
  - request parsing
  - candidate selection / failover orchestration
  - streaming relay behavior
  - request telemetry writes

- `src/providers/registry.py`
  - enabled provider registration
  - discovery-vs-inference gating

- `src/providers/openrouter.py`
  - provider discovery
  - chat completions
  - stream completions
  - probes
  - normalized provider error mapping

- `src/discover.py`
  - benchmark refresh
  - provider discovery pass
  - model upserts
  - model reconciliation for disappeared rows

- `src/ranking.py`
  - composite score recomputation

- `src/health.py`
  - passive health updates
  - bootstrap and recurring probes
  - probe budgets
  - cooldowns and backoff
  - admin-health summaries

- `src/tokens.py`
  - request-size estimation
  - multimodal inspection
  - local exact tokenizer resolution
  - heuristic fallback sizing
  - background tokenizer preload scheduling

- `src/runtime_logging.py`
  - queue-backed JSON runtime log pipeline
  - concise / verbose / debug verbosity filtering

- `src/db.py`
  - schema migrations
  - SQLite helpers
  - dedicated writer thread
  - request logging
  - config override persistence
  - benchmark cache persistence

- `src/scheduler.py`
  - recurring job registration
  - tracked execution wrappers
  - maintenance and config refresh

### 4.2 Startup Sequence

Boot order is:

1. load settings from environment and `config.yaml`
2. configure runtime logging
3. initialize database and apply forward-only migrations
4. start the SQLite writer thread
5. apply DB-backed config overrides
6. configure request-log policy
7. build provider registry according to provider gating rules
8. initialize app state and scheduler
9. deactivate OpenRouter rows if runtime inference is unavailable
10. run the discovery pipeline
11. recompute readiness from actual routable rows
12. start recurring jobs

### 4.3 Readiness Contract

`/readyz` returns success only when at least one model row is both:

- `is_active = 1`
- `is_healthy = 1`

Readiness is based on actual routable database state, not merely on configured providers or stale persisted rows.

### 4.4 Shutdown Contract

Shutdown must:

- stop the scheduler
- stop the DB writer thread
- shut down tokenizer preload executors
- shut down runtime logging

Background tokenizer preload cancellations during shutdown are expected and must not be represented as warning-level failures.

## 5. Data Model

All persistent state lives in SQLite. Timestamps use UTC ISO 8601 with a `Z` suffix.

### 5.1 `schema_migrations`

Purpose:

- forward-only schema version tracking

Core fields:

- `version`
- `applied_at`

### 5.2 `models`

Purpose:

- normalized routing table for discovered models

Important fields:

- identity
  - `id`
  - `name`
  - `provider_id`
  - `endpoint_id`
  - `provider_model_id`
  - `provider_base_url`
  - `provider_api_key_env`
  - `provider_options_json`
- capability metadata
  - `context_window`
  - `max_output_tokens`
  - `tokenizer_family`
  - `supports_tools`
  - `supports_streaming`
  - `supports_vision`
  - `supports_structured_output`
  - `supports_system_messages`
- benchmark and provider ranking metadata
  - `chatbot_arena_elo`
  - `open_llm_score`
  - `provider_rank`
  - `openrouter_rank`
- health and ranking state
  - `is_healthy`
  - `last_health_check`
  - `avg_latency_ms`
  - `avg_ttfb_ms`
  - `consecutive_failures`
  - `backoff_level`
  - `cooldown_until`
  - `last_error`
  - `last_probe_at`
  - `last_success_at`
  - `last_failure_at`
  - `last_routed_at`
  - `composite_score`
  - `score_updated_at`
- discovery lifecycle
  - `discovered_at`
  - `last_seen_at`
  - `is_active`

### 5.3 `request_log`

Purpose:

- durable request telemetry for health, diagnostics, token-estimation review, and operator inspection

Important fields:

- request identity and source
  - `id`
  - `request_id`
  - `timestamp`
  - `request_source`
- routing selection
  - `selected_model_id`
  - `provider_id`
  - `selected_provider_model_id`
  - `selected_tokenizer_family`
  - `client_requested_model`
  - `attempt_index`
  - `was_fallback`
- token-estimation evidence
  - `estimated_prompt_tokens`
  - `selected_context_window`
  - `prompt_tokens`
  - `completion_tokens`
  - `total_tokens`
- performance and outcome
  - `latency_ms`
  - `ttfb_ms`
  - `success`
  - `gateway_error_category`
  - `error_code`
  - `error_message`
- request shape
  - `was_streaming`
  - `had_tools`
  - `had_vision`

Policy:

- client request logs are low-priority and lossy under queue saturation
- probe and bootstrap logs are higher priority and must not be suppressed by client-log controls

### 5.4 `leaderboard_cache`

Purpose:

- normalized benchmark enrichment cache keyed by normalized model name

Fields:

- `model_name_normalized`
- `chatbot_arena_elo`
- `open_llm_avg_score`
- `fetched_at`

### 5.5 `config_overrides`

Purpose:

- DB-backed mutable runtime overrides

Fields:

- `key`
- `value`
- `updated_at`

## 6. Public API

### 6.1 Health Endpoints

- `GET /healthz`
  - liveness only
- `GET /readyz`
  - readiness based on routable models

### 6.2 Model Listing

- `GET /v1/models`
  - returns normalized active model list in OpenAI-style format
  - includes a synthetic `auto` alias when the gateway has an active healthy model pool

### 6.3 Chat Completions

- `POST /v1/chat/completions`
  - supports streaming and non-streaming requests
  - supports `model: "auto"`
  - supports bounded failover for retryable provider-origin failures

### 6.4 Admin Endpoints

- `GET /admin/ui`
- `GET /admin/models`
- `GET /admin/models/{id}`
- `POST /admin/models/{id}/disable`
- `POST /admin/models/{id}/enable`
- `GET /admin/health`
- `GET /admin/secrets`
- `POST /admin/secrets/vault/setup`
- `POST /admin/secrets/vault/unlock`
- `POST /admin/secrets/vault/lock`
- `PUT /admin/secrets/{key}`
- `DELETE /admin/secrets/{key}`
- `GET /admin/uninstall`
- `GET /admin/config`
- `PUT /admin/config/{key}`
- `DELETE /admin/config/{key}`
- `POST /admin/refresh`
- `GET /admin/logs`

### 6.5 Admin Health Payload Expectations

`/admin/health` must surface at least:

- bootstrap and readiness state
- DB writer status
- scheduler job state
- provider summary
- secret-management status
- runtime logging status
- recent model errors
- probe budget usage
- probe policy / candidate previews
- recent probe/bootstrap activity
- token-estimation review summary

## 7. Provider Model

### 7.1 Current Provider

The current shipping implementation supports one production provider:

- `openrouter`

### 7.2 Provider Gating

Provider runtime behavior is controlled by:

- `providers.enabled`
- `providers.<provider_id>.enabled`
- `providers.<provider_id>.discovery_enabled`
- `providers.<provider_id>.inference_enabled`
- `providers.<provider_id>.active_probe_enabled`
- `health.daily_request_budget_by_provider.<provider_id>`

Discovery and inference are distinct concerns.

Backward compatibility note:

- OpenRouter-specific keys remain supported while provider-agnostic keys are the preferred contract.

### 7.3 Development Stub Policy

The OpenRouter no-key stub:

- exists only as a development aid
- is disabled by default
- is only honored when `APP_ENV=dev`
- must not make production readiness appear healthy in the absence of a real key

### 7.4 Error Normalization

Provider adapters must normalize raw provider failures into gateway categories:

- `RATE_LIMITED`
- `PROVIDER_UNAVAILABLE`
- `INVALID_REQUEST`
- `AUTH_ERROR`
- `CONTEXT_EXCEEDED`

The OpenRouter adapter currently includes direct tests for:

- retry exhaustion
- fatal vs retryable distinction
- raw-body error parsing fallbacks
- streaming setup failures
- streaming transport failures
- dev-stub chat and stream behavior

### 7.5 Landed Milestone: Module-Only API-Key Provider Onboarding

The platform objective to make additional API-key providers viable without repeated core rewrites is now implemented.

Landed state:

- provider-specific implementation remains in `src/providers/*`
- future API-key provider onboarding should require a provider module plus config enablement, not edits to `src/main.py`, `src/proxy.py`, `src/health.py`, or `src/config.py`
- OpenRouter behavior remains supported and backward-compatible under the module-driven provider platform

Provider family in scope for this milestone:

- API-key providers with OpenAI-compatible chat/stream interfaces

Explicitly out of scope for this milestone:

- cookie/HAR/browser-auth providers
- account-scraping providers
- request-path remote token counting

## 8. Discovery And Benchmark Enrichment

### 8.1 Discovery Responsibilities

Each discovery run must:

1. refresh benchmark cache on a best-effort basis
2. fetch provider model inventory
3. normalize model rows into the `models` table
4. upsert fresh metadata
5. mark provider rows inactive when they disappear from the current provider response

### 8.2 Benchmark Sources

Current enrichment sources:

- Chatbot Arena
- Open LLM leaderboard

### 8.3 Benchmark Cache Rules

- benchmark refresh respects per-source freshness windows
- stale or failed refreshes must degrade to missing enrichment, not failed discovery
- cached scores are joined through normalized model names

### 8.4 Upstream Drift Policy

Benchmark ingestion is intentionally best-effort because upstream public artifacts are not strongly contracted.

The code currently hardens this by:

- walking backward through older parseable Chatbot Arena artifacts
- falling back across multiple artifact types
- respecting the Hugging Face dataset-server row-page limit for Open LLM rows

Remaining work in this area is maintenance-oriented rather than architectural.

## 9. Ranking

Ranking is composite and configurable.

### 9.1 Inputs

- benchmark score
- real-world usage
- latency
- availability
- context window
- feature support

Cold-start usage fallback uses provider-neutral rank metadata (`provider_rank`) with legacy `openrouter_rank` compatibility.

### 9.2 Ranking Constraints

- unhealthy rows must not outrank healthy rows as viable candidates
- routing filters still apply after ranking
- fallback insertion remains bounded and deterministic

### 9.3 Weights

Weights are runtime-configurable through config and override support.

## 10. Health Model

### 10.1 Passive Health

Passive health uses request outcomes to update:

- rolling latency
- rolling TTFB
- last success / failure
- consecutive failure counts
- cooldown state

### 10.2 Active Probes

Active probes are intentionally conservative and budget-limited.

Probe targeting includes:

- cooldown recovery candidates
- never-probed models
- stale models

### 10.3 Cooldown Behavior

Failures apply exponential backoff through:

- `consecutive_failures`
- `backoff_level`
- `cooldown_until`

### 10.4 Probe Budgets

Daily request budgets exist per provider and are surfaced to operators.

## 11. Routing

### 11.1 Candidate Selection

Routing operates on normalized database rows and filters by:

- active state
- health state
- cooldown state
- tools support
- vision support
- structured output support
- streaming support
- context window fit
- output-token fit

### 11.2 Request Preferences

Routing can use request preference headers when enabled.

### 11.3 Failover

Failover is bounded by configuration and applies only to retryable failures.

Important policy:

- exhausted `CONTEXT_EXCEEDED` returns a final `400`
- `CONTEXT_EXCEEDED` does not penalize model health

### 11.4 Streaming

Streaming must:

- relay provider SSE frames
- suppress keepalive/comment frames
- capture TTFB
- allow pre-first-byte failover
- avoid mid-stream failover after bytes have been emitted
- preserve terminal `[DONE]` semantics where applicable

Stream error payload parsing in proxy orchestration must remain provider-agnostic and rely on provider categorization hooks rather than direct provider imports.

## 12. Token Estimation Pipeline

### 12.1 Purpose

Token estimation exists to support routing and context-fit decisions before a request is sent to a provider.

It is not a billing subsystem.

### 12.2 Exact Local Paths

Exact local token counting is used when the gateway can safely resolve:

- OpenAI-compatible encodings through `tiktoken`
- non-OpenAI tokenizers through Hugging Face `AutoTokenizer` with:
  - `use_fast=True`
  - `trust_remote_code=False`

### 12.3 Resolver Behavior

The resolver supports:

- explicit tokenizer-family encodings such as `cl100k_base` and `o200k_base`
- provider-model-driven `tiktoken` lookups
- OpenAI-prefixed model normalization
- Hugging Face repo alias resolution for common provider-to-HF naming mismatches

### 12.4 Heuristic Fallback

When exact local tokenizers are unavailable, the gateway uses calibrated heuristics keyed by:

- tokenizer family
- content type
  - `prose`
  - `code`
  - `json`

### 12.5 Local-Only Policy

FreeLunch intentionally does not call remote token-count APIs on the request path.

Closed-family or API-only families are handled by:

- calibrated heuristics
- request telemetry
- manual review summaries

### 12.6 Tokenizer Review Telemetry

The gateway persists and summarizes:

- selected provider model
- selected tokenizer family
- estimated prompt tokens
- request-time selected context window
- provider-reported prompt tokens when available
- context-exceeded outcomes

`GET /admin/health` includes `token_estimation_review`, which is diagnostic and review-oriented only.

It must not automatically enable new tokenizer support.

### 12.7 Background Tokenizer Preloads

Discovery may schedule best-effort background tokenizer preloads.

Constraints:

- they must not block request handling
- unresolved first-use requests may fall back heuristically
- successful loads are cached in-process
- transient load failures may be retried later
- shutdown cancellations must be treated as expected debug-only events

### 12.8 Memory Policy

Tokenizer prewarming is intentionally not enabled by default because memory cost outweighs the current latency benefit for the repository’s deployment assumptions.

## 13. Logging And Observability

### 13.1 Two Distinct Logging Planes

FreeLunch distinguishes between:

- durable SQLite request telemetry in `request_log`
- ephemeral JSON runtime logs emitted by `runtime_logging`

These are not interchangeable.

### 13.2 Runtime Logging

Runtime logging characteristics:

- queue-backed
- separate listener thread
- JSON-line process output
- bounded queue
- lossy for low-priority records before stalling hot paths

Verbosity levels:

- `concise`
  - major lifecycle and operator-relevant events
- `verbose`
  - richer operational detail
- `debug`
  - very chatty; includes routing, scheduler, probe, and tokenizer-resolution detail

### 13.3 Request Telemetry

Request telemetry must be sufficient to support:

- health updates
- ranking inputs
- admin log inspection
- token-estimation review
- failure classification

### 13.4 Runtime Logging Status

`GET /admin/health` must include a `runtime_logging` object with at least:

- enabled state
- configured verbosity
- queue depth
- queue capacity
- dropped-record count

## 14. Configuration Model

### 14.1 Sources

Configuration comes from:

- `config.yaml`
- environment variables
- DB-backed overrides for approved keys

### 14.2 Key Namespaces

- `app`
- `providers`
- `discovery`
- `routing`
- `ranking`
- `health`
- `logging`
- `database`
- `gateway`

### 14.3 Runtime-Reload Expectations

Runtime override changes must flow through:

- startup load
- admin mutation
- periodic `config_refresh`

Scheduler intervals for discovery, ranking, and health must reschedule when their runtime config changes.

### 14.4 Current Important Settings

- provider enablement and discovery/inference gating
- benchmark refresh cadence and freshness windows
- routing attempts and request preference headers
- ranking weights and fallback model
- probe intervals, budgets, concurrency, and backoff controls
- request-log retention and queue limits
- runtime logging enablement, verbosity, and queue size
- SQLite busy timeout

## 15. Persistence And Write Policy

### 15.1 Write Thread Rule

Application writes must go through the dedicated writer thread.

Short-lived direct write connections are allowed only for initialization or narrowly scoped maintenance paths that are already implemented that way and are not hot-path application writes.

### 15.2 Queue Policy

The DB queue is bounded.

Behavior:

- client request logs are low priority and may be dropped
- reserved capacity protects higher-priority metadata writes
- blocking backpressure is acceptable for higher-priority writes

## 16. Scheduling

Registered recurring jobs:

- `discovery`
- `ranking`
- `health`
- `maintenance`
- `config_refresh`

All scheduled jobs should remain:

- single-instance
- coalesced
- explicitly observable through admin health output

## 17. Deployment Model

### 17.1 Primary Deployment Path

The repository is Docker-first.

Supported artifacts include:

- `Dockerfile`
- `docker-compose.yml`
- installer scripts

### 17.2 Installer Assets

Repository-root installer assets are part of the supported surface:

- `install.sh`
- `uninstall.sh`
- `install.ps1`
- `uninstall.ps1`

### 17.3 Provider Credential Expectations

Real deployments should provide:

- `OPENROUTER_API_KEY`

Production deployments should leave the development stub disabled.

## 18. Testing Strategy

### 18.1 Automated Test Expectations

The repository should maintain coverage over:

- DB migrations and writer behavior
- discovery and benchmark refresh behavior
- ranking
- health and probe logic
- routing
- proxy request handling
- admin endpoints
- runtime logging
- tokenizer resolution and heuristics
- direct provider adapter behavior

### 18.2 Current Quality Bar

The repository’s target quality bar is now:

- **97%+ line coverage** for `src/`
- increasing branch-depth in high-risk modules
- explicit hard-test coverage (fault-injection, property-based, stress/concurrency, and outside-repo validation)

Coverage remains a floor metric; acceptance requires depth across failure and edge behaviors, not only additional line execution.

### 18.3 Live Validation

When real provider credentials are available, low-cost live smoke tests are useful for:

- authenticated discovery
- app-level readiness verification
- one minimal provider check per targeted provider

The repository now includes an optional non-CI harness (`scripts/provider_smoke.py`) for this purpose.

Live checks should remain minimal and budget-aware.

### 18.4 Enforcement Migration

Coverage enforcement may be raised in staged gates during migration, but the accepted end-state requirement is 97%+.

## 19. Documentation Requirements

The repository’s core documentation set is:

- `FREELUNCH_SPEC_v8.md`
- `SPEC_GAP_REVIEW.md`
- `TASKS.md`
- `README.md`
- `TESTING.md`
- `RELEASE_VALIDATION_MATRIX.md`
- `RELEASE_VALIDATION_EVIDENCE.md`
- `IMPLEMENTATION_GUIDE.md`
- `CONTRIBUTING.md`
- `AGENTS.md`
- `OPERATIONS.md`
- `CHANGELOG.md`

Documentation rules:

- the spec defines the accepted target behavior for current scope
- the spec gap review records where code still falls short of that target
- the task list stays pruned and actionable
- operator-facing docs should explain current behavior, not aspirational behavior

## 20. Accepted Boundaries And Intentional Deviations

The following are intentional and should not be treated as bugs without a deliberate design decision:

- OpenRouter remains the default provider path for production deployments
- the dev stub still exists, but only as explicit dev-only behavior
- remote token-count APIs are not used
- tokenizer prewarming is not on by default
- benchmark ingestion remains best-effort because upstream schemas are unstable
- installer smoke coverage in CI uses a fake Docker shim rather than a full live-daemon install test

## 21. Current Remaining Work

The implementation remains close to spec-complete for the currently shipped OpenRouter-first behavior.

Remaining work is primarily:

- expanding multi-provider startup/readiness/routing regression depth
- keeping benchmark ingestion resilient as upstream public artifacts drift
- continuing documentation and release-history polish
- optionally deepening probe policy sophistication if operational evidence justifies it

## 22. File Map

Primary repository files:

- `src/main.py`
- `src/config.py`
- `src/db.py`
- `src/discover.py`
- `src/ranking.py`
- `src/health.py`
- `src/routing.py`
- `src/tokens.py`
- `src/proxy.py`
- `src/runtime_logging.py`
- `src/scheduler.py`
- `src/providers/base.py`
- `src/providers/openrouter.py`
- `src/providers/registry.py`
- `scripts/provider_smoke.py`
- `tests/*`
- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `config.yaml.example`
- `README.md`
- `IMPLEMENTATION_GUIDE.md`
- `CONTRIBUTING.md`
- `OPERATIONS.md`
- `AGENTS.md`
- `SPEC_GAP_REVIEW.md`
- `TASKS.md`

## 23. Summary

FreeLunch is no longer a speculative gateway design. It is an implemented, tested routing gateway with:

- bounded failover
- discovery and benchmark enrichment
- ranked health-aware routing
- local-only token estimation with exact and heuristic paths
- queued runtime logging
- durable request telemetry
- admin visibility
- installer and CI support
- module-driven provider onboarding for first-wave OpenAI-compatible API-key providers

The specification for this repository should therefore remain grounded in those concrete behaviors and the explicit policies that now govern them.
