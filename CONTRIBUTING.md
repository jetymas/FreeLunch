# Contributing

## Reporting bugs
Please open a bug issue and include OS, Docker version, gateway version, and relevant logs.

## Requesting features
Please open a feature request issue with a concrete use case.

## Development setup
1. Create venv
2. `pip install -r requirements.txt -r requirements-dev.txt`
3. `uvicorn src.main:app --reload`

## Code style
Run:
- `ruff check .`
- `ruff format .`

## Testing
Run:
- `pytest tests/ --cov=src`

## Pull requests
- Target `main`
- Use descriptive title and explain why the change is needed
- Link related issues when possible

## Commit messages
Use Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
