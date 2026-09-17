# Contributing

Start with the current code and the three living design documents:

1. [`README.md`](./README.md) for user-facing behavior and setup;
2. [`docs/architecture.md`](./docs/architecture.md) for boundaries and invariants;
3. [`docs/operations.md`](./docs/operations.md) for deployment and operator behavior;
4. [`docs/roadmap.md`](./docs/roadmap.md) for open work and acceptance criteria.

Do not treat old implementation plans or release ledgers as current contracts.
When a change needs historical context, inspect Git history. Keep one source of
truth for each topic and link to code/config/workflows instead of copying
enumerations into prose.

## Project principles

FreeLunch is deliberately conservative:

- Keep provider-specific behavior inside `src/providers/*`.
- Keep routing, health, and proxy orchestration provider-agnostic.
- Treat SQLite as a single-node system with one authoritative writer path.
- Prefer clear, low-overhead designs over abstraction sprawl or concurrency-heavy
  cleverness.
- Keep persisted timestamps as UTC ISO 8601 with a `Z` suffix.
- Preserve hard request constraints even when adding ranking or classification
  behavior.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
cp config.yaml.example config.yaml
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

The native path does not require Docker. Docker remains useful for integration,
packaging, and installer validation.

## Validation

Run the smallest relevant checks while iterating, then the full local gate:

```bash
python -m ruff check .
python -m mypy src
python scripts/generate_architecture.py --check
python -m pytest tests -q --basetemp .pytest_tmp_local -p no:cacheprovider
python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_cov -p no:cacheprovider
```

Focused examples:

```bash
python -m pytest tests/test_openrouter.py -q --basetemp .pytest_tmp_openrouter -p no:cacheprovider
python -m pytest tests/test_tokens.py -q --basetemp .pytest_tmp_tokens -p no:cacheprovider
python -m pytest tests/test_benchmarks.py -q --basetemp .pytest_tmp_benchmarks -p no:cacheprovider
python scripts/provider_smoke.py --json
```

For installer, Docker, startup, auth, or CI-sensitive changes, also run:

```bash
sh -n install.sh
sh -n uninstall.sh
```

Use the PowerShell parser checks from `.github/workflows/ci.yml` on Windows or
when PowerShell is available. Do not push until the relevant local gate is
green.

## Subsystem expectations

### Provider adapters

Test retryable and non-retryable errors separately, and test streaming and
non-streaming paths separately. Normalize provider errors in the adapter rather
than adding provider conditionals to proxy or routing code.

### Routing and proxy

Preserve provider-agnostic orchestration. Keep capability and token requirement
parsing in `src/tokens.py` where appropriate, and verify `/readyz` when startup,
provider gating, or discovery behavior changes. Streaming may fail over only
before any response content has been emitted.

### SQLite and persistence

All application writes go through the DB writer thread. Schema changes require
migration-safe tests in `tests/test_db.py`. Preserve the priority split that
allows low-priority request logs to drop while metadata writes receive queue
protection.

### Runtime logging

Runtime logs are queue-backed process events and are separate from durable
SQLite request telemetry. If logging changes, keep the `runtime_logging` admin
health payload, config example, and tests aligned. Never put raw prompts,
provider secrets, or classifier payloads into logs or durable telemetry.

### Token estimation

The current policy is local-only: use safe exact local tokenizers when available
and calibrated heuristics for unresolved families. Do not add remote token-count
calls on the request path without a design decision. Preserve safe Hugging Face
loading (`trust_remote_code=False`), alias tests, and token-review telemetry.

### Benchmark ingestion

Benchmark ingestion is best-effort. Preserve source freshness handling and
fallback parsing for upstream artifacts. A benchmark failure should reduce
enrichment quality, not stop startup or routing.

## Configuration and documentation sync

When public behavior, configuration, scheduler behavior, auth, persistence,
logging, request sizing, or deployment behavior changes:

- update the relevant implementation and tests;
- update the one authoritative document (`README.md`, `docs/architecture.md`,
  `docs/operations.md`, or `docs/roadmap.md`);
- update `config.yaml.example` or `.env.example` when the configuration surface
  changes;
- add a concise user-visible entry to `CHANGELOG.md` when appropriate.

Do not create a new status/spec/task document for a temporary plan. Put open
work in `docs/roadmap.md`, stable operational guidance in `docs/operations.md`,
and stable design intent in `docs/architecture.md`.

## Installers and releases

Installer changes must preserve non-interactive environment overrides and avoid
destructive host-level side effects. Release-facing changes should run the full
validation gate, then follow this order:

1. push to `main`;
2. wait for main CI to pass;
3. create/push a semver tag only after CI is green.

The release workflow and CI files are the source of truth for packaging and
release automation.

## Pull requests

Target `main`. Include:

- what changed and why;
- user/operator impact;
- validation actually run and anything skipped;
- intentional follow-up work or risks.

Use Conventional Commit prefixes such as `feat:`, `fix:`, `docs:`, `test:`,
and `chore:`.

## Bug and feature reports

Bug reports should include OS, Python/Docker version, commit or release,
reproduction steps, relevant logs, and relevant `/admin/health` or
`/admin/logs` data. Feature requests should state the concrete use case and
whether the request is runtime-, operator-, or developer-facing.
