# Contributing

## Start with the repo state, not assumptions

Read these in order before making larger changes:

1. `FREELUNCH_SPEC_v8.md`
2. `SPEC_GAP_REVIEW.md`
3. `TASKS.md`
4. `README.md`
5. `AGENTS.md`

Use them together:

- `FREELUNCH_SPEC_v8.md` is the full target behavior
- `SPEC_GAP_REVIEW.md` is the current implementation-vs-spec snapshot
- `TASKS.md` is the active backlog
- `README.md` is the operator-facing runtime guide
- `AGENTS.md` is the repo-specific engineering ruleset

## Project principles

FreeLunch is deliberately conservative. Preserve that bias.

- Keep provider-specific behavior inside `src/providers/*`.
- Keep routing, health, and proxy orchestration provider-agnostic.
- Treat SQLite as a single-node system with one authoritative writer path.
- Prefer clear, low-overhead designs over abstraction sprawl or concurrency-heavy cleverness.
- Keep timestamps canonical: UTC ISO 8601 with a `Z` suffix.
- If behavior changes relative to the spec, update the docs in the same branch.

## Local development setup

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

If you want local env defaults:

```bash
cp .env.example .env
cp config.yaml.example config.yaml
```

## Validation commands

Minimum common validation:

```bash
python -m ruff check .
python -m mypy src
python -m pytest tests -q --basetemp .pytest_tmp_local -p no:cacheprovider
python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_cov -p no:cacheprovider
```

Focused validation is preferred while you iterate. Run the smallest meaningful test set first, then broaden only as needed.

Examples:

```bash
python -m pytest tests/test_openrouter.py -q --basetemp .pytest_tmp_openrouter -p no:cacheprovider
python -m pytest tests/test_tokens.py -q --basetemp .pytest_tmp_tokens -p no:cacheprovider
python -m pytest tests/test_benchmarks.py -q --basetemp .pytest_tmp_benchmarks -p no:cacheprovider
```

## Current high-value areas

Changes in these areas deserve extra care and focused regression coverage:

- `src/providers/openrouter.py`
- `src/proxy.py`
- `src/tokens.py`
- `src/benchmarks.py`
- `src/health.py`
- `src/db.py`
- `src/config.py`
- `src/runtime_logging.py`

## Expectations by subsystem

### Provider adapters

When touching provider behavior:

- test retryable vs non-retryable paths separately
- test streaming and non-streaming separately
- keep normalization logic in the adapter, not in proxy/routing/health

The OpenRouter adapter is now directly covered for:

- retry exhaustion
- raw-body error fallback parsing
- stream setup and transport failures
- explicit dev-stub chat and stream behavior

Do not regress that direct adapter coverage by moving confidence back to only indirect API tests.

### Routing and proxy

When touching request handling:

- preserve provider-agnostic orchestration
- keep request requirement parsing in `src/tokens.py` where appropriate
- keep routing, proxy, and token-estimation behavior aligned
- verify `/readyz` behavior if startup or provider gating semantics move

### SQLite and persistence

When touching persistence:

- all application writes still go through the DB writer thread
- add migration-safe regression coverage in `tests/test_db.py`
- preserve the bounded writer-queue priority split between low-priority client logs and higher-priority metadata writes

### Runtime logging

Runtime logs are queue-backed operational events, separate from durable SQLite request telemetry.

If you change runtime logging:

- keep `README.md`, `config.yaml.example`, and tests aligned
- keep `/admin/health.runtime_logging` shape aligned with code
- preserve the `concise` / `verbose` / `debug` contract
- remember that debug mode is intentionally very chatty
- keep cancellation of background tokenizer preloads as a debug-only expected event rather than a warning-level failure

### Token estimation

Current policy:

- local-only token estimation is considered complete
- exact local counters are used where safely available
- calibrated heuristics cover the remaining unresolved families
- remote provider-native counting is not used on the request path
- tokenizer prewarming is intentionally not enabled by default

If you touch `src/tokens.py`:

- preserve the safe `tiktoken` path for OpenAI-compatible families
- preserve safe Hugging Face `AutoTokenizer` usage for resolvable non-OAI families
- keep alias normalization tested explicitly
- keep the heuristic classifier and profile tables aligned with regression tests
- document any operator-visible change to `/admin/health.token_estimation_review`

### Benchmark ingestion

Benchmark ingestion is resilient, not guaranteed.

If you change `src/benchmarks.py`:

- preserve best-effort behavior
- preserve source freshness handling
- preserve backward walking across parseable Chatbot Arena artifacts
- preserve compatibility with the current Open LLM dataset-server row page-size limit
- update docs if operator expectations or failure behavior change

## Installer and release changes

If you change:

- `install.sh`
- `uninstall.sh`
- `install.ps1`
- `uninstall.ps1`

also run:

```bash
sh -n install.sh
sh -n uninstall.sh
pwsh -Command "[System.Management.Automation.Language.Parser]::ParseFile('install.ps1',[ref]$null,[ref]$null) | Out-Null"
pwsh -Command "[System.Management.Automation.Language.Parser]::ParseFile('uninstall.ps1',[ref]$null,[ref]$null) | Out-Null"
```

Keep installer behavior aligned with the current Docker-first runtime model. Avoid destructive host-level side effects.

## Documentation expectations

Update docs in the same change whenever you alter:

- public API behavior
- runtime logging semantics
- config surface or defaults
- request-sizing behavior
- `/admin/health` payload shape
- scheduler behavior
- install or release workflow
- current spec alignment

In practice, that usually means updating one or more of:

- `README.md`
- `CONTRIBUTING.md`
- `config.yaml.example`
- `CHANGELOG.md`
- `SPEC_GAP_REVIEW.md`
- `TASKS.md`
- `AGENTS.md`

### Operator-facing doc sync rules

If the change affects operator behavior:

- document the runtime consequence, not just the implementation detail
- clarify whether the behavior is durable telemetry, runtime log output, or admin endpoint state
- update examples if request payloads or config toggles changed
- make sure production guidance still matches reality

## Pull requests

Target `main`.

Good PRs in this repo are specific and operationally clear. Include:

- what changed
- why it was needed
- operator-visible or user-visible impact
- validation actually run
- skipped validation and why
- any intentional partial follow-up work

If you changed logging or telemetry, call that out explicitly.

Keep the diff scoped to one workstream whenever possible.

## Commit messages

Use Conventional Commits:

- `feat:`
- `fix:`
- `docs:`
- `test:`
- `chore:`

## Reporting bugs

Please include:

- OS
- Python version and/or Docker version
- gateway version or commit
- reproduction steps
- relevant runtime log output
- relevant `/admin/health` or `/admin/logs` data when applicable

## Requesting features

Please file a request with:

- a concrete use case
- why the current behavior is insufficient
- whether the request is runtime-facing, operator-facing, or purely developer-facing
