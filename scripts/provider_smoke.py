from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Settings  # noqa: E402
from src.providers.base import ProviderError  # noqa: E402
from src.providers.registry import ProviderRegistry, RegisteredProvider  # noqa: E402


@dataclass(slots=True)
class ProviderSmokeResult:
    provider_id: str
    status: str
    message: str
    discovered_models: int | None = None
    elapsed_ms: int | None = None


def _sync_registry_runtime_gating(settings: Settings, registry: ProviderRegistry) -> None:
    for registered in registry.all_registered():
        configured_discovery_enabled = settings.is_provider_discovery_enabled(registered.name)
        configured_inference_enabled = settings.is_provider_inference_enabled(registered.name)
        runtime_state = registered.adapter.runtime_state()
        registered.discovery_enabled = (
            configured_discovery_enabled and runtime_state.discovery_available
        )
        registered.inference_enabled = (
            configured_inference_enabled and runtime_state.inference_available
        )


def _target_provider_ids(settings: Settings, provider_filter: Sequence[str] | None) -> list[str]:
    if provider_filter:
        out: list[str] = []
        for provider_id in provider_filter:
            normalized = str(provider_id).strip()
            if normalized and normalized not in out:
                out.append(normalized)
        return out

    out = [provider_id for provider_id in settings.providers_enabled if provider_id.strip()]
    return list(dict.fromkeys(out))


async def _smoke_registered_provider(registered: RegisteredProvider) -> ProviderSmokeResult:
    runtime_state = registered.adapter.runtime_state()
    if not runtime_state.discovery_available and not runtime_state.inference_available:
        return ProviderSmokeResult(
            provider_id=registered.name,
            status="skip",
            message="runtime capability unavailable (credentials not configured)",
        )
    if not registered.discovery_enabled:
        return ProviderSmokeResult(
            provider_id=registered.name,
            status="skip",
            message="discovery smoke skipped because discovery is disabled",
        )

    start = time.monotonic()
    try:
        models = await registered.adapter.discover_models()
    except ProviderError as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ProviderSmokeResult(
            provider_id=registered.name,
            status="fail",
            message=f"{exc.category}: {str(exc)[:300]}",
            elapsed_ms=elapsed_ms,
        )
    except Exception as exc:  # pragma: no cover - defensive formatting path
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ProviderSmokeResult(
            provider_id=registered.name,
            status="fail",
            message=f"{type(exc).__name__}: {str(exc)[:300]}",
            elapsed_ms=elapsed_ms,
        )

    elapsed_ms = int((time.monotonic() - start) * 1000)
    model_count = len(models) if isinstance(models, list) else 0
    if model_count <= 0:
        return ProviderSmokeResult(
            provider_id=registered.name,
            status="fail",
            message="discover_models returned no models",
            discovered_models=0,
            elapsed_ms=elapsed_ms,
        )
    return ProviderSmokeResult(
        provider_id=registered.name,
        status="pass",
        message="discovery smoke succeeded",
        discovered_models=model_count,
        elapsed_ms=elapsed_ms,
    )


async def run_provider_smoke(
    settings: Settings, *, provider_filter: Sequence[str] | None = None
) -> list[ProviderSmokeResult]:
    registry = ProviderRegistry()
    registry.register_configured(settings)
    _sync_registry_runtime_gating(settings, registry)

    registered_by_name = {registered.name: registered for registered in registry.all_registered()}
    results: list[ProviderSmokeResult] = []
    for provider_id in _target_provider_ids(settings, provider_filter):
        if not settings.is_provider_enabled(provider_id):
            results.append(
                ProviderSmokeResult(
                    provider_id=provider_id,
                    status="skip",
                    message="provider disabled by config",
                )
            )
            continue

        registered = registered_by_name.get(provider_id)
        if registered is None:
            results.append(
                ProviderSmokeResult(
                    provider_id=provider_id,
                    status="fail",
                    message="provider is enabled but not registered",
                )
            )
            continue

        results.append(await _smoke_registered_provider(registered))
    return results


def compute_exit_code(results: Sequence[ProviderSmokeResult]) -> int:
    return 1 if any(result.status == "fail" for result in results) else 0


def _summary_counts(results: Sequence[ProviderSmokeResult]) -> dict[str, int]:
    return {
        "pass": sum(1 for result in results if result.status == "pass"),
        "fail": sum(1 for result in results if result.status == "fail"),
        "skip": sum(1 for result in results if result.status == "skip"),
        "total": len(results),
    }


def _print_results(results: Sequence[ProviderSmokeResult], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        for result in results:
            suffix = ""
            if result.discovered_models is not None:
                suffix = f" models={result.discovered_models}"
            if result.elapsed_ms is not None:
                suffix = f"{suffix} elapsed_ms={result.elapsed_ms}"
            print(
                f"[{result.status.upper():4}] {result.provider_id}: {result.message}{suffix}",
            )

    counts = _summary_counts(results)
    print(
        "summary:"
        f" pass={counts['pass']}"
        f" fail={counts['fail']}"
        f" skip={counts['skip']}"
        f" total={counts['total']}"
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Manual provider smoke harness. Performs discovery-based checks for configured providers "
            "and reports pass/fail/skip per provider."
        )
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config YAML (default: config.yaml)",
    )
    parser.add_argument(
        "--provider",
        action="append",
        default=[],
        help="Provider ID to smoke-check (repeatable). Defaults to providers.enabled.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit per-provider results as JSON.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = Settings.from_env(args.config)
    results = asyncio.run(run_provider_smoke(settings, provider_filter=args.provider))
    _print_results(results, as_json=bool(args.json))
    return compute_exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
