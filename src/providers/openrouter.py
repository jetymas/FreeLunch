from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from src.providers.base import (
    ChatResult,
    GatewayErrorCategory,
    ProviderError,
    ProviderFatalError,
    ProviderRetryableError,
    ProviderRuntimeState,
    StreamResult,
    provider_error_from_error_payload,
)


def categorize_openrouter_error(
    status_code: int | None,
    error_code: str | None,
    message: str,
) -> tuple[GatewayErrorCategory, bool]:
    message_lower = message.lower()
    code_lower = (error_code or "").lower()

    if code_lower in {
        "context_length_exceeded",
        "max_tokens_exceeded",
        "token_limit_exceeded",
        "string_too_long",
    } or any(
        phrase in message_lower
        for phrase in {
            "context length",
            "maximum context length",
            "too many tokens",
            "token limit",
        }
    ):
        return "CONTEXT_EXCEEDED", True
    if (
        status_code == 429
        or code_lower in {"429", "rate_limit_exceeded", "rate_limited"}
        or "rate limit" in message_lower
    ):
        return "RATE_LIMITED", True
    if status_code in {401, 402} or code_lower in {
        "401",
        "402",
        "invalid_api_key",
        "invalid_credentials",
        "insufficient_credits",
        "payment_required",
    }:
        return "AUTH_ERROR", False
    if status_code in {400, 403, 404} or code_lower in {
        "400",
        "403",
        "404",
        "invalid_request",
        "invalid_model",
        "moderation_error",
    }:
        return "INVALID_REQUEST", False
    if status_code in {408, 409, 425, 500, 502, 503, 504} or code_lower in {
        "408",
        "409",
        "425",
        "500",
        "502",
        "503",
        "504",
        "server_error",
        "provider_error",
    }:
        return "PROVIDER_UNAVAILABLE", True
    if status_code is None:
        return "PROVIDER_UNAVAILABLE", True
    if status_code >= 500:
        return "PROVIDER_UNAVAILABLE", True
    return "INVALID_REQUEST", False


class OpenRouterAdapter:
    name = "openrouter"

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://openrouter.ai/api/v1",
        *,
        dev_stub_enabled: bool = False,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.dev_stub_enabled = dev_stub_enabled

    def runtime_state(self) -> ProviderRuntimeState:
        runtime_available = bool(self.api_key) or self.dev_stub_enabled
        return ProviderRuntimeState(
            discovery_available=runtime_available,
            inference_available=runtime_available,
        )

    def categorize_error(
        self,
        status_code: int | None,
        error_code: str | None,
        message: str,
    ) -> tuple[GatewayErrorCategory, bool]:
        return categorize_openrouter_error(status_code, error_code, message)

    def error_from_payload(
        self, payload: object, *, default_message: str = "provider stream error"
    ) -> ProviderError | None:
        return provider_error_from_error_payload(
            payload,
            categorize_error=self.categorize_error,
            default_message=default_message,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def discover_models(self) -> list[dict[str, Any]]:
        if not self.api_key:
            if not self.dev_stub_enabled:
                raise ProviderFatalError(
                    "openrouter api key is required",
                    category="AUTH_ERROR",
                    status_code=401,
                )
            return [self._fallback_model()]

        response = await self._request_with_retries("GET", "/models", timeout_seconds=15)
        payload = response.json()
        models = []
        for index, row in enumerate(payload.get("data", []), start=1):
            model_id = row.get("id")
            if not model_id or not self._is_free_model(row):
                continue

            supported_parameters = {
                str(value).lower()
                for value in row.get("supported_parameters", [])
                if value is not None
            }
            tokenizer_family = row.get("architecture", {}).get("tokenizer")
            input_modalities = {
                str(value).lower()
                for value in row.get("architecture", {}).get("input_modalities", [])
                if value is not None
            }
            max_completion_tokens = row.get("top_provider", {}).get(
                "max_completion_tokens"
            ) or row.get("max_completion_tokens")

            models.append(
                {
                    "id": f"openrouter/{model_id}",
                    "name": row.get("name") or model_id,
                    "provider_id": self.name,
                    "provider_model_id": model_id,
                    "provider_base_url": self.api_base,
                    "provider_api_key_env": "OPENROUTER_API_KEY",
                    "context_window": row.get("context_length") or 4096,
                    "max_output_tokens": max_completion_tokens,
                    "tokenizer_family": tokenizer_family,
                    "supports_tools": 1 if "tools" in supported_parameters else 0,
                    # OpenRouter's supported_parameters is not a strict stream-capability contract.
                    # Some free models stream correctly even when "stream" is omitted.
                    "supports_streaming": 1,
                    "supports_vision": 1 if "image" in input_modalities else 0,
                    "supports_structured_output": 1
                    if self._supports_structured_output(supported_parameters)
                    else 0,
                    "supports_system_messages": 1,
                    "openrouter_rank": index,
                    "chatbot_arena_elo": None,
                    "open_llm_score": None,
                    "is_healthy": 1,
                }
            )
        return models

    async def chat_completions(self, request_body: dict[str, Any], model: str) -> ChatResult:
        if not self.api_key:
            if not self.dev_stub_enabled:
                raise ProviderFatalError(
                    "openrouter api key is required",
                    category="AUTH_ERROR",
                    status_code=401,
                )
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
        start = time.monotonic()
        response = await self._request_with_retries(
            "POST",
            "/chat/completions",
            json_body=body,
            timeout_seconds=60,
        )
        payload = response.json()
        usage = payload.get("usage", {})
        latency_ms = int((time.monotonic() - start) * 1000)
        return ChatResult(
            payload=payload,
            latency_ms=latency_ms,
            ttfb_ms=latency_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )

    async def stream_chat_completions(
        self, request_body: dict[str, Any], model: str
    ) -> StreamResult:
        if not self.api_key:
            if not self.dev_stub_enabled:
                raise ProviderFatalError(
                    "openrouter api key is required",
                    category="AUTH_ERROR",
                    status_code=401,
                )
            return StreamResult(events=self._stub_stream(model, request_body))

        body = dict(request_body)
        body["model"] = model
        body["stream"] = True

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=None, connect=15.0, read=None, write=30.0, pool=15.0)
        )
        try:
            response = await client.send(
                client.build_request(
                    "POST",
                    f"{self.api_base}/chat/completions",
                    headers=self._headers(),
                    json=body,
                ),
                stream=True,
            )
            if response.status_code >= 400:
                raw_body = await response.aread()
                await response.aclose()
                await client.aclose()
                self._raise_for_response(response.status_code, raw_body)
        except Exception:
            await client.aclose()
            raise

        async def event_stream() -> AsyncGenerator[bytes, None]:
            pending_lines: list[str] = []
            try:
                async for line in response.aiter_lines():
                    if not line:
                        if pending_lines:
                            yield ("\n".join(pending_lines) + "\n\n").encode("utf-8")
                            pending_lines.clear()
                        continue
                    if line.startswith(":"):
                        continue
                    pending_lines.append(line)
                if pending_lines:
                    yield ("\n".join(pending_lines) + "\n\n").encode("utf-8")
            except httpx.TimeoutException as exc:
                raise ProviderRetryableError(
                    "provider stream timeout",
                    category="PROVIDER_UNAVAILABLE",
                ) from exc
            except httpx.HTTPError as exc:
                raise ProviderRetryableError(
                    "provider stream transport error",
                    category="PROVIDER_UNAVAILABLE",
                ) from exc
            finally:
                await response.aclose()
                await client.aclose()

        return StreamResult(events=event_stream())

    async def probe(
        self, model: str, *, max_tokens: int = 1, timeout_seconds: int = 15
    ) -> ChatResult:
        body = {
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": max_tokens,
            "stream": False,
        }
        if not self.api_key:
            return await self.chat_completions(body, model=model)

        return await self.chat_completions(body, model=model)

    def _is_free_model(self, row: dict[str, Any]) -> bool:
        pricing = row.get("pricing", {})
        prompt_price = str(pricing.get("prompt", "")).strip()
        completion_price = str(pricing.get("completion", "")).strip()
        return prompt_price == "0" and completion_price == "0"

    def _supports_structured_output(self, supported_parameters: set[str]) -> bool:
        return any(
            value in supported_parameters
            for value in {
                "response_format",
                "json_schema",
                "structured_outputs",
                "structured_output",
            }
        )

    async def _request_with_retries(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout_seconds: int,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    response = await client.request(
                        method,
                        f"{self.api_base}{path}",
                        headers=self._headers(),
                        json=json_body,
                    )
                if response.status_code >= 400:
                    self._raise_for_response(response.status_code, response.content)
                return response
            except ProviderRetryableError as exc:
                last_error = exc
                continue
            except httpx.TimeoutException:
                last_error = ProviderRetryableError(
                    "provider timeout",
                    category="PROVIDER_UNAVAILABLE",
                )
            except httpx.HTTPError as exc:
                last_error = ProviderRetryableError(
                    "provider transport error",
                    category="PROVIDER_UNAVAILABLE",
                )
                last_error.__cause__ = exc
        if isinstance(last_error, Exception):
            raise last_error
        raise ProviderRetryableError("provider request failed", category="PROVIDER_UNAVAILABLE")

    def _raise_for_response(self, status_code: int, raw_body: bytes) -> None:
        message, error_code = self._extract_error_details(raw_body)
        normalized_message = message or f"openrouter error ({status_code})"
        category, retryable = self._categorize_error(status_code, error_code, normalized_message)
        if retryable:
            raise ProviderRetryableError(
                normalized_message,
                category=category,
                status_code=status_code,
                error_code=error_code,
            )
        raise ProviderFatalError(
            normalized_message,
            category=category,
            status_code=status_code,
            error_code=error_code,
        )

    def _extract_error_details(self, raw_body: bytes) -> tuple[str | None, str | None]:
        if not raw_body:
            return None, None
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            text = raw_body.decode("utf-8", errors="ignore").strip()
            return text[:500] or None, None

        error = payload.get("error", payload)
        message = error.get("message") if isinstance(error, dict) else None
        error_code = error.get("code") if isinstance(error, dict) else None
        if error_code is not None:
            error_code = str(error_code)
        return (str(message)[:500] if message else None, error_code)

    def _categorize_error(
        self,
        status_code: int,
        error_code: str | None,
        message: str,
    ) -> tuple[GatewayErrorCategory, bool]:
        return self.categorize_error(status_code, error_code, message)

    async def _stub_stream(
        self, model: str, request_body: dict[str, Any]
    ) -> AsyncGenerator[bytes, None]:
        prompt = request_body.get("messages", [{}])[-1].get("content", "")
        chunk = {
            "id": "chatcmpl-freelunch-stub",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [
                {"index": 0, "delta": {"content": f"Echo: {prompt}"}, "finish_reason": "stop"}
            ],
        }
        yield f"data: {json.dumps(chunk)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    def _fallback_model(self) -> dict[str, Any]:
        return {
            "id": "openrouter/openrouter/free",
            "name": "openrouter/free",
            "provider_id": self.name,
            "provider_model_id": "openrouter/free",
            "provider_base_url": self.api_base,
            "provider_api_key_env": "OPENROUTER_API_KEY",
            "context_window": 4096,
            "max_output_tokens": None,
            "supports_tools": 1,
            "supports_streaming": 1,
            "supports_vision": 1,
            "supports_structured_output": 0,
            "supports_system_messages": 1,
            "openrouter_rank": 1,
            "chatbot_arena_elo": None,
            "open_llm_score": None,
            "is_healthy": 1,
        }
