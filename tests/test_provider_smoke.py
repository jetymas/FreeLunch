from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_provider_smoke_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "provider_smoke.py"
    spec = importlib.util.spec_from_file_location("provider_smoke_script", script_path)
    if spec is None or spec.loader is None:
        raise AssertionError("failed to load provider_smoke script module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(slots=True)
class _FakeRegistered:
    name: str
    adapter: object
    discovery_enabled: bool
    inference_enabled: bool


class _FakeSettings:
    def __init__(
        self,
        providers_enabled: tuple[str, ...],
        *,
        enabled_overrides: dict[str, bool] | None = None,
        discovery_overrides: dict[str, bool] | None = None,
        inference_overrides: dict[str, bool] | None = None,
    ) -> None:
        self.providers_enabled = providers_enabled
        self._enabled_overrides = enabled_overrides or {}
        self._discovery_overrides = discovery_overrides or {}
        self._inference_overrides = inference_overrides or {}

    def is_provider_enabled(self, provider_id: str) -> bool:
        if provider_id in self._enabled_overrides:
            return bool(self._enabled_overrides[provider_id])
        return provider_id in self.providers_enabled

    def is_provider_discovery_enabled(self, provider_id: str) -> bool:
        if provider_id in self._discovery_overrides:
            return bool(self._discovery_overrides[provider_id])
        return self.is_provider_enabled(provider_id)

    def is_provider_inference_enabled(self, provider_id: str) -> bool:
        if provider_id in self._inference_overrides:
            return bool(self._inference_overrides[provider_id])
        return self.is_provider_enabled(provider_id)


class _RuntimeUnavailableAdapter:
    def runtime_state(self):
        return SimpleNamespace(discovery_available=False, inference_available=False)

    async def discover_models(self):
        raise AssertionError("discover_models should not be called when runtime is unavailable")


class _PassingAdapter:
    def runtime_state(self):
        return SimpleNamespace(discovery_available=True, inference_available=True)

    async def discover_models(self):
        return [{"id": "provider/model-1"}]


class _FailingAdapter:
    def runtime_state(self):
        return SimpleNamespace(discovery_available=True, inference_available=True)

    async def discover_models(self):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_run_provider_smoke_skips_runtime_unavailable_provider(monkeypatch):
    module = _load_provider_smoke_module()
    registry_object = SimpleNamespace(
        register_configured=lambda _settings: None,
        all_registered=lambda: [
            _FakeRegistered(
                name="openai",
                adapter=_RuntimeUnavailableAdapter(),
                discovery_enabled=True,
                inference_enabled=True,
            )
        ],
    )
    monkeypatch.setattr(module, "ProviderRegistry", lambda: registry_object)

    settings = _FakeSettings(("openai",))
    results = await module.run_provider_smoke(settings)

    assert len(results) == 1
    assert results[0].provider_id == "openai"
    assert results[0].status == "skip"
    assert "runtime capability unavailable" in results[0].message
    assert module.compute_exit_code(results) == 0


@pytest.mark.asyncio
async def test_run_provider_smoke_reports_pass_fail_and_unregistered(monkeypatch):
    module = _load_provider_smoke_module()
    registry_object = SimpleNamespace(
        register_configured=lambda _settings: None,
        all_registered=lambda: [
            _FakeRegistered(
                name="openai",
                adapter=_PassingAdapter(),
                discovery_enabled=True,
                inference_enabled=True,
            ),
            _FakeRegistered(
                name="bad",
                adapter=_FailingAdapter(),
                discovery_enabled=True,
                inference_enabled=True,
            ),
        ],
    )
    monkeypatch.setattr(module, "ProviderRegistry", lambda: registry_object)

    settings = _FakeSettings(("openai", "bad", "ghost"))
    results = await module.run_provider_smoke(settings)

    by_provider = {result.provider_id: result for result in results}
    assert by_provider["openai"].status == "pass"
    assert by_provider["openai"].discovered_models == 1
    assert by_provider["bad"].status == "fail"
    assert "RuntimeError: boom" in by_provider["bad"].message
    assert by_provider["ghost"].status == "fail"
    assert "not registered" in by_provider["ghost"].message
    assert module.compute_exit_code(results) == 1
