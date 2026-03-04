# FreeLunch Task List

This file is the actionable backlog derived from the current codebase review.

Use it together with:
- `FREELUNCH_SPEC_v8.md` for the authoritative target behavior
- `SPEC_GAP_REVIEW.md` for the current spec-alignment snapshot
- `AGENTS.md` for repo-specific execution guidance

## P0 Correctness

## P1 Reliability And Observability

## P1 Configuration Parity

## P2 Testing And Quality

## P2 Release And Repository

- [ ] Keep benchmark-ingestion tests and parsing logic current as upstream Arena/Open LLM artifacts drift.
  Files: `src/benchmarks.py`, `tests/test_benchmarks.py`, `SPEC_GAP_REVIEW.md`, `OPERATIONS.md`

## P2 Documentation

- [ ] Keep `SPEC_GAP_REVIEW.md` current whenever a spec-facing feature lands.
- [ ] Keep `OPERATIONS.md`, `README.md`, and `config.yaml.example` synchronized with runtime behavior when admin payloads, config surface, or logging/tokenization behavior changes.
- [ ] Keep this task list pruned: move completed items out instead of letting it become a second changelog.
