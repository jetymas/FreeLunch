# Testing Guide

This document is the canonical testing entrypoint for FreeLunch.

Use it with:

- `FREELUNCH_SPEC_v8.md` for the authoritative quality target
- `SPEC_GAP_REVIEW.md` for current alignment status
- `TASKS.md` for active implementation backlog
- `OPERATIONS.md` for deployment/runtime validation
- `RELEASE_VALIDATION_MATRIX.md` for manual cross-platform sign-off evidence
- `RELEASE_VALIDATION_EVIDENCE.md` for executed matrix results and blockers

## 1. Quality Target

FreeLunch now targets a first-class test posture with:

- coverage target: **97%+ line coverage** on `src/`
- branch-depth growth in high-risk modules (`proxy`, `providers`, `benchmarks`, `scheduler`, `tokens`)
- strong behavior depth, not just line coverage
- reproducible validation for both in-repo and outside-repo operation

Latest baseline now meets this target (`418 passed`, `97.68%` total `src/` line coverage). Remaining deltas are depth/realism oriented; see `SPEC_GAP_REVIEW.md` and `TASKS.md`.

## 2. Test Layers

A complete validation wave should include:

1. Unit tests
- deterministic pure-function and helper behavior
- edge-case parsing/coercion behavior

2. Integration tests
- FastAPI endpoints, DB interactions, scheduler-triggered behavior
- provider adapter + routing + health interactions

3. Contract tests
- provider error normalization invariants
- stream/event framing compatibility invariants
- schema robustness for provider discovery payloads

4. Fault-injection tests
- transport failures, malformed payloads, decode errors
- partial stream failures, pre-first-byte stream failures
- probe failures and fallback handling

5. Property/stress tests
- randomized routing invariants (no duplicates, bounded output, explicit model precedence)
- tokenizer estimation invariants (non-negative, monotonic with additional content)
- scheduler/probe race safety and budget invariants

6. Outside-repo validation
- installer + compose + container smoke on real runtime
- auth behavior and readiness checks in deployed mode
- optional low-budget live-provider checks

## 3. Standard Commands

Baseline local checks:

```bash
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy src
python -m pytest tests -q --basetemp .pytest_tmp_local -p no:cacheprovider
python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_cov -p no:cacheprovider
```

Focused suites (iterate fast first):

```bash
python -m pytest tests/test_api.py tests/test_app.py tests/test_routing.py tests/test_health.py -q --basetemp .pytest_tmp_runtime -p no:cacheprovider
python -m pytest tests/test_openai_compatible.py tests/test_openrouter.py -q --basetemp .pytest_tmp_providers -p no:cacheprovider
python -m pytest tests/test_benchmarks.py tests/test_tokens.py tests/test_db.py -q --basetemp .pytest_tmp_data -p no:cacheprovider
```

## 4. 97% Roadmap (Execution Plan)

The work is organized into parallel waves to increase both coverage and depth.

Current status:
- Wave A landed.
- Wave B landed.
- Wave C landed (property + stress/concurrency suites).
- Wave D landed for this release (CI container smoke + Linux real-daemon installer/runtime smoke + restricted-egress + low-budget live-provider checks complete; macOS matrix leg explicitly waived by project owner due host unavailability).

### Wave A: High-Impact Branch Closures

1. Runtime-path hardening tests (`src/proxy.py`, `src/routing.py`, `src/health.py`)
- generic exception fallback in non-stream path
- pre-first-byte stream failure failover
- mid-stream transport failure behavior
- invalid bearer token behavior
- routing preference and `:free` alias edge behavior
- probe failure/budget race paths

2. Provider contract depth (`src/providers/openai_compatible.py`, `src/providers/openrouter.py`, `src/providers/registry.py`)
- malformed/invalid JSON normalization
- discovery schema compatibility matrix
- retry exhaustion and transport error preservation
- stream framing and EOF edge handling
- registry dynamic import error/sanitization edge tests

### Wave B: Data/Scheduler/Token Depth

1. Benchmark ingestion robustness (`src/benchmarks.py`)
- recursive payload shape variants
- source fallback ordering edge cases
- malformed paging/row payload behavior
- one-source-fails/one-source-persists behavior

2. Scheduler behavior depth (`src/scheduler.py`)
- run/failure accounting invariants
- wrapper execution paths for ranking/health/maintenance/config refresh

3. Token pipeline depth (`src/tokens.py`)
- multimodal and structured metadata edge cases
- preload gating/concurrency and fallback-on-exact-failure behavior

### Wave C: Hard Testing Modes

1. Property-based tests (Hypothesis)
- routing invariants over randomized candidate sets
- token-estimation monotonicity/non-negative invariants
- benchmark pagination invariants (no duplicates/skips)

2. Stress/concurrency tests
- probe budget enforcement under concurrency
- tokenizer preload de-duplication under parallel requests

3. Mutation test pilot (nightly/non-blocking initially)
- target critical pure logic modules first
- use mutation score trend as depth signal

### Wave D: Outside-Repo Reliability

1. CI runtime realism improvements
- real-Docker Linux installer smoke (not only fake shim)
- auth-on smoke (401/200 checks)
- compose runtime smoke (`docker compose up`, `/healthz`, `/readyz`, `/v1/models`, tiny chat)

2. Manual release matrix
- native Windows Docker Desktop install/uninstall validation
- native macOS install/uninstall validation
- restricted egress/degraded network behavior check
- real-provider tiny non-stream + stream check (budget-limited)

## 5. Enforcement Strategy

To avoid unstable transitions, enforce progressively while closing gaps:

1. keep CI gate at current floor while Wave A/B lands
2. raise to 93 once high-risk provider/runtime branches land
3. raise to 95 after property/stress tests are stable
4. raise to **97** and treat regressions as release blockers

The target remains 97+; staged enforcement is only for migration safety.

## 6. Reporting Requirements

Each testing wave should report:

- coverage delta (overall and by changed modules)
- new hard-test categories added
- flaky tests discovered and mitigation
- runtime/deployment validation evidence
- remaining blockers and next wave recommendation

## 7. Latest Verification Snapshot

- `python -m ruff check src tests` -> pass
- `python -m mypy src` -> pass
- `python -m pytest tests --cov=src --cov-report=term-missing -q --basetemp .pytest_tmp_waveD_cov -p no:cacheprovider` -> `418 passed`, total coverage `97.68%`
