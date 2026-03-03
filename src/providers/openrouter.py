from __future__ import annotations

from typing import Any

import httpx

from src.providers.base import ChatResult, ProviderFatalError, ProviderRetryableError


class OpenRouterAdapter:
    name = "openrouter"

    def __init__(self, api_key: str, api_base: str = "https://openrouter.ai/api/v1") -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def discover_models(self) -> list[dict[str, Any]]:
        if not self.api_key:
            return [self._fallback_model()]

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(f"{self.api_base}/models", headers=self._headers())
            if response.status_code >= 500:
                raise ProviderRetryableError(f"openrouter discovery failed: {response.status_code}")
            if response.status_code >= 400:
                raise ProviderFatalError(f"openrouter discovery unauthorized: {response.status_code}")
            payload = response.json()
            models = []
            for row in payload.get("data", []):
                model_id = row.get("id")
                if not model_id:
                    continue
                models.append(
                    {
                        "id": f"openrouter/{model_id}",
                        "name": model_id,
                        "provider_id": self.name,
                        "provider_model_id": model_id,
                        "provider_base_url": self.api_base,
                        "provider_api_key_env": "OPENROUTER_API_KEY",
                        "context_window": row.get("context_length") or 4096,
                        "max_output_tokens": row.get("max_completion_tokens"),
                        "supports_tools": 1,
                        "supports_vision": 1 if "vision" in model_id.lower() else 0,
                        "supports_streaming": 1,
                        "supports_structured_output": 0,
                        "supports_system_messages": 1,
                        "openrouter_rank": None,
                        "chatbot_arena_elo": None,
                        "open_llm_score": None,
                        "is_healthy": 1,
                    }
                )
            return models or [self._fallback_model()]
        except httpx.TimeoutException as exc:
            raise ProviderRetryableError("openrouter discovery timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderRetryableError("openrouter discovery transport error") from exc

    async def chat_completions(self, request_body: dict[str, Any], model: str) -> ChatResult:
        if not self.api_key:
            prompt = request_body.get("messages", [{}])[-1].get("content", "")
            return ChatResult(
                payload={
                    "id": "chatcmpl-freelunch-stub",
                    "object": "chat.completion",
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": f"Echo: {prompt}"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )

        body = dict(request_body)
        body["model"] = model
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{self.api_base}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
            if response.status_code in {408, 425, 429} or response.status_code >= 500:
                raise ProviderRetryableError(f"openrouter temporary error ({response.status_code})")
            if response.status_code >= 400:
                raise ProviderFatalError(f"openrouter fatal error ({response.status_code})")
            payload = response.json()
            usage = payload.get("usage", {})
            return ChatResult(
                payload=payload,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
        except httpx.TimeoutException as exc:
            raise ProviderRetryableError("provider timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderRetryableError("provider transport error") from exc

    def _fallback_model(self) -> dict[str, Any]:
        return {
            "id": "openrouter/openrouter/auto",
            "name": "openrouter/auto",
            "provider_id": self.name,
            "provider_model_id": "openrouter/auto",
            "provider_base_url": self.api_base,
            "provider_api_key_env": "OPENROUTER_API_KEY",
            "context_window": 4096,
            "max_output_tokens": None,
            "supports_tools": 1,
            "supports_vision": 1,
            "supports_streaming": 1,
            "supports_structured_output": 0,
            "supports_system_messages": 1,
            "openrouter_rank": 1,
            "chatbot_arena_elo": None,
            "open_llm_score": None,
            "is_healthy": 1,
        }
