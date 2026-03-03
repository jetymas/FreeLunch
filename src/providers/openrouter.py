from __future__ import annotations

from typing import Any

import httpx

from .base import ChatResult, ProviderFatalError, ProviderRetryableError


class OpenRouterAdapter:
    name = "openrouter"

    def __init__(self, api_key: str, base_url: str, timeout_s: float) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def discover_models(self) -> list[dict[str, Any]]:
        if not self.api_key:
            return []

        url = f"{self.base_url}/models"
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                response = client.get(url, headers=self._headers())
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise ProviderRetryableError("openrouter discovery unavailable") from exc
            raise ProviderFatalError("openrouter discovery request rejected") from exc
        except httpx.HTTPError as exc:
            raise ProviderRetryableError("openrouter discovery network error") from exc

        payload = response.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        models: list[dict[str, Any]] = []
        for item in data:
            model_id = item.get("id")
            if not model_id:
                continue
            models.append(
                {
                    "provider": self.name,
                    "model_name": model_id,
                    "display_name": item.get("name", model_id),
                    "supports_tools": int(self._supports_tools(item)),
                    "supports_vision": int(self._supports_vision(item)),
                    "supports_streaming": 1,
                    "is_healthy": 1,
                    "score": 1.0,
                }
            )

        return models

    def chat_completions(self, request_body: dict[str, Any], model: str) -> ChatResult:
        if not self.api_key:
            raise ProviderFatalError("OPENROUTER_API_KEY is not configured")

        body = dict(request_body)
        body["model"] = model

        url = f"{self.base_url}/chat/completions"
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                response = client.post(url, headers=self._headers(), json=body)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (408, 409, 429) or exc.response.status_code >= 500:
                raise ProviderRetryableError("openrouter retryable failure") from exc
            raise ProviderFatalError("openrouter rejected request") from exc
        except httpx.TimeoutException as exc:
            raise ProviderRetryableError("openrouter timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderRetryableError("openrouter network error") from exc

        payload = response.json()
        usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
        return ChatResult(
            payload=payload,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )

    @staticmethod
    def _supports_tools(item: dict[str, Any]) -> bool:
        supported_parameters = item.get("supported_parameters", [])
        return "tools" in supported_parameters or "tool_choice" in supported_parameters

    @staticmethod
    def _supports_vision(item: dict[str, Any]) -> bool:
        modalities = item.get("architecture", {}).get("input_modalities", [])
        return "image" in modalities
