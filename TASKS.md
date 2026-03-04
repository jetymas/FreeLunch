# FreeLunch Task List

This file is the actionable backlog derived from the current codebase review.

Use it together with:
- `FREELUNCH_SPEC_v8.md` for the authoritative target behavior
- `SPEC_GAP_REVIEW.md` for the current spec-alignment snapshot
- `AGENTS.md` for repo-specific execution guidance

## P0 Correctness

- [ ] Tighten benchmark refresh toward full spec parity: honor source cache hours more strictly and replace the current Chatbot Arena CSV score proxy with richer ELO snapshot ingestion.
  Files: `src/benchmarks.py`, `src/config.py`, `src/discover.py`, `tests/test_benchmarks.py`, `tests/test_db.py`

## P1 Reliability And Observability

- [ ] Replace the current family-aware heuristic request sizing with true tokenizer-backed estimation for common model families when libraries/metadata are available.
  Files: `src/tokens.py`, `src/proxy.py`, `src/routing.py`, `tests/test_api.py`
- [ ] Decide whether the DB writer queue should stay unbounded; if not, add bounded backpressure/drop policy that protects metadata writes.
  Files: `src/db.py`, `src/proxy.py`, `src/discover.py`, `src/health.py`

## P1 Configuration Parity

- [ ] Finish config/runtime parity for provider enablement and remaining logging controls (`providers.enabled`, provider `enabled` / `discovery_enabled` / `inference_enabled`, and any still-unused logging knobs).
  Files: `src/config.py`, `src/main.py`, `src/providers/registry.py`, `README.md`, `tests/test_app.py`

## P2 Testing And Quality

## P2 Release And Repository

- [ ] Add Section 15 installer assets if that part of the spec remains in scope.
  Files: `install.sh`, `uninstall.sh`, `install.ps1`, `uninstall.ps1`, `README.md`

## P2 Documentation

- [ ] Keep `SPEC_GAP_REVIEW.md` current whenever a spec-facing feature lands.
- [ ] Keep this task list pruned: move completed items out instead of letting it become a second changelog.
