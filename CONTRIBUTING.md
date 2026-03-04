# Contributing

## Reporting bugs
Please include:
- OS
- Python version and/or Docker version
- gateway version or commit
- relevant request/admin log output
- clear reproduction steps

## Requesting features
Please open a feature request issue with a concrete use case.

## Development setup
1. Create and activate a virtual environment.
2. Install dependencies with `pip install -r requirements.txt -r requirements-dev.txt`.
3. Copy `.env.example` if you want local environment defaults.
4. Run the app with `uvicorn src.main:app --reload`.
5. Use `README.md`, `SPEC_GAP_REVIEW.md`, `TASKS.md`, and `AGENTS.md` to understand the current repo state before starting larger work.

## Code style
Run:
- `ruff check .`
- `ruff format --check .`
- `mypy src`

## Testing
Run:
- `pytest tests/ -q --basetemp .pytest_tmp_local -p no:cacheprovider`
- `pytest tests/ --cov=src --cov-report=term-missing`

Coverage is reported in CI today, but the repo does not yet enforce the full intended coverage threshold from the project spec.

## Pull requests
- Target `main`
- Use descriptive title and explain why the change is needed
- Link related issues when possible
- Update docs when the change affects public behavior, config, or spec alignment

## Commit messages
Use Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
