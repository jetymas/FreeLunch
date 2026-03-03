# Intelligent LLM Gateway
## Complete Project Specification

| Field | Value |
|---|---|
| **Document Version** | 1.2 |
| **Date** | March 3, 2026 |
| **Author** | OpenAI + Manus AI working draft, consolidated and corrected |
| **Status** | Ready for Agent Handoff |
| **License** | MIT |
| **Target Repository** | `github.com/jetymas/FreeLunch` |

---

## Purpose of This Document

This specification is intended to be handed directly to a development agent or human developer as the complete, authoritative description of what to build. It is written to be unambiguous and self-contained: a developer who has never spoken to the project owner should be able to read this document and produce a correct, production-quality implementation.

## Publication Variables

**Resolved for this repository:** `REPO_OWNER = jetymas`.


This specification uses one repository publication token consistently:

- `jetymas` — the GitHub owner or organization that will publish the repository and container image.

Where repository URLs, raw-content URLs, or GHCR image names appear, `jetymas` is the only token that must be substituted before public release. All other architecture, file paths, and implementation behavior in this document are authoritative as written.

The document is organized into three logical parts:

**Part 1 — Foundation** covers the project overview, goals, non-goals, high-level architecture, the complete SQLite data model, and the external API contracts the gateway consumes.

**Part 2 — Modules** provides a module-by-module specification of every Python file in the `src/` directory, including public interfaces, detailed internal logic, error handling requirements, and client integration instructions for OpenClaw, Kilo Code, Open WebUI, and others.

**Part 3 — Infrastructure** covers the complete configuration reference (`config.yaml` and environment variables), the repository file structure, Dockerfile and Docker Compose files, the GitHub Actions CI/CD pipeline, the testing strategy with required test cases, open-source conventions (license, README structure, CONTRIBUTING.md, commit style), the deployment guide, and the future roadmap.

---

## Table of Contents

| Section | Title |
|---|---|
| **1** | Project Overview |
| 1.1 | Purpose |
| 1.2 | Problem Statement |
| 1.3 | Goals |
| 1.4 | Non-Goals |
| **2** | Architecture |
| 2.1 | High-Level Overview |
| 2.2 | Request Lifecycle |
| 2.3 | Subsystem Interaction & Scheduling |
| **3** | Data Models |
| 3.1 | `models` Table |
| 3.2 | `request_log` Table |
| 3.3 | `leaderboard_cache` Table |
| 3.4 | `config_overrides` Table |
| **4** | External API Contracts |
| 4.1 | OpenRouter — Model Discovery |
| 4.2 | OpenRouter — Inference, Streaming & Limits |
| 4.3 | Chatbot Arena — ELO Scores |
| 4.4 | LiteLLM — Provider Client Behavior |
| 4.5 | Gateway's Own Admin API |
| **5** | Module Specifications |
| 5.1 | `src/db.py` — Database Layer |
| 5.2 | `src/discover.py` — Discovery Orchestrator & Provider Plugins |
| 5.3 | `src/ranking.py` — Ranking Engine |
| 5.4 | `src/health.py` — Health Monitor |
| 5.5 | `src/routing.py` — Routing Engine |
| 5.6 | `src/proxy.py` — Proxy Server |
| 5.7 | `src/config.py` — Configuration |
| 5.8 | `src/scheduler.py` — Background Task Scheduler |
| **6** | Client Integration Specifications |
| 6.1 | OpenClaw |
| 6.2 | Kilo Code |
| 6.3 | Open WebUI |
| 6.4 | SillyTavern |
| 6.5 | Any OpenAI SDK (Python) |
| 6.6 | curl |
| **7** | Configuration Reference |
| 7.1 | `config.yaml` — Full Reference |
| 7.2 | Environment Variables Reference |
| **8** | Repository Structure |
| **9** | Docker Configuration |
| 9.1 | `Dockerfile` |
| 9.2 | `docker-compose.yml` |
| 9.3 | `docker-compose.dev.yml` |
| 9.4 | `requirements.txt` |
| 9.5 | `requirements-dev.txt` |
| **10** | CI/CD Pipeline |
| 10.1 | `.github/workflows/ci.yml` |
| 10.2 | `.github/workflows/release.yml` |
| **11** | Testing Strategy |
| 11.1 | `tests/conftest.py` — Shared Fixtures |
| 11.2 | Key Test Cases |
| **12** | Open Source Conventions |
| 12.1 | `LICENSE` |
| 12.2 | `README.md` — Required Sections |
| 12.3 | `CONTRIBUTING.md` — Required Content |
| 12.4 | `pyproject.toml` — Tool Configuration |
| 12.5 | Semantic Versioning |
| 12.6 | Changelog |
| **13** | Deployment Guide |
| 13.1 | VPS Deployment |
| 13.2 | Local PC Deployment |
| 13.3 | Updating the Gateway |
| 13.4 | Accessing the Admin API |
| **14** | Future Roadmap |
| **15** | Bootstrap Installer Scripts |
| | References |

---

# Intelligent LLM Gateway — Project Specification (Part 1 of 3)
## Overview, Architecture, Data Models & API Contracts

**Document Version:** 1.2  
**Date:** March 3, 2026  
**Author:** OpenAI + Manus AI working draft, consolidated and corrected  
**Status:** Ready for Agent Handoff

---

## 1. Project Overview

### 1.1 Purpose

The Intelligent LLM Gateway is an open-source, self-hosted proxy server that automatically routes LLM inference requests to the best available free model at any given moment. It exposes a single, unified OpenAI-compatible HTTP endpoint to all client applications, abstracting away the complexity of model selection, provider differences, rate limits, and availability. The user never needs to manually switch models or reconfigure their tools — the gateway handles all of that transparently.

### 1.2 Problem Statement

The LLM ecosystem is fragmented and fast-moving. New models are released weekly, free model availability changes frequently, and each provider has its own API format, rate limits, and error behaviors. Developers using tools like OpenClaw, Kilo Code, Open WebUI, or any OpenAI SDK currently face three compounding problems:

1. **Manual model management.** Discovering and switching to better free models requires manually updating configuration files across multiple applications.
2. **Single points of failure.** Relying on one provider or model means any outage or rate limit breach disrupts all dependent applications.
3. **API fragmentation.** Different inference providers expose slightly different behaviors, auth rules, and capability metadata, requiring a clean provider abstraction.

The gateway solves all three problems with a single deployment.

### 1.3 Goals

The following goals define the scope and success criteria for this project.

| Goal | Description | Priority |
|---|---|---|
| **Universal compatibility** | Expose a standard `/v1/chat/completions` endpoint compatible with OpenAI SDKs and OpenAI-style clients | Must Have |
| **Provider-pluggable architecture** | Keep provider-specific logic isolated behind a narrow adapter boundary so new providers can be added later without rewriting routing, health, or proxy logic | Must Have |
| **OpenRouter-first initial implementation** | Ship the first production-ready version with a single well-supported provider adapter (`openrouter`) instead of overextending into low-value providers | Must Have |
| **Health-aware routing** | Route only to healthy, capability-compatible models and fail over across alternates when retryable provider errors occur | Must Have |
| **Bootstrap readiness gating** | Do not report ready until migrations complete, bootstrap discovery succeeds, and at least one routable model exists | Must Have |
| **Docker-first deployment** | Package as a single `docker compose up` deployment with no host dependencies beyond Docker | Must Have |
| **Streaming support** | Support server-sent event (SSE) streaming end-to-end with correct relay semantics | Must Have |
| **Compute efficiency & low memory overhead** | Run in a single process with a single SQLite writer thread, conservative background work, and minimal in-memory state | Must Have |
| **Configurable ranking** | Allow users to tune ranking weights and request-time routing preferences via configuration and headers | Should Have |
| **Request logging & telemetry** | Log requests, model selections, latency, TTFB, and fallback behavior for observability and ranking inputs | Should Have |
| **Admin API** | Expose endpoints for inspecting models, readiness, health, logs, and refresh status | Should Have |
| **Capability-aware routing** | Filter explicitly on tools, vision, streaming, structured output, context window, and output-token limits | Should Have |
### 1.4 Non-Goals

The following are explicitly out of scope for the initial version:

- **Local model inference.** The gateway does not run models locally (no Ollama, llama.cpp, vLLM, or GPU scheduling).
- **Broad provider coverage in v1.** The initial shipping implementation includes only the `openrouter` provider adapter. Additional providers are added later as separate adapter modules.
- **Horizontal multi-node scale.** This specification targets a single-node deployment. SQLite WAL is intentionally used for simplicity; multi-replica operation is a future concern.
- **Embedding routing in the initial release.** `/v1/embeddings` remains a roadmap item.
- **A browser dashboard in the initial release.** Operational visibility is provided through JSON admin endpoints and logs, not a full web UI.
## 2. Architecture

### 2.1 High-Level Overview

The gateway is a single Python application that runs inside one container and is optimized for low operational overhead. Its runtime is intentionally simple:

1. **Proxy Server** — FastAPI app that accepts OpenAI-compatible requests.
2. **Routing Engine** — pure selection logic that chooses the best candidates from normalized DB rows.
3. **Provider Registry + Adapters** — the only place where provider-specific discovery, inference, probing, auth, and error quirks are allowed to exist.
4. **Discovery / Ranking / Health background jobs** — scheduled tasks that refresh metadata, recompute scores, and maintain health using passive-first signals.
5. **SQLite Database + Dedicated Writer Thread** — persistent state plus one authoritative write path.

The critical architectural rule is:

> **Routing, proxying, and health orchestration must never contain provider-specific conditionals.**
> Provider-specific logic belongs in `src/providers/*` only.

LiteLLM is used as a convenience client **inside provider adapters** where appropriate, but LiteLLM is **not** the abstraction boundary for the gateway. The abstraction boundary is the gateway's own provider adapter interface.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Client Applications                         │
│   OpenClaw  │  Kilo Code  │  Open WebUI  │  OpenAI SDK / curl       │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ POST /v1/chat/completions
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     FastAPI Proxy Server                             │
│  • Auth / request validation / readiness gate                        │
│  • Parses request-time routing preferences                           │
│  • Performs bounded failover across candidate models                 │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        Routing Engine                                │
│  • Reads normalized model rows                                       │
│  • Filters by tools / vision / streaming / structured output         │
│  • Applies dynamic preference overrides                              │
└───────────────┬───────────────────────────┬──────────────────────────┘
                │                           │
                │ read                      │ call provider adapter
                ▼                           ▼
┌──────────────────────────────┐   ┌──────────────────────────────────┐
│ SQLite (read connections)    │   │ Provider Registry                 │
│ models / request_log / cache │   │  • openrouter adapter (v1)        │
└───────────────┬──────────────┘   │  • optional future providers      │
                │                  └─────────────────┬────────────────┘
                │ enqueue writes                     │
                ▼                                    ▼
┌──────────────────────────────┐        ┌──────────────────────────────┐
│ Dedicated DB Writer Thread   │        │ External APIs                 │
│ sole owner of write conn     │        │ OpenRouter `/models` +        │
│ WAL / migrations / log batch │        │ `/chat/completions`           │
└──────────────────────────────┘        └──────────────────────────────┘
```

The first shipping provider is **OpenRouter only**. The spec remains provider-agnostic by keeping the provider boundary strict and normalized model metadata stable.
### 2.2 Request Lifecycle

The following describes the complete lifecycle of a single chat completion request through the gateway:

1. A client sends a `POST /v1/chat/completions` request to the gateway, optionally with `model: "auto"` or any model name.
2. The Proxy Server validates the request format and API key (if authentication is enabled).
3. The Routing Engine queries the SQLite database for the current ranked list of healthy models. If the client specified a concrete model name that exists in the pool, that model is used directly. If the client specified `"auto"` or a model name not in the pool, the top-ranked healthy model is selected.
4. If the selected model requires tool-calling support and the top-ranked model does not support it, the Routing Engine selects the highest-ranked model that does support it.
5. The Proxy Server forwards the request to the selected model's provider endpoint, translating the request to the provider's expected format if necessary (LiteLLM handles this translation automatically).
6. The response (streaming or non-streaming) is relayed back to the client verbatim.
7. The Proxy Server logs the request metadata (timestamp, selected model, provider, latency, token counts, success/failure) to the SQLite `request_log` table asynchronously.

### 2.3 Subsystem Interaction & Scheduling

The gateway uses FastAPI lifespan startup plus APScheduler recurring jobs. Startup is split into **bootstrap** and **steady-state** phases.

### Bootstrap phase (must complete before readiness)

1. Initialize SQLite and apply schema migrations.
2. Start the dedicated DB writer thread.
3. Load config and register enabled providers.
4. Run discovery once across enabled provider adapters.
5. Recompute ranking.
6. Run a tiny startup health pass on the configured fallback model and the top-ranked models (budget-capped).
7. Mark the app **ready** only if at least one routable model exists.
8. Start recurring scheduler jobs.

### Steady-state phase

| Subsystem | Trigger | Default Interval | Writes To | Reads From |
|---|---|---|---|---|
| Bootstrap controller | Startup only | Once | app state, `models`, `config_overrides` | provider adapters, SQLite |
| Discovery orchestrator | Startup + schedule | Every 60 minutes | `models` table | provider discovery adapters, leaderboard sources |
| Ranking engine | After each discovery run | On-demand | `models` table (score columns) | `models`, `request_log`, `leaderboard_cache` |
| Health monitor | Startup + schedule | Every 180 minutes | `models` table, `request_log` | `models`, `request_log`, provider probe policies |
| Proxy server | Per HTTP request | Real-time | `request_log`, `models` health signals | `models`, `config_overrides` |
| DB writer thread | Continuous while app is running | Event-driven | all DB writes | write queue |

Additional scheduler requirements:

- All recurring jobs must use `max_instances=1` and `coalesce=True`.
- Job bodies may read directly from SQLite, but any mutation must go through the DB writer queue.
- Discovery or health failures must degrade gracefully: the app stays live, but readiness may flip to `false` if no routable models remain.
## 3. Data Models

All persistent state is stored in a single SQLite database file (`gateway.db`). The schema is managed via a simple migration script (`src/db.py`) that creates tables on first run.

### 3.0 `schema_migrations` Table (Schema Evolution)

The gateway must support **forward-only SQLite schema migrations**. The database is persistent
and will outlive containers; therefore, schema changes must be applied safely on startup.

A minimal migrations mechanism is required:

- Create a `schema_migrations` table.
- Each migration is an idempotent function that applies one version bump.
- On startup, `init_db()` applies all pending migrations in order inside a transaction.

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
```

Recommended timestamp format: UTC ISO 8601 with Z suffix, e.g. `2026-03-02T23:15:00Z`.

---

### 3.1 `models` Table

This is the central normalized model table. Every routable model endpoint discovered from any enabled provider adapter is represented as one row.

```sql
CREATE TABLE IF NOT EXISTS models (
    -- Identity
    id                    TEXT PRIMARY KEY,        -- Canonical stable ID. Format: "{provider_id}/{provider_model_id}" or "{provider_id}/{provider_model_id}@{endpoint_id}" when endpoint_id is present.
    name                  TEXT NOT NULL,           -- Human-readable display name
    provider_id           TEXT NOT NULL,           -- Registered provider adapter name, e.g. "openrouter"
    endpoint_id           TEXT DEFAULT NULL,       -- Optional sub-endpoint identifier for future multi-endpoint providers
    provider_model_id     TEXT NOT NULL,           -- Model slug as expected by the provider adapter
    provider_base_url     TEXT NOT NULL,           -- Base URL used by the provider adapter
    provider_api_key_env  TEXT NOT NULL,           -- Environment variable name containing auth credentials
    provider_options_json TEXT DEFAULT NULL,       -- Adapter-owned immutable hints (JSON string)

    -- Capabilities
    context_window            INTEGER DEFAULT 4096,
    max_output_tokens         INTEGER DEFAULT NULL,
    tokenizer_family          TEXT DEFAULT NULL,   -- e.g. "Llama3", "cl100k_base", adapter-provided hint
    supports_tools            INTEGER DEFAULT 0,
    supports_streaming        INTEGER DEFAULT 1,
    supports_vision           INTEGER DEFAULT 0,
    supports_structured_output INTEGER DEFAULT 0,
    supports_system_messages  INTEGER DEFAULT 1,

    -- Benchmark & cold-start popularity hints
    chatbot_arena_elo     REAL DEFAULT NULL,
    open_llm_score        REAL DEFAULT NULL,
    openrouter_rank       INTEGER DEFAULT NULL,   -- Deprecated name retained for compatibility; treated as an optional adapter-supplied cold-start popularity hint only

    -- Health & performance
    is_healthy            INTEGER DEFAULT 1,
    last_health_check     TEXT DEFAULT NULL,
    avg_latency_ms        REAL DEFAULT NULL,
    avg_ttfb_ms           REAL DEFAULT NULL,
    consecutive_failures  INTEGER DEFAULT 0,
    backoff_level         INTEGER DEFAULT 0,
    cooldown_until        TEXT DEFAULT NULL,
    last_error            TEXT DEFAULT NULL,

    -- Adaptive health probing state
    last_probe_at         TEXT DEFAULT NULL,
    last_success_at       TEXT DEFAULT NULL,
    last_failure_at       TEXT DEFAULT NULL,
    last_routed_at        TEXT DEFAULT NULL,

    -- Composite ranking
    composite_score       REAL DEFAULT 0.0,
    score_updated_at      TEXT DEFAULT NULL,

    -- Metadata
    discovered_at         TEXT NOT NULL,
    last_seen_at          TEXT NOT NULL,
    is_active             INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_models_routing
    ON models (is_active, is_healthy, cooldown_until, composite_score DESC);

CREATE INDEX IF NOT EXISTS idx_models_provider_active
    ON models (provider_id, is_active, last_seen_at DESC);

-- NOTE: SQLite partial indexes cannot reference non-deterministic functions like datetime('now').
-- Routing queries MUST apply the cooldown filter at query time using a bound parameter:
--   WHERE is_active=1 AND is_healthy=1 AND (cooldown_until IS NULL OR cooldown_until < :now_iso)
```

**Timestamp rule:** all TEXT timestamps in this spec use UTC ISO 8601 with a `Z` suffix and must be emitted in one canonical format only.
### 3.2 `request_log` Table

Stores request telemetry used for observability, failover analysis, ranking inputs, and probe-budget accounting.

```sql
CREATE TABLE IF NOT EXISTS request_log (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id            TEXT DEFAULT NULL,      -- Correlates retries / streaming completion logs for one client request
    timestamp             TEXT NOT NULL,
    request_source        TEXT NOT NULL DEFAULT 'client', -- client | probe | bootstrap
    selected_model_id     TEXT NOT NULL,
    provider_id           TEXT NOT NULL,
    client_requested_model TEXT DEFAULT NULL,
    attempt_index         INTEGER DEFAULT 0,
    was_fallback          INTEGER DEFAULT 0,

    prompt_tokens         INTEGER DEFAULT NULL,
    completion_tokens     INTEGER DEFAULT NULL,
    total_tokens          INTEGER DEFAULT NULL,
    latency_ms            REAL DEFAULT NULL,
    ttfb_ms               REAL DEFAULT NULL,

    success               INTEGER DEFAULT 1,
    gateway_error_category TEXT DEFAULT NULL,
    error_code            TEXT DEFAULT NULL,
    error_message         TEXT DEFAULT NULL,

    was_streaming         INTEGER DEFAULT 0,
    had_tools             INTEGER DEFAULT 0,
    had_vision            INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_request_log_timestamp ON request_log (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_request_log_model ON request_log (selected_model_id);
CREATE INDEX IF NOT EXISTS idx_request_log_provider_day ON request_log (provider_id, request_source, timestamp DESC);
```
### 3.3 `leaderboard_cache` Table

Leaderboard data (Chatbot Arena ELO scores, Open LLM Leaderboard scores) is fetched and cached here to avoid repeated scraping. The cache is invalidated after 24 hours.

```sql
CREATE TABLE IF NOT EXISTS leaderboard_cache (
    model_name_normalized TEXT PRIMARY KEY,     -- Normalized model name for fuzzy matching (lowercase, no special chars)
    chatbot_arena_elo     REAL DEFAULT NULL,
    open_llm_avg_score    REAL DEFAULT NULL,
    fetched_at            TEXT NOT NULL          -- ISO 8601 timestamp; invalidate if older than 24 hours
);
```

### 3.4 `config_overrides` Table

Allows runtime configuration changes without restarting the server. The application reads this table at startup and periodically refreshes it.

```sql
CREATE TABLE IF NOT EXISTS config_overrides (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- Example rows:
-- ('ranking.weight.benchmark_score', '0.35', '2026-03-01T00:00:00Z')
-- ('health.probe_interval_minutes', '15', '2026-03-01T00:00:00Z')
-- ('routing.fallback_model', 'openrouter/meta-llama/llama-3.3-70b-instruct:free', '2026-03-01T00:00:00Z')
```

---

## 4. External API Contracts

This section documents the external APIs the gateway consumes. All HTTP calls must include appropriate error handling, retries with exponential backoff (max 3 retries), and timeouts (default 15 seconds).

### 4.1 OpenRouter — Model Discovery

**Endpoint:** `GET https://openrouter.ai/api/v1/models`  
**Authentication:** None required for model listing; `Authorization: Bearer <OPENROUTER_API_KEY>` required for inference  
**Contract role in this spec:** primary discovery source for the initial implementation

**Response fields relied on by the adapter:**
- `id`
- `name`
- `context_length`
- `pricing.prompt`
- `pricing.completion`
- `architecture.tokenizer` (if present)
- `top_provider.max_completion_tokens` (if present)
- `supported_parameters`

**Filtering logic:**
- Include a model only if it is genuinely free.
- Primary check: `pricing.prompt == "0"` AND `pricing.completion == "0"`.
- Secondary hint: a `:free` suffix may be present, but pricing fields remain authoritative.

**Capability extraction:**
- `supports_tools`: `"tools"` in `supported_parameters`
- `supports_streaming`: `"stream"` in `supported_parameters`
- `supports_structured_output`: adapter best-effort from `supported_parameters` / known model features
- `supports_vision`: input modality includes image or adapter-known multimodal model family
- `supports_system_messages`: default `true` unless the adapter knows otherwise
- `context_window`: `context_length`
- `max_output_tokens`: `top_provider.max_completion_tokens` if present
- `tokenizer_family`: `architecture.tokenizer` if present

**Cold-start popularity hint:**
The adapter may populate `openrouter_rank` using the model's index position in the `/models` response array as a weak heuristic only. The gateway must not depend on any undocumented OpenRouter rankings endpoint.
### 4.2 OpenRouter — Inference, Streaming & Limits

**Inference endpoint:** `POST https://openrouter.ai/api/v1/chat/completions`  
**Protocol:** OpenAI-compatible chat completions API with optional SSE streaming

**Important contract assumptions:**
- Requests are sent in OpenAI chat format.
- `stream: true` produces SSE frames and terminates with `data: [DONE]`.
- OpenRouter performs provider-side fallbacks transparently when one of its upstream providers errors.
- Free-model usage is rate-limited and must be treated as a shared budget for both real requests and active probes.

**Operational guidance for this spec:**
- Recommended fallback model ID: `openrouter/openrouter/free` if discovered.
- Do not depend on undocumented popularity or rankings endpoints.
- Keep active probing extremely conservative because probe traffic consumes the same free-tier budget as user traffic.

### 4.3 Chatbot Arena — ELO Scores

**Source:** Chatbot Arena public leaderboard / dataset snapshots  
**Authentication:** None  
**Use in this spec:** optional benchmark signal only

The ranking engine uses Chatbot Arena ELO data as one benchmark input when fuzzy matching succeeds. Missing benchmark data must not disqualify a model; weights are redistributed among the remaining available factors.

### 4.4 LiteLLM — Provider Client Behavior

LiteLLM is used as a **helper library inside provider adapters**, not as the gateway's public abstraction layer.

In this specification:
- The `openrouter` provider adapter may call LiteLLM using model names like `openrouter/<provider_model_id>`.
- Streaming is enabled by passing `stream=True`.
- LiteLLM exception classes are normalized by the adapter into gateway error categories.
- Future providers may use LiteLLM **or** bypass it entirely if direct HTTP is simpler or more reliable.

This separation keeps the gateway provider-agnostic even if LiteLLM behavior changes for a particular backend.

### 4.5 Gateway's Own Admin API

The gateway exposes two classes of health endpoints:

- **Unauthenticated infrastructure endpoints**
  - `GET /healthz` → liveness only
  - `GET /readyz` → readiness only
- **Authenticated diagnostic endpoints**
  - `GET /admin/health` → rich operational status, queue depth, provider summaries, probe budgets, and recent job outcomes

This split is intentional: container orchestrators should not need administrative credentials just to perform liveness checks.

# Intelligent LLM Gateway — Project Specification (Part 2 of 3)
## Module Specifications & Client Integrations

**Document Version:** 1.2  
**Date:** March 3, 2026  
**Author:** OpenAI + Manus AI working draft, consolidated and corrected  
**Status:** Ready for Agent Handoff

---

## Module-by-Module Implementation Specifications

**Document Version:** 1.1  
**Date:** March 3, 2026  
**Author:** OpenAI + prior working draft

---

## 5. Module Specifications

This section specifies each Python module in the `src/` directory in detail. Each module specification includes its purpose, public interface (functions/classes), internal logic, error handling requirements, and relevant implementation notes.

### 5.1 `src/db.py` — Database Layer

**Purpose:** Provides all database access for the application. All other modules must interact with SQLite exclusively through this module. No other module should import `sqlite3` directly.

**Dependencies:** `sqlite3` (stdlib), `queue` (stdlib), `threading` (stdlib), `contextlib` (stdlib), `datetime` (stdlib)

**Public Interface:**

```python
def init_db(db_path: str = "gateway.db") -> None:
    """
    Initializes SQLite and applies forward-only schema migrations.
    Must run synchronously during startup before the writer thread begins.
    """

def start_writer() -> None:
    """Starts the dedicated DB writer thread and its sole write connection."""

def stop_writer(flush_timeout_seconds: float = 5.0) -> None:
    """Stops the writer thread after draining the queue (best effort)."""

def flush_writes(timeout_seconds: float = 5.0) -> None:
    """Blocks until queued writes are flushed or timeout is reached."""

def get_connection() -> sqlite3.Connection:
    """
    Returns a thread-local read connection with row_factory = sqlite3.Row.
    Callers must treat it as read-mostly; mutating SQL outside db.py is forbidden.
    """

def upsert_model(model: dict, wait: bool = False) -> None:
    """Enqueues an insert/update for one normalized model row."""

def mark_models_not_seen(provider_id: str, seen_ids: list[str], wait: bool = False) -> int:
    """Marks active models from one provider inactive if absent from the latest discovery run."""

def get_routable_models(
    require_tools: bool = False,
    require_vision: bool = False,
    require_structured_output: bool = False,
    require_streaming: bool = False,
    require_system_messages: bool = True,
    min_context_tokens: int = 0,
    min_output_tokens: int = 0,
    now_iso: str | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    """
    Returns models eligible for routing, ordered by composite_score DESC.
    Cooldown expiration MUST be evaluated using a bound now_iso parameter.
    """

def get_all_models() -> list[sqlite3.Row]:
    """Returns all models regardless of health or active status."""

def get_model_by_id(model_id: str) -> sqlite3.Row | None:
    """Returns one model row by primary key."""

def update_model_health(
    model_id: str,
    is_healthy: bool,
    latency_ms: float | None,
    ttfb_ms: float | None,
    error_message: str | None = None,
) -> None:
    """
    Enqueues a health update.
    On failure, increments consecutive_failures and applies exponential cooldown backoff.
    On success, resets failures/backoff and updates latency EMA + TTFB EMA.
    """

def record_model_routed(model_id: str) -> None:
    """Enqueues last_routed_at update."""

def record_model_success(model_id: str) -> None:
    """Enqueues last_success_at update."""

def record_model_failure(model_id: str, error_message: str | None = None) -> None:
    """Enqueues last_failure_at / last_error update."""

def update_model_score(model_id: str, composite_score: float, wait: bool = False) -> None:
    """Enqueues score update."""

def log_request(entry: dict) -> None:
    """
    Enqueues a request_log row insert.
    This path must be non-blocking for ordinary request traffic.
    """

def get_recent_logs(limit: int = 100, offset: int = 0) -> list[sqlite3.Row]:
    """Returns recent request log rows, newest first."""

def get_provider_probe_usage(provider_id: str, utc_day: str) -> int:
    """Returns count of probe/bootstrap requests already consumed today for one provider."""

def get_leaderboard_cache(model_name_normalized: str) -> sqlite3.Row | None:
    """Returns a cached leaderboard entry if present and still fresh."""

def upsert_leaderboard_cache(model_name_normalized: str, elo: float | None, open_llm: float | None, wait: bool = False) -> None:
    """Enqueues leaderboard cache update."""

def prune_old_logs(days: int = 30) -> int:
    """Deletes old request_log rows. Called by maintenance/admin workflows."""

def get_config(key: str, default: str | None = None) -> str | None:
    """Reads a config override from config_overrides."""

def set_config(key: str, value: str, wait: bool = False) -> None:
    """Enqueues a config override write."""
```

**Implementation Notes:**

#### Migration Strategy

Because `/data/gateway.db` is persistent across container upgrades, the gateway MUST support schema upgrades without deleting the database.

- Maintain a monotonically increasing integer `DB_SCHEMA_VERSION`.
- Store applied versions in `schema_migrations`.
- Each migration is a deterministic function `migrate_to_vN(conn)`.
- Use `ALTER TABLE ... ADD COLUMN` for additive changes.
- Use `DROP INDEX IF EXISTS ...` + `CREATE INDEX ...` for index changes.
- Startup migrations run **before** the writer thread begins.

Example additive migration for this patch set:

```sql
ALTER TABLE models ADD COLUMN endpoint_id TEXT DEFAULT NULL;
ALTER TABLE models ADD COLUMN provider_options_json TEXT DEFAULT NULL;
ALTER TABLE models ADD COLUMN max_output_tokens INTEGER DEFAULT NULL;
ALTER TABLE models ADD COLUMN tokenizer_family TEXT DEFAULT NULL;
ALTER TABLE models ADD COLUMN supports_structured_output INTEGER DEFAULT 0;
ALTER TABLE models ADD COLUMN supports_system_messages INTEGER DEFAULT 1;
ALTER TABLE models ADD COLUMN avg_ttfb_ms REAL DEFAULT NULL;
ALTER TABLE models ADD COLUMN backoff_level INTEGER DEFAULT 0;
ALTER TABLE models ADD COLUMN last_error TEXT DEFAULT NULL;

ALTER TABLE request_log ADD COLUMN request_id TEXT DEFAULT NULL;
ALTER TABLE request_log ADD COLUMN request_source TEXT NOT NULL DEFAULT 'client';
ALTER TABLE request_log ADD COLUMN provider_id TEXT DEFAULT NULL;
ALTER TABLE request_log ADD COLUMN attempt_index INTEGER DEFAULT 0;
ALTER TABLE request_log ADD COLUMN was_fallback INTEGER DEFAULT 0;
ALTER TABLE request_log ADD COLUMN gateway_error_category TEXT DEFAULT NULL;
ALTER TABLE request_log ADD COLUMN had_vision INTEGER DEFAULT 0;

DROP INDEX IF EXISTS idx_models_routing;
CREATE INDEX IF NOT EXISTS idx_models_routing
    ON models (is_active, is_healthy, cooldown_until, composite_score DESC);
```

#### Dedicated Database Writer Pattern (authoritative)

After startup, **all writes** must flow through a single writer thread that owns the sole write connection.

- The writer consumes a bounded in-process queue.
- Reads use thread-local read connections.
- `log_request()` should be batch-friendly and may drop oldest low-priority log entries only if the queue is fully saturated; metadata/config writes must not be dropped.
- Rare strong-consistency operations (bootstrap, manual admin refresh, enable/disable model) may call write helpers with `wait=True` and then `flush_writes()`.

This is the final authoritative write model for the project; no module other than `db.py` may perform direct mutating SQL.
### 5.2 `src/discover.py` — Discovery Orchestrator & Provider Plugins

**Purpose:** Coordinates provider discovery plugins, normalizes discovered models, joins benchmark data, and writes provider-neutral model rows into SQLite.

**Dependencies:** `aiohttp`, `asyncio`, `rapidfuzz`, `src/db.py`, `src/config.py`, `src.providers.registry`, `src.ranking.py`

**Public Interface:**

```python
async def run_discovery() -> dict:
    """
    Runs all enabled provider discovery adapters concurrently, joins leaderboard data,
    upserts models, marks stale rows inactive provider-by-provider, recomputes scores,
    and returns a summary.
    """

def normalize_model_name(name: str) -> str:
    """Normalizes names for fuzzy leaderboard matching."""

def match_leaderboard_score(model_name: str, leaderboard: dict[str, dict], threshold: float = 85.0) -> dict | None:
    """Returns benchmark scores when fuzzy match confidence is above threshold."""
```

#### Provider plugin architecture

Provider-specific logic lives under `src/providers/`.

```python
class ProviderInference(Protocol):
    provider_id: str
    async def chat_completions(self, model: sqlite3.Row, body: dict) -> object: ...
    async def stream_chat_completions(self, model: sqlite3.Row, body: dict) -> AsyncIterator[bytes]: ...
    def normalize_error(self, exc: Exception) -> str: ...

class ProviderDiscovery(Protocol):
    provider_id: str
    async def list_models(self, session: aiohttp.ClientSession) -> list[dict]: ...

class ProviderProbePolicy(Protocol):
    provider_id: str
    def select_probe_candidates(self, models: list[sqlite3.Row], now_iso: str, budget_remaining: int) -> list[sqlite3.Row]: ...
    async def probe(self, session: aiohttp.ClientSession, model: sqlite3.Row) -> tuple[bool, float | None, float | None, str | None]: ...
```

`src/providers/registry.py` owns registration and lookup:

```python
def register_provider(provider: object) -> None: ...
def get_provider(provider_id: str) -> ProviderInference: ...
def get_enabled_discovery_providers() -> list[ProviderDiscovery]: ...
def get_probe_policy(provider_id: str) -> ProviderProbePolicy | None: ...
```

#### Initial provider implementation

The initial production build includes exactly one provider adapter:

- `src/providers/openrouter.py`

Future providers are added by implementing the interfaces above and registering them. No proxy/routing/health code changes should be required.

#### Detailed `run_discovery()` logic

1. Load enabled providers from config.
2. Create one `aiohttp.ClientSession` with sane timeouts.
3. Run all enabled discovery adapters concurrently.
4. Fetch benchmark sources concurrently.
5. For each normalized model dict returned by an adapter:
   - attach benchmark scores if matched
   - upsert the row via `db.upsert_model(..., wait=False)`
6. For each provider independently, mark rows not seen in this run inactive via `db.mark_models_not_seen(provider_id, seen_ids)`.
7. Flush writes.
8. Recompute composite scores across active models.
9. Return a summary with counts by provider and any non-fatal errors.

#### Normalized model dict format

Every discovery adapter must emit this format:

```python
{
    "id": str,
    "name": str,
    "provider_id": str,
    "endpoint_id": str | None,
    "provider_model_id": str,
    "provider_base_url": str,
    "provider_api_key_env": str,
    "provider_options_json": str | None,
    "context_window": int,
    "max_output_tokens": int | None,
    "tokenizer_family": str | None,
    "supports_tools": bool,
    "supports_streaming": bool,
    "supports_vision": bool,
    "supports_structured_output": bool,
    "supports_system_messages": bool,
    "chatbot_arena_elo": float | None,
    "open_llm_score": float | None,
    "openrouter_rank": int | None,
}
```

#### OpenRouter adapter notes

The OpenRouter adapter:
- discovers models from `/api/v1/models`
- filters to free models using pricing fields
- extracts `tokenizer_family` and `max_output_tokens` when present
- may set `openrouter_rank` from response order only as a cold-start hint
- uses `provider_model_id` exactly as OpenRouter expects it for inference
### 5.3 `src/ranking.py` — Ranking Engine

**Purpose:** Computes a composite score for each active model and writes it back to the DB.

**Dependencies:** `math` (stdlib), `statistics` (stdlib), `src/db.py`, `src/config.py`

**Public Interface:**

```python
def compute_score(model: sqlite3.Row, global_stats: dict) -> float:
    """Computes one model's composite score from normalized factors."""

def compute_all_scores() -> int:
    """Recomputes scores for all active models. Returns count updated."""
```

**Default weights:**

```python
{
    "benchmark_score": 0.30,
    "real_world_usage": 0.15,
    "latency": 0.20,
    "availability": 0.20,
    "context_window": 0.10,
    "feature_support": 0.05,
}
```

**Factor computation details:**

- **`benchmark_score`** — blended Chatbot Arena / Open LLM data when available.
- **`real_world_usage`** — derived from the gateway's own trailing-7-day telemetry first: request share, success ratio, and low 429 ratio. If there is insufficient telemetry, fall back to the adapter-supplied cold-start hint (`openrouter_rank`). If neither exists, use a neutral score of `0.5`.
- **`latency`** — derived primarily from `avg_ttfb_ms`, then `avg_latency_ms` if TTFB is unknown. Lower is better.
- **`availability`** — derived from recent success history, consecutive failures, cooldown state, and backoff level.
- **`context_window`** — normalized across active models; larger is better.
- **`feature_support`** — additive bonus from tools, vision, structured output, and streaming support.

**Weight redistribution:** if one factor is unavailable for a model, its weight is redistributed proportionally across the remaining factors.

This design removes the gateway's dependency on undocumented third-party popularity endpoints while still allowing a provider-specific cold-start hint to help the first few requests.
### 5.4 `src/health.py` — Health Monitor

**Purpose:** Maintains a high-quality routing pool while minimizing probe spend. Health signals come from:

1. **Passive health signals (primary)** — real user traffic via the proxy.
2. **Active probes (secondary)** — tiny synthetic requests used sparingly for startup validation and cooldown recovery.

**Dependencies:** `aiohttp`, `asyncio`, `datetime`, `src/db.py`, `src/config.py`, `src.providers.registry`

**Public Interface:**

```python
async def run_health_checks() -> dict:
    """
    Runs an adaptive, provider-budget-aware health pass.
    Returns counts for considered, probed, recovered, failed, and skipped models.
    """

async def bootstrap_health_check() -> dict:
    """
    Tiny startup validation pass.
    Probes only the configured fallback model (if present) and the top-ranked models,
    capped by health.startup_probe_limit.
    """
```

#### Health rules

- Passive traffic is the primary signal. If a model is serving real requests successfully, it should rarely be probed.
- Probes are selected only from providers that expose a `ProviderProbePolicy` and still have remaining daily budget.
- Probe results update both `avg_latency_ms` and `avg_ttfb_ms`.
- Repeated failures trigger exponential cooldown backoff:
  - `cooldown = base_cooldown_minutes * (2 ** min(backoff_level, max_backoff_exponent))`
- Success resets `consecutive_failures`, `backoff_level`, and cooldown.
- Rate-limited models should not be hammered; active probing must respect `daily_request_budget_by_provider`.

#### Default selection order

1. Models exiting cooldown
2. Never-probed models (bounded)
3. Stale top-ranked models with no recent success signal
4. Optional random exploration sample (disabled by default)

#### OpenRouter defaults (conservative)

Because free-model requests share the same budget as real user traffic, the default OpenRouter probe settings are intentionally conservative:

- `probe_interval_minutes: 180`
- `max_probes_per_run: 1`
- `startup_probe_limit: 2`
- `daily_request_budget_by_provider.openrouter: 5`
- `probe_max_tokens: 1`

The health monitor must use `request_log.request_source in ('probe', 'bootstrap')` to account for daily probe usage.
### 5.5 `src/routing.py` — Routing Engine

**Purpose:** Selects the best model for each incoming request using normalized DB metadata only.

**Dependencies:** `dataclasses` (stdlib), `src/db.py`, `src/config.py`

**Public Interface:**

```python
@dataclass
class RoutingPreferences:
    preference: str = "balanced"   # balanced | quality | latency | context | reliability
    max_latency_ms: int | None = None
    min_context_tokens: int | None = None

def select_model(
    requested_model: str,
    require_tools: bool = False,
    require_vision: bool = False,
    require_structured_output: bool = False,
    require_streaming: bool = False,
    require_system_messages: bool = True,
    min_context_tokens: int = 0,
    min_output_tokens: int = 0,
    preferences: RoutingPreferences | None = None,
) -> sqlite3.Row:
    """Returns the best routable model or raises NoHealthyModelsError."""

def get_candidate_models(
    requested_model: str,
    require_tools: bool = False,
    require_vision: bool = False,
    require_structured_output: bool = False,
    require_streaming: bool = False,
    require_system_messages: bool = True,
    min_context_tokens: int = 0,
    min_output_tokens: int = 0,
    preferences: RoutingPreferences | None = None,
    max_attempts: int = 3,
) -> list[sqlite3.Row]:
    """
    Returns an ordered candidate list for bounded failover.
    Candidate 0 is the preferred model; the remaining entries are alternates.
    """

class NoHealthyModelsError(Exception):
    """Raised when no healthy, capability-compatible models are available."""
```

**Capability filters are first-class and mandatory:**
- tools support
- vision support
- streaming support
- structured output support
- system-message support
- minimum context window
- minimum output-token capacity

**Request-time preference overrides**

The routing engine accepts a `RoutingPreferences` object derived from optional request headers:

- `X-Gateway-Preference: balanced|quality|latency|context|reliability`
- `X-Gateway-Max-Latency-Ms: <integer>`
- `X-Gateway-Min-Context: <integer>`

These preferences do **not** bypass capability filters. They only re-rank otherwise eligible candidates.

**Fallback behavior**

- If the configured fallback model is active and routable, include it in the candidate set even if its score is low.
- Recommended default fallback: `openrouter/openrouter/free` if discovered.
- If no models remain after capability filtering, raise `NoHealthyModelsError`.

**Model name passthrough**

If the client requests a model name that does not exist in the pool, the gateway treats it as `auto` and selects the best compatible candidate. If the name matches a known model with a `:free` suffix appended, that row may be selected directly.
### 5.6 `src/proxy.py` — Proxy Server

**Purpose:** The main FastAPI application. Accepts OpenAI-compatible HTTP requests, delegates model selection to the Routing Engine, forwards requests through the selected provider adapter, and returns responses to the client.

**Dependencies:** `fastapi`, `uvicorn`, `src.routing`, `src.db`, `src.config`, `src.providers.registry`, `src.tokens`

**Endpoints:**

```python
# Main proxy endpoint — OpenAI-compatible
POST /v1/chat/completions

# Infrastructure health endpoints (unauthenticated)
GET  /healthz
GET  /readyz

# Pass-through / convenience endpoints
GET  /v1/models
POST /v1/embeddings      # Future roadmap item
POST /v1/completions     # Legacy compatibility, lower priority

# Admin endpoints (protected by API key)
GET  /admin/models
GET  /admin/models/{id}
POST /admin/models/{id}/disable
POST /admin/models/{id}/enable
GET  /admin/health
POST /admin/refresh
GET  /admin/logs
```

#### 5.6.1 Readiness gating

`/v1/chat/completions` must return **503 Service Unavailable** with `Retry-After` when `app.state.ready == False`.

Ready means all of the following are true:
- migrations completed
- config loaded
- writer thread running
- bootstrap discovery completed successfully
- at least one routable model exists

#### 5.6.2 Per-request multi-model fallback

The proxy MUST NOT fail immediately on the first retryable provider error.

Algorithm:
1. Parse request-time routing preferences from headers.
2. Estimate token requirements using `src.tokens` with the best available tokenizer hint and a 15% safety buffer.
3. Build a ranked candidate list via `routing.get_candidate_models()`.
4. Attempt each candidate in order until success or `routing.max_attempts` is exhausted.
5. Penalize health only for retryable provider-origin failures.
6. Return the first successful response or a final 502 if all candidates fail.

**Retryable gateway categories:**
- `RATE_LIMITED`
- `PROVIDER_UNAVAILABLE`
- `CONTEXT_EXCEEDED` (only retryable if a larger-context alternate exists)

**Non-retryable gateway categories:**
- `INVALID_REQUEST`
- `AUTH_ERROR`

#### 5.6.3 Streaming relay hardening

Streaming behavior must follow these rules:

- Preserve OpenAI-compatible SSE framing.
- Do not double-wrap upstream SSE frames.
- Detect client disconnect and cancel upstream generation promptly.
- Measure and store TTFB separately from total latency.
- Fail over to the next candidate **only if the upstream provider fails before the first response bytes are sent**.
- Once the first SSE chunk has been emitted to the client, no further gateway failover is allowed.
- On successful completion, emit terminal `data: [DONE]` if the upstream protocol does not already provide it.

#### 5.6.4 Unified error taxonomy

All provider-origin exceptions must be normalized into gateway categories before routing logic handles them:

- `RATE_LIMITED`
- `PROVIDER_UNAVAILABLE`
- `INVALID_REQUEST`
- `AUTH_ERROR`
- `CONTEXT_EXCEEDED`

Adapters perform the provider-specific mapping. The proxy only understands the normalized categories.

#### 5.6.5 Main request implementation (authoritative pseudocode)

```python
@app.post("/v1/chat/completions")
async def chat_completions(request: Request, body: ChatCompletionRequest):
    if not app.state.ready:
        raise HTTPException(status_code=503, detail="Gateway not ready", headers={"Retry-After": "10"})

    authenticate(request)

    preferences = parse_routing_preferences(request.headers)
    require_tools = bool(body.tools)
    require_vision = request_contains_images(body)
    require_structured_output = request_requires_structured_output(body)
    require_streaming = bool(body.stream)
    require_system_messages = True

    min_context_tokens = estimate_required_tokens(body.messages, requested_model=body.model)
    min_output_tokens = body.max_tokens or body.max_completion_tokens or 0

    candidates = routing.get_candidate_models(
        requested_model=body.model or "auto",
        require_tools=require_tools,
        require_vision=require_vision,
        require_structured_output=require_structured_output,
        require_streaming=require_streaming,
        require_system_messages=require_system_messages,
        min_context_tokens=min_context_tokens,
        min_output_tokens=min_output_tokens,
        preferences=preferences,
        max_attempts=int(cfg.get("routing.max_attempts", 3)),
    )

    request_id = make_request_id()
    last_error = None

    for attempt_index, model in enumerate(candidates):
        provider = registry.get_provider(model["provider_id"])
        db.record_model_routed(model["id"])

        try:
            if body.stream:
                return stream_via_provider(provider, model, body, request_id, attempt_index)

            response, latency_ms, ttfb_ms, usage = await provider.chat_completions(model, body)
            db.record_model_success(model["id"])
            db.update_model_health(model["id"], True, latency_ms, ttfb_ms)
            db.log_request({...})
            return response

        except Exception as exc:
            category = provider.normalize_error(exc)
            last_error = exc

            if category in ("INVALID_REQUEST", "AUTH_ERROR"):
                db.log_request({...})
                raise HTTPException(status_code=map_status(category), detail=str(exc))

            db.record_model_failure(model["id"], error_message=str(exc))
            db.update_model_health(model["id"], False, None, None, category)
            db.log_request({...})
            continue

    raise HTTPException(status_code=502, detail=f"All candidate providers failed: {last_error}")
```
### 5.7 `src/config.py` — Configuration

**Purpose:** Loads `config.yaml`, overlays runtime DB overrides, and exposes a thread-safe configuration API.

**Dependencies:** `pyyaml`, `src.db`

**Public Interface:**

```python
class Config:
    def get(self, key: str, default=None): ...
    def reload(self): ...

def get_config() -> Config: ...
```

Configuration must support:
- enabled provider registry
- ranking weights
- routing failover limits
- request-time preference header toggles
- health probe budgets and cooldown settings
- bootstrap readiness behavior
### 5.8 `src/scheduler.py` — Background Task Scheduler

**Purpose:** Owns FastAPI lifespan startup/shutdown, bootstrap sequencing, and APScheduler recurring jobs.

**Dependencies:** `apscheduler`, `src.discover`, `src.health`, `src.db`, `src.config`

```python
async def bootstrap_gateway(app: FastAPI) -> None:
    """
    Authoritative startup sequence:
    1. init_db()
    2. load config
    3. register providers
    4. start_writer()
    5. run_discovery()
    6. compute_all_scores()
    7. bootstrap_health_check()
    8. set app.state.ready = has_routable_models()
    9. start_scheduler(app)
    """

def start_scheduler(app: FastAPI) -> None:
    """
    Creates AsyncIOScheduler jobs with max_instances=1 and coalesce=True:
    - discovery: every discovery.interval_minutes
    - health: every health.probe_interval_minutes
    """
```

**Authoritative readiness behavior:**
- The app may be **live but not ready**.
- `/healthz` reports process liveness and critical thread status.
- `/readyz` reflects whether the bootstrap conditions are currently satisfied.
- Manual `/admin/refresh` reruns discovery + ranking + targeted health and then recomputes readiness.
## 6. Client Integration Specifications

This section documents exactly how each supported client platform should be configured to use the gateway. This content should appear verbatim in the project README.

### 6.1 OpenClaw

OpenClaw reads its configuration from `~/.openclaw/openclaw.json` (or the path specified by the `OPENCLAW_CONFIG` environment variable). To route through the gateway, add a custom model entry pointing to the gateway's endpoint.

```json
{
  "agents": {
    "defaults": {
      "model": {
        "primary": "auto"
      },
      "models": {
        "auto": {
          "provider": "openai",
          "baseUrl": "http://<GATEWAY_HOST>:8000/v1",
          "apiKey": "<GATEWAY_API_KEY_OR_ANY_STRING_IF_AUTH_DISABLED>"
        }
      }
    }
  }
}
```

Replace `<GATEWAY_HOST>` with the IP address or hostname of the machine running the gateway (e.g., `192.168.1.100` for a local network deployment, or `localhost` if running on the same machine). The `apiKey` field is required by OpenClaw's schema but is only validated by the gateway if `GATEWAY_API_KEY` is set.

### 6.2 Kilo Code (VS Code Extension)

Kilo Code is configured via VS Code settings. Open the VS Code settings JSON (`Ctrl+Shift+P` → "Open User Settings JSON") and add:

```json
{
  "kilocode.apiProvider": "openai",
  "kilocode.openAiBaseUrl": "http://<GATEWAY_HOST>:8000/v1",
  "kilocode.openAiApiKey": "<GATEWAY_API_KEY_OR_ANY_STRING>",
  "kilocode.openAiModelId": "auto"
}
```

Alternatively, in the Kilo Code sidebar, select **OpenAI Compatible** as the provider, enter the gateway URL as the base URL, and type `auto` as the model ID.

### 6.3 Open WebUI

In the Open WebUI admin panel, navigate to **Settings → Connections → OpenAI API**. Set:

- **API Base URL:** `http://<GATEWAY_HOST>:8000/v1`
- **API Key:** `<GATEWAY_API_KEY_OR_ANY_STRING>`

After saving, click **Refresh** to load the model list from the gateway. The `auto` model will appear in the model dropdown.

### 6.4 SillyTavern

In SillyTavern, go to **API Connections → Chat Completion → API**. Select **OpenAI** and set:

- **Reverse Proxy:** `http://<GATEWAY_HOST>:8000/v1`
- **Proxy Password:** `<GATEWAY_API_KEY_OR_LEAVE_BLANK>`

Then click **Connect** and select `auto` from the model list.

### 6.5 Any OpenAI SDK (Python)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://<GATEWAY_HOST>:8000/v1",
    api_key="<GATEWAY_API_KEY_OR_ANY_STRING>",
)

response = client.chat.completions.create(
    model="auto",  # Gateway selects the best free model
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

### 6.6 curl

```bash
curl http://<GATEWAY_HOST>:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <GATEWAY_API_KEY>" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

---

---

# Intelligent LLM Gateway — Project Specification (Part 3 of 3)
## Configuration, Docker, CI/CD & Open Source Conventions

**Document Version:** 1.1  
**Date:** March 3, 2026  
**Author:** OpenAI + prior working draft

---

## 7. Configuration Reference

### 7.1 `config.yaml` — Full Reference

The `config.yaml` file is the primary configuration file. It is mounted into the Docker container at `/app/config.yaml`. A `config.yaml.example` file with all options documented and sane defaults must be included in the repository root.

```yaml
# ============================================================
# Intelligent LLM Gateway — Configuration File
# OpenRouter-first, provider-pluggable initial release
# ============================================================

gateway:
  host: "0.0.0.0"
  port: 8000
  workers: 1                 # Keep at 1 for SQLite simplicity
  log_level: "info"
  # api_key: ""              # Optional gateway API key

providers:
  enabled:
    - openrouter

  openrouter:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
    active_probe_enabled: true
    api_base: "https://openrouter.ai/api/v1"
    api_key_env: "OPENROUTER_API_KEY"
    free_only: true
    fallback_model: "openrouter/openrouter/free"
    probe_daily_budget: 5

# Discovery orchestration
discovery:
  interval_minutes: 60
  request_timeout_seconds: 15
  leaderboard:
    chatbot_arena:
      enabled: true
      cache_hours: 24
    open_llm:
      enabled: true
      cache_hours: 24

# Routing and score composition
routing:
  max_attempts: 3
  token_estimation_safety_buffer: 0.15
  enable_request_preference_headers: true

ranking:
  weights:
    benchmark_score: 0.30
    real_world_usage: 0.15
    latency: 0.20
    availability: 0.20
    context_window: 0.10
    feature_support: 0.05
  fallback_model: "openrouter/openrouter/free"

health:
  probe_interval_minutes: 180
  probe_timeout_seconds: 15
  probe_concurrency: 1
  max_probes_per_run: 1
  stale_after_minutes: 360
  top_n_stale_probe: 3
  startup_probe_limit: 2
  consecutive_failures_threshold: 3
  cooldown_minutes: 30
  max_backoff_exponent: 4
  probe_max_tokens: 1
  daily_request_budget_by_provider:
    openrouter: 5

logging:
  request_log_enabled: true
  request_log_retention_days: 30
  log_queue_size: 5000

database:
  path: "/data/gateway.db"
  busy_timeout_ms: 5000
```
### 7.2 Environment Variables Reference

All sensitive values (API keys) must be provided via environment variables, never hardcoded in `config.yaml`.

```bash
# Required for the initial shipping provider
OPENROUTER_API_KEY=sk-or-v1-...

# Optional gateway auth key
GATEWAY_API_KEY=

# Optional path overrides
GATEWAY_CONFIG_PATH=/app/config.yaml
GATEWAY_DB_PATH=/data/gateway.db

# Optional log level override
GATEWAY_LOG_LEVEL=INFO
```
## 8. Repository Structure

The following is the canonical repository structure. Every file and directory listed here must exist in the repository.

```
llm-gateway/
│
├── .github/
│   ├── workflows/
│   │   ├── ci.yml
│   │   └── release.yml
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.md
│   │   └── feature_request.md
│   └── pull_request_template.md
│
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── db.py
│   ├── discover.py
│   ├── health.py
│   ├── proxy.py
│   ├── ranking.py
│   ├── routing.py
│   ├── scheduler.py
│   ├── tokens.py                 # Token estimation helpers + safety buffer logic
│   └── providers/
│       ├── __init__.py
│       ├── base.py               # Provider interfaces / protocols
│       ├── registry.py           # Registration + lookup of enabled providers
│       └── openrouter.py         # Initial shipping provider adapter
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_db.py
│   ├── test_discover.py
│   ├── test_ranking.py
│   ├── test_routing.py
│   ├── test_health.py
│   ├── test_proxy.py
│   ├── test_scheduler.py
│   ├── test_tokens.py
│   └── providers/
│       ├── test_registry.py
│       └── test_openrouter.py
│
├── config.yaml.example
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── docker-compose.dev.yml
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── README.md
├── CONTRIBUTING.md
├── CHANGELOG.md
├── LICENSE
└── .gitignore
```
## 9. Docker Configuration

### 9.1 `Dockerfile`

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

RUN groupadd -r gateway && useradd -r -g gateway gateway
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config.yaml.example ./config.yaml.example

RUN mkdir -p /data && chown gateway:gateway /data
USER gateway

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3     CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "src.proxy:app", "--host", "0.0.0.0", "--port", "8000"]
```
### 9.2 `docker-compose.yml`

```yaml
services:
  gateway:
    image: ghcr.io/jetymas/FreeLunch:latest
    # For local development, build from source instead:
    # build: .
    container_name: llm-gateway
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - gateway_data:/data
      - ./config.yaml:/app/config.yaml:ro
    env_file:
      - .env
    environment:
      - GATEWAY_DB_PATH=/data/gateway.db
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

volumes:
  gateway_data:
    driver: local
```
### 9.3 `docker-compose.dev.yml`

```yaml
services:
  gateway:
    build: .
    volumes:
      - ./src:/app/src
      - ./config.yaml:/app/config.yaml:ro
    environment:
      - GATEWAY_LOG_LEVEL=DEBUG
    command: >
      python -m uvicorn src.proxy:app
      --host 0.0.0.0
      --port 8000
      --reload
      --reload-dir /app/src
```
### 9.4 `requirements.txt` (Pinned Versions)

```
# Core proxy framework
litellm==1.35.0
fastapi==0.115.0
uvicorn[standard]==0.30.6

# HTTP client for discovery and health checks
aiohttp==3.10.5

# Background task scheduling
apscheduler==3.10.4

# Configuration
pyyaml==6.0.2

# Fuzzy string matching for leaderboard joining
rapidfuzz==3.9.7

# Token counting
tiktoken==0.7.0

# sqlite3 is from the standard library
```
### 9.5 `requirements-dev.txt`

```
-r requirements.txt

# Testing
pytest==8.3.3
pytest-asyncio==0.24.0
pytest-cov==5.0.0
httpx==0.27.2          # Required by FastAPI TestClient

# Linting and formatting
ruff==0.6.9

# Type checking
mypy==1.11.2
types-pyyaml==6.0.12.20240917
```

---

## 10. CI/CD Pipeline

### 10.1 `.github/workflows/ci.yml`

This workflow runs on every push to any branch and on every pull request targeting `main`.

```yaml
name: CI

on:
  push:
    branches: ["**"]
  pull_request:
    branches: [main]

jobs:
  lint-and-typecheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install ruff mypy types-pyyaml
      - run: ruff check src/ tests/
      - run: ruff format --check src/ tests/
      - run: mypy src/ --ignore-missing-imports

  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
      - run: pip install -r requirements-dev.txt
      - run: pytest tests/ --cov=src --cov-report=xml --cov-report=term-missing

  docker-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t llm-gateway:test .
      - run: |
          docker run -d --name test-gateway             -e OPENROUTER_API_KEY=test             llm-gateway:test
          sleep 10
          docker exec test-gateway python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"
```
### 10.2 `.github/workflows/release.yml`

This workflow runs when a version tag (e.g., `v1.0.0`) is pushed to the repository. It builds the Docker image and pushes it to GitHub Container Registry (GHCR).

```yaml
name: Release

on:
  push:
    tags:
      - "v*.*.*"

jobs:
  release:
    name: Build & Push Docker Image
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata for Docker
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ github.repository }}
          tags: |
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=raw,value=latest

      - name: Build and push Docker image
        uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          platforms: linux/amd64,linux/arm64  # Support both x86 and ARM (Raspberry Pi, Apple Silicon)
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          generate_release_notes: true
          files: |
            config.yaml.example
            .env.example
            docker-compose.yml
```

---

## 11. Testing Strategy

### 11.1 `tests/conftest.py` — Shared Fixtures

```python
import pytest
import sqlite3
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from src.db import init_db
from src.proxy import app

@pytest.fixture
def in_memory_db(tmp_path):
    """Provides a fresh in-memory SQLite database for each test."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    return db_path

@pytest.fixture
def test_client(in_memory_db):
    """Provides a FastAPI TestClient with a fresh database."""
    with TestClient(app) as client:
        yield client

@pytest.fixture
def mock_openrouter_response():
    """Returns a realistic mock response from the OpenRouter /models endpoint."""
    return {
        "data": [
            {
                "id": "meta-llama/llama-3.3-70b-instruct:free",
                "name": "Meta: Llama 3.3 70B Instruct (free)",
                "context_length": 131072,
                "pricing": {"prompt": "0", "completion": "0"},
                "supported_parameters": ["tools", "stream"],
            },
            {
                "id": "google/gemma-3-27b-it:free",
                "name": "Google: Gemma 3 27B (free)",
                "context_length": 96000,
                "pricing": {"prompt": "0", "completion": "0"},
                "supported_parameters": ["stream"],
            },
        ]
    }
```

### 11.2 Key Test Cases

The following test cases represent the minimum required coverage. Each must be implemented as a pytest test function.

**`tests/test_ranking.py`:**

| Test | Description |
|---|---|
| `test_score_range` | All composite scores are in [0.0, 100.0] |
| `test_weight_redistribution` | Score is still computed correctly when benchmark data is missing |
| `test_higher_elo_ranks_higher` | Model with higher ELO scores higher than one with lower ELO |
| `test_lower_latency_ranks_higher` | Model with lower latency scores higher than one with higher latency |
| `test_weights_sum_to_one` | Application raises ValueError at startup if weights do not sum to 1.0 |

**`tests/test_routing.py`:**

| Test | Description |
|---|---|
| `test_auto_selects_top_model` | `select_model("auto")` returns the model with the highest composite score |
| `test_tool_filter` | `select_model("auto", require_tools=True)` only returns models with `supports_tools=1` |
| `test_unhealthy_model_excluded` | Unhealthy models are never returned by `select_model` |
| `test_cooldown_model_excluded` | Models in cooldown are never returned by `select_model` |
| `test_explicit_model_passthrough` | `select_model("openrouter/llama-3.3-70b:free")` returns that specific model if healthy |
| `test_explicit_model_fallback` | `select_model("openrouter/llama-3.3-70b:free")` falls back to top model if that model is unhealthy |
| `test_no_healthy_models_raises` | `select_model` raises `NoHealthyModelsError` when pool is empty |

**`tests/test_health.py`:**

| Test | Description |
|---|---|
| `test_successful_probe` | Healthy endpoint sets `is_healthy=1` and records latency |
| `test_failed_probe_increments_counter` | Failed probe increments `consecutive_failures` |
| `test_cooldown_triggered` | After N consecutive failures, `cooldown_until` is set |
| `test_recovery_clears_cooldown` | Successful probe after cooldown clears `cooldown_until` and resets counter |
| `test_concurrency_limit` | Concurrency semaphore prevents more than N simultaneous probes |

**`tests/test_proxy.py`:**

| Test | Description |
|---|---|
| `test_chat_completions_returns_200` | Valid request returns 200 with OpenAI-format response |
| `test_auth_required_when_key_set` | Returns 401 when API key is configured but not provided |
| `test_auth_passes_with_correct_key` | Returns 200 when correct API key is provided |
| `test_streaming_response` | Streaming request returns `text/event-stream` content type |
| `test_no_healthy_models_returns_503` | Returns 503 when no healthy models are available |
| `test_models_endpoint` | `GET /v1/models` returns list including "auto" model |
| `test_rate_limit_triggers_health_update` | RateLimitError from provider marks model as unhealthy |

---

## 12. Open Source Conventions

### 12.1 `LICENSE`

Use the **MIT License**. This is the most permissive common license, compatible with commercial use, and the most widely understood in the developer community. The license file must include the current year and the repository owner's name.

```
MIT License

Copyright (c) 2026 <YOUR_NAME>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 12.2 `README.md` — Required Sections

The README must be comprehensive enough that a technically competent user can deploy the gateway without reading any other documentation. It must include at minimum:

1. **Project title and one-sentence description**
2. **What it does** — plain-language explanation
3. **Quick Start** — copy `.env.example`, add `OPENROUTER_API_KEY`, run `docker compose up -d`
4. **Client Configuration** — OpenClaw, Kilo Code, Open WebUI, SillyTavern, OpenAI SDK
5. **Configuration Reference** — key settings from `config.yaml.example`
6. **Architecture** — proxy, routing, provider registry, discovery, health, writer thread
7. **How Ranking Works** — benchmark + telemetry + latency + availability
8. **Health Endpoints** — `/healthz`, `/readyz`, and `/admin/health`
9. **Admin API** — model inspection and refresh endpoints
10. **Development** — tests, linting, local run instructions
11. **Roadmap** — future provider adapters, embeddings, metrics, dashboard
12. **License**
### 12.3 `CONTRIBUTING.md` — Required Content

The CONTRIBUTING.md must cover:

- **How to report a bug** — Link to the bug report issue template. Ask users to include their OS, Docker version, gateway version, and the relevant log output.
- **How to request a feature** — Link to the feature request issue template.
- **Development setup** — Step-by-step instructions for setting up a local development environment without Docker.
- **Code style** — The project uses `ruff` for linting and formatting. Run `ruff check .` and `ruff format .` before submitting a PR. The CI will fail if there are linting errors.
- **Testing** — All new features must include tests. Run `pytest tests/ --cov=src` to verify coverage. The CI enforces a minimum coverage of 80%.
- **Pull request process** — PRs must target the `main` branch, have a descriptive title, and include a description of the change and why it was made. Link to any related issues.
- **Commit message convention** — Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `chore:`, etc.

### 12.4 `pyproject.toml` — Tool Configuration

```toml
[project]
name = "llm-gateway"
version = "0.1.0"
description = "Intelligent proxy that auto-routes to the best free LLM"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.11"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "A", "C4", "SIM"]
ignore = ["E501"]  # Line length handled by formatter

[tool.mypy]
python_version = "3.11"
strict = false
ignore_missing_imports = true
warn_return_any = true
warn_unused_configs = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "--cov=src --cov-report=term-missing --cov-fail-under=80"

[tool.coverage.run]
omit = ["tests/*"]
```

### 12.5 Semantic Versioning

The project follows [Semantic Versioning 2.0.0](https://semver.org/):

- **MAJOR** version: incompatible changes to config format, DB schema, or public proxy behavior
- **MINOR** version: new backward-compatible features (new provider adapters, new ranking factors, new admin endpoints)
- **PATCH** version: backward-compatible fixes

Version tags trigger releases: `git tag v1.0.0 && git push origin v1.0.0`.

The `CHANGELOG.md` must be updated for every release using [Keep a Changelog](https://keepachangelog.com/).

### 12.6 Changelog

**Authoritative precedence rule:** The module specifications, schema definitions, and canonical file contents in this document are authoritative. The changelog is explanatory only; it does not override the current-state instructions elsewhere in the spec.

The legacy “VERSION 3 ARCHITECTURE UPDATES” appendix is superseded by the changelog below.

**2026-03-02**
- Added adaptive probing with passive-first health signals.
- Added forward-only SQLite migrations and removed invalid `datetime('now')` index logic.
- Added bounded multi-model failover and a unified error taxonomy.
- Made the dedicated DB writer pattern authoritative.

**2026-03-03**
- Removed HuggingFace from the initial shipping provider set.
- Introduced provider plugins under `src/providers/*` with an OpenRouter-first adapter.
- Added readiness gating and split `/healthz` vs `/readyz` vs `/admin/health`.
- Hardened streaming relay semantics and documented no mid-stream failover.
- Reworked ranking to prefer gateway-observed telemetry over undocumented popularity APIs.
- Added request-time routing preferences and richer capability filters.
## 13. Deployment Guide

### 13.1 VPS Deployment (Recommended)

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# 2. Clone the repository
# Replace jetymas with the actual GitHub user or org before running.
git clone https://github.com/jetymas/FreeLunch.git
cd llm-gateway

# 3. Configure the gateway
cp config.yaml.example config.yaml
cp .env.example .env
# Edit .env and set:
# OPENROUTER_API_KEY=sk-or-v1-...
# GATEWAY_API_KEY=<optional>

# 4. Start the gateway
docker compose up -d

# 5. Verify liveness then readiness
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
```
### 13.2 Local PC Deployment (Internal Network)

For local-network use, expose port 8000 on the host and point clients at `http://<LAN-IP>:8000/v1`.

- Use `/healthz` for basic process checks.
- Use `/readyz` before attaching interactive clients that expect immediate responses.
- Keep `workers: 1` unless the persistence layer is changed away from SQLite.
### 13.3 Updating the Gateway

```bash
# Pull the latest image
docker compose pull

# Restart with the new image (zero-downtime is not guaranteed with SQLite)
docker compose up -d --force-recreate

# The database volume is preserved across updates.
```

### 13.4 Accessing the Admin API

- `/healthz` and `/readyz` do not require authentication.
- `/admin/*` endpoints should require `GATEWAY_API_KEY` in production.
- `GET /admin/health` is the main diagnostic endpoint for queue depth, bootstrap state, recent scheduler outcomes, and provider summaries.
## 14. Future Roadmap

The following features are planned for future versions but are out of scope for the initial implementation described in this specification.

| Feature | Description | Priority |
|---|---|---|
| **Additional provider adapters** | Add more providers by implementing `src/providers/*` without changing routing/proxy internals | High |
| **Embedding Model Support** | Route `/v1/embeddings` to the best compatible provider | High |
| **Model Pinning Policies** | Allow per-client or per-route pinned defaults | Medium |
| **Prometheus / Metrics Export** | Export health, latency, failover, and queue metrics | Medium |
| **Web Dashboard** | Browser-based UI for rankings, logs, and provider status | Medium |
| **Prompt Caching** | Cache identical prompts or enable adapter-side caching | Low |
| **Multi-user Auth** | Support multiple API keys with quotas and rate limits | Low |
| **Alternative storage backend** | Optional PostgreSQL backend for multi-node deployments | Low |
| **Local inference adapters** | Ollama / vLLM / llama.cpp adapters as optional future work | Low |
## References

[1] OpenAI. (2026). *API Reference — Chat Completions*. https://platform.openai.com/docs/api-reference/chat

[2] OpenRouter. (2026). *API Reference — Models*. https://openrouter.ai/docs/api-reference/list-available-models

[3] OpenRouter. (2026). *FAQ / Rate Limits*. https://openrouter.ai/docs/faq

[4] LMSYS Org. (2026). *Chatbot Arena Leaderboard*. https://chat.lmsys.org/

[5] LiteLLM. (2026). *LiteLLM Documentation*. https://docs.litellm.ai/

[6] APScheduler. (2026). *APScheduler Documentation*. https://apscheduler.readthedocs.io/

[7] FastAPI. (2026). *FastAPI Documentation*. https://fastapi.tiangolo.com/

[8] Conventional Commits. (2026). *Conventional Commits Specification v1.0.0*. https://www.conventionalcommits.org/en/v1.0.0/

[9] Keep a Changelog. (2026). *Keep a Changelog v1.1.0*. https://keepachangelog.com/en/1.1.0/

[10] Semantic Versioning. (2026). *Semantic Versioning 2.0.0*. https://semver.org/
---

# Section 15: Bootstrap Installer Scripts

## 15. Bootstrap Installer Scripts

These scripts are canonical repository files, but they are intentionally parameterized by the single publication token `jetymas`. Before publishing installers or copy-pasting public commands, replace `jetymas` with the real GitHub owner or organization for the repository.

The gateway ships two installer scripts that handle the complete setup process — including Docker installation — on a fresh machine. The goal is that a user who has never heard of Docker can go from zero to a running gateway with a single command. Both scripts must be committed to the repository root and kept in sync with the Docker Compose configuration.

### 15.1 Design Principles

The installer scripts follow four principles that are common to well-regarded open-source installers (Homebrew, rustup, uv, Tailscale):

**Idempotency.** Running the script a second time on a machine where the gateway is already installed must be safe. It should detect existing installations, offer to upgrade or skip, and never corrupt a working setup.

**Transparency.** Every significant action the script takes must be printed to the terminal before it is executed. The user must never be surprised by what the script did. No silent background downloads of unexpected software.

**Graceful failure.** If any step fails, the script must print a clear, human-readable error message explaining what went wrong and what the user can do to fix it. It must never leave the system in a partially configured state without telling the user.

**Minimal footprint.** The script installs only what is strictly necessary: Docker Engine (not Docker Desktop, on Linux), the gateway image, and a system service entry. It does not install development tools, modify shell profiles beyond what is needed, or require root access for more steps than necessary.

---

### 15.2 Repository File Placement

```
llm-gateway/
├── install.sh          # Linux and macOS installer
├── install.ps1         # Windows installer (PowerShell)
├── uninstall.sh        # Linux and macOS uninstaller
└── uninstall.ps1       # Windows uninstaller
```

All four files must be committed with Unix line endings (`LF`), even the `.ps1` files. The `.gitattributes` file must enforce this:

```gitattributes
install.sh    text eol=lf
uninstall.sh  text eol=lf
install.ps1   text eol=lf
uninstall.ps1 text eol=lf
```

The `install.ps1` file must begin with a `param()` block and must not use `#!/usr/bin/env` shebangs, as PowerShell does not support them.

---

### 15.3 `install.sh` — Linux and macOS

This script handles installation on Linux (Debian/Ubuntu, Fedora/RHEL, Arch) and macOS (Intel and Apple Silicon). It must be POSIX-compliant `sh` rather than bash-specific, so that it works on minimal server images that may not have bash installed. The only exception is process substitution, which must be avoided.

#### 15.3.1 Canonical Repository File

```sh
#!/bin/sh
# =============================================================================
# LLM Gateway — Bootstrap Installer
# https://github.com/jetymas/FreeLunch
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/jetymas/FreeLunch/main/install.sh | sh
#
# What this script does:
#   1. Detects the operating system and architecture.
#   2. Checks for Docker; installs it if missing.
#   3. Checks for Docker Compose v2; installs it if missing.
#   4. Creates the installation directory (~/.llm-gateway or /opt/llm-gateway).
#   5. Downloads config.yaml.example and .env.example from the repository.
#   6. Interactively prompts the user for required configuration values.
#   7. Pulls the latest gateway Docker image.
#   8. Registers and starts a system service (systemd or launchd).
#   9. Prints a success summary with the gateway URL and next steps.
# =============================================================================

set -e  # Exit immediately on error
set -u  # Treat unset variables as errors

# ── Constants ────────────────────────────────────────────────────────────────

REPO="jetymas/FreeLunch"
IMAGE="ghcr.io/${REPO}:latest"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/main"
INSTALL_DIR="${HOME}/.llm-gateway"
SERVICE_NAME="llm-gateway"
GATEWAY_PORT="8000"

# ANSI color codes (disabled if not a terminal)
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' BOLD='' RESET=''
fi

# ── Helper Functions ─────────────────────────────────────────────────────────

info()    { printf "${BLUE}[INFO]${RESET}  %s\n" "$*"; }
success() { printf "${GREEN}[OK]${RESET}    %s\n" "$*"; }
warn()    { printf "${YELLOW}[WARN]${RESET}  %s\n" "$*"; }
error()   { printf "${RED}[ERROR]${RESET} %s\n" "$*" >&2; exit 1; }

# Prompt the user for input with a default value.
# Usage: prompt "Question" "default_value" -> result in $REPLY
prompt() {
    _question="$1"
    _default="$2"
    if [ -n "$_default" ]; then
        printf "%s [%s]: " "$_question" "$_default"
    else
        printf "%s: " "$_question"
    fi
    read -r REPLY
    if [ -z "$REPLY" ]; then
        REPLY="$_default"
    fi
}

# Prompt for a secret (no echo). Result in $REPLY
prompt_secret() {
    _question="$1"
    printf "%s: " "$_question"
    stty -echo
    read -r REPLY
    stty echo
    printf "\n"
}

# Check if a command exists
has_cmd() { command -v "$1" >/dev/null 2>&1; }

# ── OS and Architecture Detection ────────────────────────────────────────────

detect_os() {
    OS="$(uname -s)"
    ARCH="$(uname -m)"

    case "$OS" in
        Linux)
            if [ -f /etc/os-release ]; then
                # shellcheck disable=SC1091
                . /etc/os-release
                DISTRO="${ID:-unknown}"
                DISTRO_LIKE="${ID_LIKE:-}"
            else
                DISTRO="unknown"
                DISTRO_LIKE=""
            fi
            ;;
        Darwin)
            DISTRO="macos"
            DISTRO_LIKE=""
            ;;
        *)
            error "Unsupported operating system: ${OS}. Please use the Windows installer (install.ps1) on Windows."
            ;;
    esac

    case "$ARCH" in
        x86_64|amd64) ARCH="amd64" ;;
        aarch64|arm64) ARCH="arm64" ;;
        *) error "Unsupported CPU architecture: ${ARCH}." ;;
    esac

    info "Detected OS: ${OS} (${DISTRO}), Architecture: ${ARCH}"
}

# ── Docker Installation ───────────────────────────────────────────────────────

check_docker() {
    if has_cmd docker && docker info >/dev/null 2>&1; then
        DOCKER_VERSION="$(docker --version | awk '{print $3}' | tr -d ',')"
        success "Docker is already installed (version ${DOCKER_VERSION})."
        return 0
    fi

    if has_cmd docker && ! docker info >/dev/null 2>&1; then
        warn "Docker is installed but the Docker daemon is not running."
        warn "Attempting to start the Docker service..."
        if has_cmd systemctl; then
            sudo systemctl start docker || error "Failed to start Docker. Please start it manually and re-run this installer."
        else
            error "Could not start Docker automatically. Please start the Docker daemon and re-run this installer."
        fi
        return 0
    fi

    # Docker is not installed
    info "Docker is not installed. Installing now..."
    install_docker
}

install_docker() {
    case "$OS" in
        Linux)
            install_docker_linux
            ;;
        Darwin)
            install_docker_macos
            ;;
    esac
}

install_docker_linux() {
    # Use the official Docker convenience script for all supported Linux distros.
    # This script supports: Ubuntu, Debian, Fedora, CentOS, RHEL, SLES, Raspbian.
    # See: https://get.docker.com
    info "Downloading Docker installation script from get.docker.com..."
    info "This will install Docker Engine (not Docker Desktop) — a lightweight daemon with no GUI."

    if ! has_cmd curl && ! has_cmd wget; then
        error "Neither curl nor wget is available. Please install one and re-run this installer."
    fi

    # Download the script to a temp file so the user can inspect it if desired
    DOCKER_INSTALL_SCRIPT="$(mktemp /tmp/docker-install.XXXXXX.sh)"
    if has_cmd curl; then
        curl -fsSL https://get.docker.com -o "$DOCKER_INSTALL_SCRIPT"
    else
        wget -qO "$DOCKER_INSTALL_SCRIPT" https://get.docker.com
    fi

    info "Running Docker installer (this may take a few minutes and will require sudo)..."
    sudo sh "$DOCKER_INSTALL_SCRIPT"
    rm -f "$DOCKER_INSTALL_SCRIPT"

    # Add the current user to the docker group so docker can be run without sudo
    info "Adding ${USER} to the 'docker' group..."
    sudo usermod -aG docker "$USER"

    # Enable and start the Docker service
    if has_cmd systemctl; then
        sudo systemctl enable docker
        sudo systemctl start docker
    fi

    success "Docker Engine installed successfully."
    warn "NOTE: You may need to log out and back in for group membership changes to take effect."
    warn "      If the installer fails at the 'pull image' step, run: newgrp docker"
}

install_docker_macos() {
    # On macOS, Docker Engine cannot run natively — Docker Desktop is required.
    # We check for Homebrew and use it if available; otherwise provide manual instructions.
    warn "On macOS, Docker requires Docker Desktop."

    if has_cmd brew; then
        info "Homebrew detected. Installing Docker Desktop via Homebrew Cask..."
        brew install --cask docker
        info "Please open Docker Desktop from your Applications folder and complete the setup."
        info "Once Docker Desktop is running, press Enter to continue..."
        read -r _
    else
        printf "\n"
        printf "${BOLD}Docker Desktop is required on macOS but could not be installed automatically.${RESET}\n"
        printf "Please follow these steps:\n"
        printf "  1. Download Docker Desktop from: https://www.docker.com/products/docker-desktop/\n"
        printf "  2. Install and open Docker Desktop.\n"
        printf "  3. Wait for Docker Desktop to show 'Docker Desktop is running' in the menu bar.\n"
        printf "  4. Re-run this installer.\n"
        printf "\n"
        exit 0
    fi
}

check_docker_compose() {
    # Docker Compose v2 is a plugin (docker compose), not a standalone binary (docker-compose).
    # The gateway requires v2 syntax.
    if docker compose version >/dev/null 2>&1; then
        COMPOSE_VERSION="$(docker compose version --short 2>/dev/null || echo 'unknown')"
        success "Docker Compose v2 is available (version ${COMPOSE_VERSION})."
        return 0
    fi

    # On Linux, Docker Compose v2 may need to be installed separately
    if [ "$OS" = "Linux" ]; then
        info "Installing Docker Compose plugin..."
        # Install via the official Docker apt/yum repository (already added by get.docker.com)
        if has_cmd apt-get; then
            sudo apt-get install -y docker-compose-plugin
        elif has_cmd dnf; then
            sudo dnf install -y docker-compose-plugin
        elif has_cmd yum; then
            sudo yum install -y docker-compose-plugin
        else
            # Fallback: download the binary directly from GitHub
            COMPOSE_VERSION_TAG="v2.27.0"
            COMPOSE_URL="https://github.com/docker/compose/releases/download/${COMPOSE_VERSION_TAG}/docker-compose-linux-${ARCH}"
            COMPOSE_DEST="${HOME}/.docker/cli-plugins/docker-compose"
            mkdir -p "${HOME}/.docker/cli-plugins"
            info "Downloading Docker Compose ${COMPOSE_VERSION_TAG}..."
            if has_cmd curl; then
                curl -fsSL "$COMPOSE_URL" -o "$COMPOSE_DEST"
            else
                wget -qO "$COMPOSE_DEST" "$COMPOSE_URL"
            fi
            chmod +x "$COMPOSE_DEST"
        fi
        success "Docker Compose plugin installed."
    else
        error "Docker Compose v2 is not available. Please ensure Docker Desktop is fully started."
    fi
}

# ── Installation Directory Setup ─────────────────────────────────────────────

setup_install_dir() {
    # Check if already installed
    if [ -d "$INSTALL_DIR" ] && [ -f "${INSTALL_DIR}/.env" ]; then
        warn "An existing installation was found at ${INSTALL_DIR}."
        prompt "Upgrade the existing installation? (existing config will be preserved)" "yes"
        if [ "$REPLY" = "yes" ] || [ "$REPLY" = "y" ]; then
            UPGRADING=true
            info "Upgrading existing installation..."
            return 0
        else
            info "Installation cancelled. Your existing installation is unchanged."
            exit 0
        fi
    fi

    UPGRADING=false
    info "Creating installation directory at ${INSTALL_DIR}..."
    mkdir -p "$INSTALL_DIR"
    success "Installation directory created."
}

# ── Configuration Setup ───────────────────────────────────────────────────────

download_config_templates() {
    if [ "$UPGRADING" = "true" ]; then
        info "Skipping config template download (upgrading existing installation)."
        return 0
    fi

    info "Downloading configuration templates..."

    if has_cmd curl; then
        curl -fsSL "${RAW_BASE}/config.yaml.example" -o "${INSTALL_DIR}/config.yaml"
        curl -fsSL "${RAW_BASE}/docker-compose.yml" -o "${INSTALL_DIR}/docker-compose.yml"
    else
        wget -qO "${INSTALL_DIR}/config.yaml" "${RAW_BASE}/config.yaml.example"
        wget -qO "${INSTALL_DIR}/docker-compose.yml" "${RAW_BASE}/docker-compose.yml"
    fi

    success "Configuration templates downloaded."
}

collect_user_config() {
    if [ "$UPGRADING" = "true" ]; then
        info "Skipping configuration (upgrading — existing .env is preserved)."
        return 0
    fi

    printf "\n${BOLD}=== Gateway Configuration ===${RESET}\n"
    printf "You will need API keys from the providers you want to use.\n"
    printf "OpenRouter is required for the initial shipping provider set.\n\n"

    printf "OpenRouter (https://openrouter.ai/settings/keys):\n"
    prompt_secret "  OpenRouter API key"
    OPENROUTER_API_KEY="$REPLY"

    if [ -z "$OPENROUTER_API_KEY" ]; then
        error "An OpenRouter API key is required."
    fi

    # Gateway API key
    printf "\nGateway authentication:\n"
    printf "  An API key protects the gateway from unauthorized use on your network.\n"
    printf "  Leave blank to disable authentication (safe for home networks).\n"
    prompt_secret "  Gateway API key (leave blank to disable auth)"
    GATEWAY_API_KEY="$REPLY"

    # Port
    printf "\nNetwork:\n"
    prompt "  Port to listen on" "$GATEWAY_PORT"
    GATEWAY_PORT="$REPLY"

    # Write the .env file
    cat > "${INSTALL_DIR}/.env" <<EOF
# LLM Gateway — Environment Variables
# Generated by install.sh on $(date)
# Edit this file to update your configuration, then restart the gateway.

OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
GATEWAY_API_KEY=${GATEWAY_API_KEY}
GATEWAY_DB_PATH=/data/gateway.db
EOF

    # Patch the port in docker-compose.yml
    if has_cmd sed; then
        sed -i.bak "s/\"8000:8000\"/\"${GATEWAY_PORT}:8000\"/" "${INSTALL_DIR}/docker-compose.yml"
        rm -f "${INSTALL_DIR}/docker-compose.yml.bak"
    fi

    success "Configuration written to ${INSTALL_DIR}/.env"
}

# ── Docker Image Pull ─────────────────────────────────────────────────────────

pull_image() {
    info "Pulling the latest gateway image (${IMAGE})..."
    info "This may take a few minutes on first install (~200MB download)."
    docker pull "$IMAGE"
    success "Gateway image downloaded."
}

# ── System Service Registration ───────────────────────────────────────────────

register_service() {
    case "$OS" in
        Linux)  register_service_systemd ;;
        Darwin) register_service_launchd ;;
    esac
}

register_service_systemd() {
    if ! has_cmd systemctl; then
        warn "systemd not found. The gateway will not start automatically on boot."
        warn "Start it manually with: cd ${INSTALL_DIR} && docker compose up -d"
        return 0
    fi

    info "Registering systemd service (${SERVICE_NAME})..."

    # Write the unit file
    sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null <<EOF
[Unit]
Description=Intelligent LLM Gateway
Documentation=https://github.com/${REPO}
After=network-online.target docker.service
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=/usr/bin/docker compose up -d --pull always
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=120
Restart=no
User=${USER}

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable "${SERVICE_NAME}"
    sudo systemctl start "${SERVICE_NAME}"

    success "systemd service registered and started."
}

register_service_launchd() {
    PLIST_PATH="${HOME}/Library/LaunchAgents/com.llmgateway.plist"
    info "Registering launchd service at ${PLIST_PATH}..."

    # Docker Compose path on macOS (may vary by installation method)
    DOCKER_COMPOSE_PATH="$(command -v docker || echo '/usr/local/bin/docker')"

    cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.llmgateway</string>
    <key>ProgramArguments</key>
    <array>
        <string>${DOCKER_COMPOSE_PATH}</string>
        <string>compose</string>
        <string>--project-directory</string>
        <string>${INSTALL_DIR}</string>
        <string>up</string>
        <string>-d</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>${HOME}/.llm-gateway/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>${HOME}/.llm-gateway/launchd-error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF

    launchctl load -w "$PLIST_PATH"
    success "launchd service registered and started."
}

# ── Post-Install Summary ──────────────────────────────────────────────────────

print_summary() {
    LOCAL_IP="127.0.0.1"
    # Try to find the LAN IP for the summary
    if has_cmd ip; then
        LOCAL_IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}' || echo '127.0.0.1')"
    elif has_cmd ifconfig; then
        LOCAL_IP="$(ifconfig | awk '/inet / && !/127.0.0.1/ {print $2; exit}' || echo '127.0.0.1')"
    fi

    printf "\n"
    printf "${GREEN}${BOLD}╔══════════════════════════════════════════════════════╗${RESET}\n"
    printf "${GREEN}${BOLD}║        LLM Gateway installed successfully!           ║${RESET}\n"
    printf "${GREEN}${BOLD}╚══════════════════════════════════════════════════════╝${RESET}\n"
    printf "\n"
    printf "${BOLD}Gateway URL (this machine):${RESET}  http://localhost:${GATEWAY_PORT}/v1\n"
    printf "${BOLD}Gateway URL (local network):${RESET} http://${LOCAL_IP}:${GATEWAY_PORT}/v1\n"
    printf "${BOLD}Admin health check:${RESET}          http://localhost:${GATEWAY_PORT}/admin/health\n"
    printf "${BOLD}Installation directory:${RESET}      ${INSTALL_DIR}\n"
    printf "\n"
    printf "${BOLD}Configure your LLM clients:${RESET}\n"
    printf "  • OpenAI Base URL: http://${LOCAL_IP}:${GATEWAY_PORT}/v1\n"
    printf "  • Model:           auto\n"
    if [ -n "${GATEWAY_API_KEY:-}" ]; then
        printf "  • API Key:         (the key you entered during setup)\n"
    else
        printf "  • API Key:         (any string — auth is disabled)\n"
    fi
    printf "\n"
    printf "${BOLD}Useful commands:${RESET}\n"
    printf "  View logs:    docker compose -f ${INSTALL_DIR}/docker-compose.yml logs -f\n"
    printf "  Stop:         docker compose -f ${INSTALL_DIR}/docker-compose.yml down\n"
    printf "  Restart:      docker compose -f ${INSTALL_DIR}/docker-compose.yml restart\n"
    printf "  Uninstall:    curl -fsSL ${RAW_BASE}/uninstall.sh | sh\n"
    printf "\n"
    printf "The gateway will start automatically on every system boot.\n"
    printf "It may take up to 60 seconds on first start while it discovers and ranks models.\n"
    printf "\n"
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
    printf "\n${BOLD}LLM Gateway — Bootstrap Installer${RESET}\n"
    printf "https://github.com/${REPO}\n\n"

    detect_os
    check_docker
    check_docker_compose
    setup_install_dir
    download_config_templates
    collect_user_config
    pull_image
    register_service
    print_summary
}

main "$@"
```

#### 15.3.2 `uninstall.sh`

```sh
#!/bin/sh
# LLM Gateway — Uninstaller

set -e
INSTALL_DIR="${HOME}/.llm-gateway"
SERVICE_NAME="llm-gateway"

printf "This will stop and remove the LLM Gateway service and all its data.\n"
printf "Your API keys in ${INSTALL_DIR}/.env will also be deleted.\n"
printf "Are you sure? (yes/no): "
read -r CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    printf "Uninstall cancelled.\n"
    exit 0
fi

# Stop and remove the container
if [ -f "${INSTALL_DIR}/docker-compose.yml" ]; then
    docker compose -f "${INSTALL_DIR}/docker-compose.yml" down --volumes 2>/dev/null || true
fi

# Remove system service
if command -v systemctl >/dev/null 2>&1 && systemctl is-enabled "$SERVICE_NAME" >/dev/null 2>&1; then
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    sudo rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    sudo systemctl daemon-reload
fi

# Remove launchd plist (macOS)
PLIST="${HOME}/Library/LaunchAgents/com.llmgateway.plist"
if [ -f "$PLIST" ]; then
    launchctl unload -w "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
fi

# Remove installation directory
rm -rf "$INSTALL_DIR"

printf "LLM Gateway has been uninstalled.\n"
printf "The Docker image is still cached locally. To remove it:\n"
printf "  docker rmi ghcr.io/jetymas/FreeLunch:latest\n"
```

---

### 15.4 `install.ps1` — Windows (PowerShell)

This script runs on Windows 10 (version 1903 or later) and Windows 11. It requires PowerShell 5.1 or later, which is pre-installed on all supported Windows versions. It must be run as Administrator, because installing WSL2 and Docker Desktop requires elevated privileges.

#### 15.4.1 Prerequisites and WSL2

Docker Desktop on Windows requires WSL2 (Windows Subsystem for Linux version 2). The script handles WSL2 installation automatically, but WSL2 installation requires a reboot. The script handles this by writing a "resume" flag to the registry, registering itself as a Run-once startup task, and instructing the user to reboot. After reboot, the script resumes from the step after WSL2 installation.

#### 15.4.2 Full Annotated Script

```powershell
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    LLM Gateway — Bootstrap Installer for Windows
.DESCRIPTION
    Installs Docker Desktop (with WSL2), pulls the LLM Gateway image,
    configures the gateway, and registers it as a Windows service.
.LINK
    https://github.com/jetymas/FreeLunch
.EXAMPLE
    irm https://raw.githubusercontent.com/jetymas/FreeLunch/main/install.ps1 | iex
#>

param(
    [switch]$Resume  # Internal: used when resuming after a reboot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Constants ─────────────────────────────────────────────────────────────────

$Repo       = "jetymas/FreeLunch"
$Image      = "ghcr.io/$Repo`:latest"
$RawBase    = "https://raw.githubusercontent.com/$Repo/main"
$InstallDir = Join-Path $env:USERPROFILE ".llm-gateway"
$ServiceName = "LLMGateway"
$ResumeKey  = "HKCU:\Software\LLMGateway\Install"
$GatewayPort = "8000"

# ── Helper Functions ──────────────────────────────────────────────────────────

function Write-Info    { param($Msg) Write-Host "[INFO]  $Msg" -ForegroundColor Cyan }
function Write-Success { param($Msg) Write-Host "[OK]    $Msg" -ForegroundColor Green }
function Write-Warn    { param($Msg) Write-Host "[WARN]  $Msg" -ForegroundColor Yellow }
function Write-Fail    { param($Msg) Write-Host "[ERROR] $Msg" -ForegroundColor Red; exit 1 }

function Prompt-Input {
    param([string]$Question, [string]$Default = "")
    if ($Default) {
        $prompt = "$Question [$Default]"
    } else {
        $prompt = $Question
    }
    $value = Read-Host $prompt
    if ([string]::IsNullOrWhiteSpace($value)) { $value = $Default }
    return $value
}

function Prompt-Secret {
    param([string]$Question)
    $secure = Read-Host $Question -AsSecureString
    $plain  = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    return $plain
}

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# ── Windows Version Check ─────────────────────────────────────────────────────

function Assert-WindowsVersion {
    $build = [System.Environment]::OSVersion.Version.Build
    if ($build -lt 19041) {
        Write-Fail "Windows 10 version 2004 (build 19041) or later is required for WSL2. Your build: $build"
    }
    Write-Info "Windows version check passed (build $build)."
}

# ── WSL2 Installation ─────────────────────────────────────────────────────────

function Install-WSL2 {
    Write-Info "Checking WSL2 status..."

    # Check if WSL is already installed and version 2 is default
    try {
        $wslStatus = wsl --status 2>&1
        if ($wslStatus -match "Default Version: 2") {
            Write-Success "WSL2 is already installed and set as default."
            return $false  # No reboot needed
        }
    } catch { }

    Write-Info "WSL2 is not installed. Installing now..."
    Write-Info "This requires a system reboot. The installer will resume automatically after reboot."

    # Enable WSL and Virtual Machine Platform features
    dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart | Out-Null
    dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart | Out-Null

    # Download and install the WSL2 kernel update
    Write-Info "Downloading WSL2 Linux kernel update..."
    $kernelUrl = "https://wslstorestorage.blob.core.windows.net/wslblob/wsl_update_x64.msi"
    $kernelMsi = Join-Path $env:TEMP "wsl_update_x64.msi"
    Invoke-WebRequest -Uri $kernelUrl -OutFile $kernelMsi -UseBasicParsing
    Start-Process msiexec.exe -ArgumentList "/i `"$kernelMsi`" /quiet /norestart" -Wait
    Remove-Item $kernelMsi -Force

    # Set WSL2 as the default version
    wsl --set-default-version 2

    return $true  # Reboot is required
}

function Register-ResumeTask {
    # Write a registry flag so we know to resume after reboot
    if (-not (Test-Path $ResumeKey)) {
        New-Item -Path $ResumeKey -Force | Out-Null
    }
    Set-ItemProperty -Path $ResumeKey -Name "Resuming" -Value "1"

    # Register a one-time scheduled task to resume the installer after login
    $scriptPath = $PSCommandPath
    if (-not $scriptPath) {
        # Script was piped via irm | iex — save it first
        $scriptPath = Join-Path $env:TEMP "llm-gateway-install.ps1"
        Invoke-WebRequest -Uri "$RawBase/install.ps1" -OutFile $scriptPath -UseBasicParsing
    }

    $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
                   -Argument "-ExecutionPolicy Bypass -File `"$scriptPath`" -Resume"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    Register-ScheduledTask -TaskName "LLMGatewayInstallResume" `
        -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

    Write-Host ""
    Write-Host "A reboot is required to complete WSL2 installation." -ForegroundColor Yellow
    Write-Host "The installer will resume automatically after you log back in." -ForegroundColor Yellow
    Write-Host ""
    $reboot = Prompt-Input "Reboot now?" "yes"
    if ($reboot -eq "yes" -or $reboot -eq "y") {
        Restart-Computer -Force
    } else {
        Write-Warn "Please reboot manually and log back in to continue the installation."
        exit 0
    }
}

function Clear-ResumeTask {
    Unregister-ScheduledTask -TaskName "LLMGatewayInstallResume" -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item -Path $ResumeKey -Recurse -Force -ErrorAction SilentlyContinue
}

# ── Docker Desktop Installation ───────────────────────────────────────────────

function Install-DockerDesktop {
    Write-Info "Checking Docker Desktop status..."

    if (Test-Command "docker") {
        try {
            docker info 2>&1 | Out-Null
            $ver = (docker --version) -replace "Docker version ", "" -replace ",.*", ""
            Write-Success "Docker Desktop is already running (version $ver)."
            return
        } catch {
            Write-Warn "Docker is installed but not running. Please start Docker Desktop and press Enter."
            Read-Host "Press Enter when Docker Desktop is running"
            return
        }
    }

    Write-Info "Downloading Docker Desktop installer..."
    Write-Info "Docker Desktop is ~600MB. This may take several minutes."

    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "amd64" }
    $dockerUrl = "https://desktop.docker.com/win/main/$arch/Docker%20Desktop%20Installer.exe"
    $dockerInstaller = Join-Path $env:TEMP "DockerDesktopInstaller.exe"

    Invoke-WebRequest -Uri $dockerUrl -OutFile $dockerInstaller -UseBasicParsing

    Write-Info "Running Docker Desktop installer (silent install)..."
    Start-Process $dockerInstaller -ArgumentList "install --quiet --accept-license" -Wait
    Remove-Item $dockerInstaller -Force

    Write-Success "Docker Desktop installed."
    Write-Warn "Please start Docker Desktop from the Start menu."
    Write-Warn "Wait for the whale icon in the system tray to show 'Docker Desktop is running'."
    Write-Host ""
    Read-Host "Press Enter when Docker Desktop is running"

    # Verify Docker is now accessible
    $retries = 0
    while ($retries -lt 12) {
        try {
            docker info 2>&1 | Out-Null
            Write-Success "Docker Desktop is running."
            return
        } catch {
            $retries++
            Write-Info "Waiting for Docker to start... ($retries/12)"
            Start-Sleep -Seconds 10
        }
    }
    Write-Fail "Docker Desktop did not start within 2 minutes. Please start it manually and re-run the installer."
}

# ── Installation Directory and Configuration ──────────────────────────────────

function Setup-InstallDir {
    $script:Upgrading = $false

    if ((Test-Path $InstallDir) -and (Test-Path (Join-Path $InstallDir ".env"))) {
        Write-Warn "An existing installation was found at $InstallDir."
        $upgrade = Prompt-Input "Upgrade the existing installation? (existing config will be preserved)" "yes"
        if ($upgrade -eq "yes" -or $upgrade -eq "y") {
            $script:Upgrading = $true
            Write-Info "Upgrading existing installation..."
            return
        } else {
            Write-Info "Installation cancelled."
            exit 0
        }
    }

    Write-Info "Creating installation directory at $InstallDir..."
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Write-Success "Installation directory created."
}

function Download-ConfigTemplates {
    if ($script:Upgrading) {
        Write-Info "Skipping config template download (upgrading)."
        return
    }

    Write-Info "Downloading configuration templates..."
    Invoke-WebRequest -Uri "$RawBase/config.yaml.example" `
        -OutFile (Join-Path $InstallDir "config.yaml") -UseBasicParsing
    Invoke-WebRequest -Uri "$RawBase/docker-compose.yml" `
        -OutFile (Join-Path $InstallDir "docker-compose.yml") -UseBasicParsing
    Write-Success "Configuration templates downloaded."
}

function Collect-UserConfig {
    if ($script:Upgrading) {
        Write-Info "Skipping configuration (upgrading — existing .env is preserved)."
        return
    }

    Write-Host ""
    Write-Host "=== Gateway Configuration ===" -ForegroundColor White
    Write-Host "You will need API keys from the providers you want to use."
    Write-Host "OpenRouter is required for the initial shipping provider set."
    Write-Host ""

    Write-Host "OpenRouter (https://openrouter.ai/settings/keys):"
    $openrouterKey = Prompt-Secret "  OpenRouter API key"

    if ([string]::IsNullOrWhiteSpace($openrouterKey)) {
        Write-Fail "An OpenRouter API key is required."
    }

    Write-Host ""
    Write-Host "Gateway authentication:"
    Write-Host "  Leave blank to disable authentication (safe for home networks)."
    $gatewayKey = Prompt-Secret "  Gateway API key (leave blank to disable auth)"

    Write-Host ""
    $script:GatewayPort = Prompt-Input "  Port to listen on" $GatewayPort

    # Write .env file
    $envContent = @"
# LLM Gateway - Environment Variables
# Generated by install.ps1 on $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

OPENROUTER_API_KEY=$openrouterKey
GATEWAY_API_KEY=$gatewayKey
GATEWAY_DB_PATH=/data/gateway.db
"@
    Set-Content -Path (Join-Path $InstallDir ".env") -Value $envContent -Encoding UTF8

    # Patch port in docker-compose.yml
    $composePath = Join-Path $InstallDir "docker-compose.yml"
    (Get-Content $composePath) -replace '"8000:8000"', "`"$($script:GatewayPort):8000`"" |
        Set-Content $composePath -Encoding UTF8

    Write-Success "Configuration written to $InstallDir\.env"
}

# ── Docker Image and Service ──────────────────────────────────────────────────

function Pull-Image {
    Write-Info "Pulling the latest gateway image ($Image)..."
    Write-Info "This may take a few minutes on first install (~200MB download)."
    docker pull $Image
    Write-Success "Gateway image downloaded."
}

function Register-WindowsService {
    # Use NSSM (Non-Sucking Service Manager) to wrap docker compose as a Windows service.
    # NSSM is a well-established open-source tool for this purpose.
    Write-Info "Installing NSSM (service manager)..."

    $nssmUrl  = "https://nssm.cc/release/nssm-2.24.zip"
    $nssmZip  = Join-Path $env:TEMP "nssm.zip"
    $nssmDir  = Join-Path $env:TEMP "nssm-extract"
    $nssmDest = "C:\Windows\System32\nssm.exe"

    if (-not (Test-Path $nssmDest)) {
        Invoke-WebRequest -Uri $nssmUrl -OutFile $nssmZip -UseBasicParsing
        Expand-Archive -Path $nssmZip -DestinationPath $nssmDir -Force
        $arch = if ([Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
        Copy-Item (Join-Path $nssmDir "nssm-2.24\$arch\nssm.exe") $nssmDest -Force
        Remove-Item $nssmZip, $nssmDir -Recurse -Force
    }

    Write-Info "Registering Windows service ($ServiceName)..."

    # Remove existing service if upgrading
    nssm stop $ServiceName 2>$null
    nssm remove $ServiceName confirm 2>$null

    $dockerPath = (Get-Command docker).Source
    $composeArgs = "compose --project-directory `"$InstallDir`" up -d"

    nssm install $ServiceName $dockerPath $composeArgs
    nssm set $ServiceName AppDirectory $InstallDir
    nssm set $ServiceName DisplayName "Intelligent LLM Gateway"
    nssm set $ServiceName Description "Auto-routing proxy for free LLM models"
    nssm set $ServiceName Start SERVICE_AUTO_START
    nssm set $ServiceName AppStdout (Join-Path $InstallDir "service.log")
    nssm set $ServiceName AppStderr (Join-Path $InstallDir "service-error.log")

    # Load environment variables from .env file into the service
    $envVars = Get-Content (Join-Path $InstallDir ".env") |
        Where-Object { $_ -match "^[A-Z_]+=.+" } |
        ForEach-Object { $_ }
    foreach ($envVar in $envVars) {
        $parts = $envVar -split "=", 2
        nssm set $ServiceName AppEnvironmentExtra "$($parts[0])=$($parts[1])"
    }

    Start-Service $ServiceName
    Write-Success "Windows service registered and started."
}

# ── Post-Install Summary ──────────────────────────────────────────────────────

function Print-Summary {
    # Get local IP
    $localIP = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -ne "127.0.0.1" -and $_.PrefixOrigin -ne "WellKnown" } |
        Select-Object -First 1).IPAddress

    if (-not $localIP) { $localIP = "127.0.0.1" }

    Write-Host ""
    Write-Host "╔══════════════════════════════════════════════════════╗" -ForegroundColor Green
    Write-Host "║        LLM Gateway installed successfully!           ║" -ForegroundColor Green
    Write-Host "╚══════════════════════════════════════════════════════╝" -ForegroundColor Green
    Write-Host ""
    Write-Host "Gateway URL (this machine):  http://localhost:$($script:GatewayPort)/v1"
    Write-Host "Gateway URL (local network): http://${localIP}:$($script:GatewayPort)/v1"
    Write-Host "Admin health check:          http://localhost:$($script:GatewayPort)/admin/health"
    Write-Host "Installation directory:      $InstallDir"
    Write-Host ""
    Write-Host "Configure your LLM clients:" -ForegroundColor White
    Write-Host "  • OpenAI Base URL: http://${localIP}:$($script:GatewayPort)/v1"
    Write-Host "  • Model:           auto"
    Write-Host ""
    Write-Host "Useful commands:" -ForegroundColor White
    Write-Host "  View logs:  docker compose -f `"$InstallDir\docker-compose.yml`" logs -f"
    Write-Host "  Stop:       Stop-Service $ServiceName"
    Write-Host "  Start:      Start-Service $ServiceName"
    Write-Host "  Uninstall:  irm $RawBase/uninstall.ps1 | iex"
    Write-Host ""
    Write-Host "The gateway starts automatically on every system boot." -ForegroundColor Cyan
    Write-Host ""
}

# ── Main ──────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "LLM Gateway - Bootstrap Installer for Windows" -ForegroundColor White
Write-Host "https://github.com/$Repo"
Write-Host ""

Assert-WindowsVersion

if ($Resume) {
    Write-Info "Resuming installation after reboot..."
    Clear-ResumeTask
    # WSL2 is now installed; continue from Docker Desktop step
    Install-DockerDesktop
} else {
    $rebootNeeded = Install-WSL2
    if ($rebootNeeded) {
        Register-ResumeTask  # This reboots the machine
        exit 0               # Unreachable, but explicit
    }
    Install-DockerDesktop
}

Setup-InstallDir
Download-ConfigTemplates
Collect-UserConfig
Pull-Image
Register-WindowsService
Print-Summary
```

#### 15.4.3 `uninstall.ps1`

```powershell
#Requires -RunAsAdministrator
param()

$InstallDir  = Join-Path $env:USERPROFILE ".llm-gateway"
$ServiceName = "LLMGateway"

Write-Host "This will stop and remove the LLM Gateway service and all its data."
$confirm = Read-Host "Are you sure? (yes/no)"
if ($confirm -ne "yes") { Write-Host "Uninstall cancelled."; exit 0 }

# Stop and remove the Windows service
if (Get-Service $ServiceName -ErrorAction SilentlyContinue) {
    Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue
    nssm remove $ServiceName confirm 2>$null
}

# Stop and remove Docker containers
if (Test-Path (Join-Path $InstallDir "docker-compose.yml")) {
    docker compose --project-directory $InstallDir down --volumes 2>$null
}

# Remove installation directory
Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "LLM Gateway has been uninstalled."
Write-Host "The Docker image is still cached locally. To remove it:"
Write-Host "  docker rmi ghcr.io/jetymas/FreeLunch:latest"
```

---

### 15.5 Repository Updates Required for Section 15

The following files must be added to the repository to support the bootstrap installer:

| File | Purpose |
|---|---|
| `install.sh` | Linux and macOS installer (content from Section 15.3) |
| `install.ps1` | Windows installer (content from Section 15.4) |
| `uninstall.sh` | Linux and macOS uninstaller |
| `uninstall.ps1` | Windows uninstaller |
| `.gitattributes` | Enforce LF line endings on all installer scripts |

The `README.md` Quick Start section (see Section 12.2) must be updated to lead with the one-liner install commands rather than the manual Docker Compose steps. The manual steps should remain in the document as a secondary option under a collapsible `<details>` block.

### 15.6 CI/CD Updates Required for Section 15

A new CI job must be added to `.github/workflows/ci.yml` to validate the installer scripts on every push:

```yaml
validate-installers:
  name: Validate Installer Scripts
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - name: Check shell script syntax (install.sh)
      run: sh -n install.sh && sh -n uninstall.sh
    - name: Lint shell scripts with shellcheck
      uses: ludeeus/action-shellcheck@master
      with:
        scandir: "."
        pattern: "*.sh"
    - name: Validate PowerShell syntax (install.ps1)
      shell: pwsh
      run: |
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            "${{ github.workspace }}/install.ps1", [ref]$null, [ref]$errors)
        if ($errors.Count -gt 0) { $errors; exit 1 }
        [System.Management.Automation.Language.Parser]::ParseFile(
            "${{ github.workspace }}/uninstall.ps1", [ref]$null, [ref]$errors)
        if ($errors.Count -gt 0) { $errors; exit 1 }
```

### 15.7 `uv` One-Liner Alternative (Developer Path)

For developers and technically confident users who prefer not to use Docker, the gateway must also be publishable as a Python package on PyPI. This enables the `uv` installation path described in the architecture overview.

The `pyproject.toml` must include an entry point:

```toml
[project.scripts]
llm-gateway = "src.proxy:run"
```

And `src/proxy.py` must expose a `run()` function:

```python
def run():
    """Entry point for uv tool install / pipx install."""
    import uvicorn
    from src.config import get_config
    cfg = get_config()
    uvicorn.run(
        "src.proxy:app",
        host=cfg.get("gateway.host", "0.0.0.0"),
        port=int(cfg.get("gateway.port", 8000)),
        log_level=cfg.get("gateway.log_level", "info"),
    )
```

With this in place, the developer install path becomes:

```bash
# Install uv (one-time, ~10MB, no root required)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install the gateway
uv tool install llm-gateway

# Run it
llm-gateway
```

This path does not provide automatic service registration or Docker isolation, but it is the fastest way to get the gateway running for development or personal use on a machine where Python is already a comfortable tool.

---

*End of Section 15. End of Specification.*
