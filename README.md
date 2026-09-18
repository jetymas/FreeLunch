# FreeLunch

FreeLunch is a self-hosted, OpenAI-compatible gateway that selects an available model for each request. It combines provider discovery, benchmark enrichment, health-aware ranking, capability checks, and bounded failover behind one stable `/v1` API.

The service is intentionally a single-node application: one FastAPI process, SQLite persistence, a scheduler, and provider adapters. Docker is supported but optional; direct Python execution is useful for local development and small deployments.

## Quick start

### Docker installer

The installers require Docker and Compose v2 to already be installed. They do not install Docker.

Linux/macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/jetymas/FreeLunch/main/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/jetymas/FreeLunch/main/install.ps1 | iex
```

The installer asks for `OPENROUTER_API_KEY`, writes deployment files under `~/.freelunch` (or `%USERPROFILE%\\.freelunch`), and starts the gateway.

### Docker Compose from a checkout

```bash
cp config.yaml.example config.yaml
cp .env.example .env
# Set OPENROUTER_API_KEY in .env, then:
docker compose up -d
```

### Native Python run

Docker is optional when running from source:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn src.main:app --host 127.0.0.1 --port 8000
```

For development and tests, install `requirements-dev.txt` too. Copy `config.yaml.example` and `.env.example` when local configuration is needed.

The base install includes exact `tiktoken` sizing and heuristic fallback. To
also enable local Hugging Face tokenizers for supported non-OpenAI families:

```bash
pip install -r requirements-tokenizers.txt
```

Bind to a non-loopback address only when gateway authentication and appropriate
network controls are enabled.

Check the service:

```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
curl http://localhost:8000/v1/models
```

`/healthz` means that the process is alive. `/readyz` means that discovery has produced at least one active, healthy, routable model.

## Use the gateway

FreeLunch exposes the OpenAI-compatible endpoints `GET /v1/models` and `POST /v1/chat/completions`. Use `auto` as the model name to let the gateway select a candidate.

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"hello"}]}'
```

If gateway authentication is enabled, add `Authorization: Bearer <GATEWAY_API_KEY>`. Many OpenAI-compatible clients only need the base URL `http://localhost:8000/v1`, model `auto`, and a placeholder API key when gateway auth is disabled.

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Summarize FreeLunch in one sentence."}],
)
print(response.choices[0].message.content)
```

The complete request and schema surface is defined by [`src/proxy.py`](./src/proxy.py) and the generated `/docs` and `/openapi.json` endpoints. Do not maintain a second handwritten route inventory here.

## Configuration and secrets

[`config.yaml.example`](./config.yaml.example) is the authoritative reference for the implemented configuration surface. [`.env.example`](./.env.example) lists environment variables used by the default deployment. Provider-specific behavior belongs in [`src/providers/`](./src/providers/).

Keep real provider keys in local environment files or the managed secret vault; never commit populated secrets. Managed provider secrets are encrypted in SQLite and unlocked only for the running process. Gateway authentication is separate and should be enabled before exposing the service beyond a trusted local network.

The no-key OpenRouter stub is for development only: it requires `APP_ENV=dev` and explicit `providers.openrouter.dev_stub_enabled=true`, and is ignored in other environments.

## How it works

For each request, FreeLunch parses requirements (context, capabilities, tools, vision, streaming, and output capacity), filters incompatible candidates, ranks the remainder using health and performance signals, and calls a provider adapter. Retryable failures can trigger bounded failover; partial streamed responses are never replayed through another provider.

Background jobs refresh provider inventory and benchmark enrichment, recompute ranking, probe health, enforce retention, and refresh runtime configuration. Application writes pass through the SQLite writer thread. Runtime JSON logs are ephemeral process output; request telemetry is durable SQLite data exposed by the admin APIs.

Stable architectural rules and source links are in [`docs/architecture.md`](./docs/architecture.md). Operational procedures, deployment modes, admin endpoint interpretation, and incident checks are in [`docs/operations.md`](./docs/operations.md).

## Administration

The lightweight operator UI is served at `/admin/ui`. JSON endpoints under `/admin` expose model state, health and scheduler status, effective config, runtime logs, durable request logs, gateway auth, and the encrypted secret vault. Treat admin endpoints as privileged: do not expose them without gateway authentication and network controls.

For the current operational checklist, use [`docs/operations.md`](./docs/operations.md). Endpoint behavior is implemented in [`src/admin_ui.py`](./src/admin_ui.py) and [`src/proxy.py`](./src/proxy.py).

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

Validation commands:

```bash
python -m ruff check .
python -m mypy src
python scripts/generate_architecture.py --check
python -m pytest tests -q --basetemp .pytest_tmp_local -p no:cacheprovider
python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_cov -p no:cacheprovider
```

For a focused provider check, use the optional manual harness: `python scripts/provider_smoke.py --help` or `python scripts/provider_smoke.py --json`.

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for code-change, testing, release, and documentation-sync expectations.

## Repository map

```text
src/main.py                 application lifecycle and scheduler wiring
src/proxy.py                HTTP API, auth, request handling, failover, streams
src/routing.py              candidate filtering and ordering
src/tokens.py               request sizing and capability parsing
src/discover.py             provider discovery and model reconciliation
src/ranking.py              composite model scoring
src/health.py               passive health and active probes
src/db.py                   SQLite schema, migrations, and writer thread
src/providers/              provider contracts, registry, and adapters
src/admin_ui.py             operator UI entry point
scripts/provider_smoke.py   manual provider validation
tests/                      unit, integration, property, and stress tests
```

Architecture diagrams are intentionally small and anchored to these source entry points. Generated inventories and route schemas should come from code or FastAPI OpenAPI output rather than being copied into prose.

## Current goals

The active direction is tracked in [`docs/roadmap.md`](./docs/roadmap.md):

1. evaluate an optional, cheap prompt-task classifier before model selection;
2. make native Python deployment a first-class path while keeping Docker optional;
3. reduce unnecessary dependencies and review security boundaries.

The classifier is deliberately separate from token estimation and provider adapters. Its design must define opt-in behavior, bounded latency, privacy, failure handling, and whether classification changes routing.

## Documentation map

Each topic has one living source of truth:

- [`README.md`](./README.md): purpose, onboarding, and user-facing behavior.
- [`docs/architecture.md`](./docs/architecture.md): boundaries, invariants, runtime flows, and code-driven architecture mapping.
- [`docs/operations.md`](./docs/operations.md): deployment, configuration, administration, observability, and incidents.
- [`docs/roadmap.md`](./docs/roadmap.md): open goals, decisions, and acceptance criteria.
- [`CONTRIBUTING.md`](./CONTRIBUTING.md): developer workflow and validation.
- [`AGENTS.md`](./AGENTS.md): repository instructions for coding agents.
- [`CHANGELOG.md`](./CHANGELOG.md): released user-visible changes.

Routes, settings, provider lists, dependencies, and CI behavior remain defined by their respective code, configuration, project metadata, and workflow files.

## License

MIT. See [`LICENSE`](./LICENSE).
