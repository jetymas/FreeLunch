from __future__ import annotations

import importlib


def _route_paths(app) -> set[str]:
    paths: set[str] = set()
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)
        router = getattr(route, "original_router", None)
        pending.extend(getattr(router, "routes", ()))
    return paths


def test_api_docs_are_disabled_in_production_by_default(monkeypatch):
    import src.main as main_module

    with monkeypatch.context() as scoped:
        scoped.setenv("APP_ENV", "production")
        scoped.delenv("API_DOCS_ENABLED", raising=False)
        main_module = importlib.reload(main_module)
        paths = _route_paths(main_module.app)
        assert "/docs" not in paths
        assert "/redoc" not in paths
        assert "/openapi.json" not in paths
        assert "/healthz" in paths
        assert "/readyz" in paths
        assert "/admin/ui" in paths
    importlib.reload(main_module)


def test_api_docs_are_disabled_for_production_config_without_environment_override(
    tmp_path, monkeypatch
):
    import src.main as main_module

    (tmp_path / "config.yaml").write_text("app:\n  env: prod\n", encoding="utf-8")
    with monkeypatch.context() as scoped:
        scoped.chdir(tmp_path)
        scoped.delenv("APP_ENV", raising=False)
        scoped.delenv("API_DOCS_ENABLED", raising=False)
        main_module = importlib.reload(main_module)
        paths = _route_paths(main_module.app)
        assert "/docs" not in paths
        assert "/redoc" not in paths
        assert "/openapi.json" not in paths
    importlib.reload(main_module)


def test_api_docs_can_be_explicitly_enabled_in_production(monkeypatch):
    import src.main as main_module

    with monkeypatch.context() as scoped:
        scoped.setenv("APP_ENV", "prod")
        scoped.setenv("API_DOCS_ENABLED", "true")
        main_module = importlib.reload(main_module)
        paths = _route_paths(main_module.app)
        assert {"/docs", "/redoc", "/openapi.json"} <= paths
    importlib.reload(main_module)


def test_development_docs_remain_available(monkeypatch):
    import src.main as main_module

    with monkeypatch.context() as scoped:
        scoped.setenv("APP_ENV", "dev")
        scoped.delenv("API_DOCS_ENABLED", raising=False)
        main_module = importlib.reload(main_module)
        paths = _route_paths(main_module.app)
        assert {"/docs", "/redoc", "/openapi.json"} <= paths
    importlib.reload(main_module)
