from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncGenerator, Mapping
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


def categorize_openai_compatible_error(
    status_code: int | None,
    error_code: str | None,
    message: str,
) -> tuple[GatewayErrorCategory, bool]:
    message_lower = message.lower()
    code_lower = (error_code or "").lower()

    if code_lower in {
        "context_length_exceeded",
        "context_window_exceeded",
        "max_tokens_exceeded",
        "token_limit_exceeded",
        "string_too_long",
    } or any(
        phrase in message_lower
        for phrase in {
            "context length",
            "maximum context length",
            "context window",
            "too many tokens",
            "token limit",
            "input is too long",
        }
    ):
        return "CONTEXT_EXCEEDED", True
    if (
        status_code == 429
        or code_lower in {"429", "rate_limit_exceeded", "rate_limited", "too_many_requests"}
        or "rate limit" in message_lower
    ):
        return "RATE_LIMITED", True
    if status_code in {401, 402, 403} or code_lower in {
        "401",
        "402",
        "403",
        "invalid_api_key",
        "invalid_key",
        "invalid_authentication",
        "authentication_error",
        "permission_denied",
        "insufficient_credits",
        "payment_required",
    }:
        return "AUTH_ERROR", False
    if status_code in {400, 404, 405, 409, 422} or code_lower in {
        "400",
        "404",
        "405",
        "409",
        "422",
        "invalid_request_error",
        "invalid_request",
        "invalid_model",
        "model_not_found",
        "unsupported_parameter",
    }:
        return "INVALID_REQUEST", False
    if status_code in {408, 425, 500, 502, 503, 504} or code_lower in {
        "408",
        "425",
        "500",
        "502",
        "503",
        "504",
        "timeout",
        "server_error",
        "internal_server_error",
        "service_unavailable",
    }:
        return "PROVIDER_UNAVAILABLE", True
    if status_code is None:
        return "PROVIDER_UNAVAILABLE", True
    if status_code >= 500:
        return "PROVIDER_UNAVAILABLE", True
    return "INVALID_REQUEST", False


def resolve_openai_compatible_credentials(
    provider_config: Mapping[str, Any],
    *,
    default_api_base: str,
    default_api_key_env: str,
) -> tuple[str, str, str]:
    api_base = str(provider_config.get("api_base", default_api_base)).strip() or default_api_base
    api_key_env = (
        str(provider_config.get("api_key_env", default_api_key_env)).strip() or default_api_key_env
    )
    api_key = os.getenv(api_key_env, "")
    if not api_key:
        api_key = str(provider_config.get("api_key", "")).strip()
    return api_key, api_base, api_key_env


class OpenAICompatibleAdapter:
    name = "openai_compatible"
    provider_api_key_env = "API_KEY"
    default_api_base = "https://api.openai.com/v1"
    default_context_window = 4096
    default_request_timeout_seconds = 60
    default_discovery_timeout_seconds = 15
    max_retries = 3

    def __init__(
        self,
        api_key: str,
        *,
        api_base: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_base = (api_base or self.default_api_base).rstrip("/")

    def runtime_state(self) -> ProviderRuntimeState:
        runtime_available = bool(self.api_key)
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
        return categorize_openai_compatible_error(status_code, error_code, message)

    def error_from_payload(
        self, payload: object, *, default_message: str = "provider stream error"
    ) -> ProviderError | None:
        return provider_error_from_error_payload(
            payload,
            categorize_error=self.categorize_error,
            default_message=default_message,
        )

    async def discover_models(self) -> list[dict[str, Any]]:
        self._assert_api_key()
        response = await self._request_with_retries(
            "GET",
            "/models",
            timeout_seconds=self.default_discovery_timeout_seconds,
        )
        payload = self._parse_json(response.content)
        rows = payload.get("data")
        if not isinstance(rows, list):
            return []

        models: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, Mapping):
                continue
            model_id = str(row.get("id") or "").strip()
            if not model_id:
                continue

            capabilities = self._extract_capabilities(row)
            input_modalities = self._extract_input_modalities(row)
            supports_tools = self._extract_bool(
                row,
                "supports_tools",
                "tool_calling",
                "supports_function_calling",
            )
            if supports_tools is None:
                supports_tools = any(
                    value in capabilities
                    for value in {
                        "tools",
                        "tool_use",
                        "tool_choice",
                        "function_calling",
                        "functions",
                    }
                )

            supports_streaming = self._extract_bool(
                row,
                "supports_streaming",
                "streaming",
                "supports_stream",
            )
            if supports_streaming is None:
                supports_streaming = "stream" in capabilities if capabilities else True

            supports_structured_output = self._extract_bool(
                row,
                "supports_structured_output",
                "supports_json_schema",
            )
            if supports_structured_output is None:
                supports_structured_output = any(
                    value in capabilities
                    for value in {
                        "response_format",
                        "json_schema",
                        "structured_outputs",
                        "structured_output",
                    }
                )

            supports_vision = self._extract_bool(row, "supports_vision", "vision")
            if supports_vision is None:
                supports_vision = any(
                    value in input_modalities for value in {"image", "vision", "multimodal"}
                )

            context_window = self._extract_int(
                row,
                "context_window",
                "context_length",
                "max_context_length",
                "max_input_tokens",
                "input_token_limit",
                "token_limit",
                "max_tokens",
                "architecture.context_length",
            )
            max_output_tokens = self._extract_int(
                row,
                "max_output_tokens",
                "max_completion_tokens",
                "max_output_token",
                "output_token_limit",
                "completion_token_limit",
            )
            tokenizer_family = self._extract_str(
                row,
                "tokenizer_family",
                "tokenizer",
                "architecture.tokenizer",
            )

            models.append(
                {
                    "id": f"{self.name}/{model_id}",
                    "name": str(row.get("name") or model_id),
                    "provider_id": self.name,
                    "provider_model_id": model_id,
                    "provider_base_url": self.api_base,
                    "provider_api_key_env": self.provider_api_key_env,
                    "context_window": context_window or self.default_context_window,
                    "max_output_tokens": max_output_tokens,
                    "tokenizer_family": tokenizer_family,
                    "supports_tools": 1 if supports_tools else 0,
                    "supports_streaming": 1 if supports_streaming else 0,
                    "supports_vision": 1 if supports_vision else 0,
                    "supports_structured_output": 1 if supports_structured_output else 0,
                    "supports_system_messages": 1,
                    "provider_rank": index,
                    "chatbot_arena_elo": None,
                    "open_llm_score": None,
                    "is_healthy": 1,
                }
            )

        return models

    async def chat_completions(self, request_body: dict[str, Any], model: str) -> ChatResult:
        self._assert_api_key()
        body = dict(request_body)
        body["model"] = model

        start = time.monotonic()
        response = await self._request_with_retries(
            "POST",
            "/chat/completions",
            json_body=body,
            timeout_seconds=self.default_request_timeout_seconds,
        )
        payload = self._parse_json(response.content)
        usage = payload.get("usage", {}) if isinstance(payload.get("usage"), Mapping) else {}
        latency_ms = int((time.monotonic() - start) * 1000)
        return ChatResult(
            payload=payload,
            latency_ms=latency_ms,
            ttfb_ms=latency_ms,
            prompt_tokens=self._coerce_int(usage.get("prompt_tokens")),
            completion_tokens=self._coerce_int(usage.get("completion_tokens")),
            total_tokens=self._coerce_int(usage.get("total_tokens")),
        )

    async def stream_chat_completions(
        self, request_body: dict[str, Any], model: str
    ) -> StreamResult:
        self._assert_api_key()
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
        self._assert_api_key()
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": max_tokens,
            "stream": False,
        }
        start = time.monotonic()
        response = await self._request_with_retries(
            "POST",
            "/chat/completions",
            json_body=body,
            timeout_seconds=max(int(timeout_seconds), 1),
        )
        payload = self._parse_json(response.content)
        usage = payload.get("usage", {}) if isinstance(payload.get("usage"), Mapping) else {}
        latency_ms = int((time.monotonic() - start) * 1000)
        return ChatResult(
            payload=payload,
            latency_ms=latency_ms,
            ttfb_ms=latency_ms,
            prompt_tokens=self._coerce_int(usage.get("prompt_tokens")),
            completion_tokens=self._coerce_int(usage.get("completion_tokens")),
            total_tokens=self._coerce_int(usage.get("total_tokens")),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _assert_api_key(self) -> None:
        if self.api_key:
            return
        raise ProviderFatalError(
            f"{self.name} api key is required",
            category="AUTH_ERROR",
            status_code=401,
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
        for _attempt in range(max(int(self.max_retries), 1)):
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
            except httpx.TimeoutException as exc:
                last_error = ProviderRetryableError(
                    "provider timeout",
                    category="PROVIDER_UNAVAILABLE",
                )
                last_error.__cause__ = exc
            except httpx.HTTPError as exc:
                last_error = ProviderRetryableError(
                    "provider transport error",
                    category="PROVIDER_UNAVAILABLE",
                )
                last_error.__cause__ = exc

        if isinstance(last_error, Exception):
            raise last_error
        raise ProviderRetryableError(
            "provider request failed",
            category="PROVIDER_UNAVAILABLE",
        )

    def _raise_for_response(self, status_code: int, raw_body: bytes) -> None:
        message, error_code = self._extract_error_details(raw_body)
        normalized_message = message or f"{self.name} error ({status_code})"
        category, retryable = self.categorize_error(status_code, error_code, normalized_message)
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

        if not isinstance(payload, Mapping):
            return None, None

        error = payload.get("error")
        error_payload = error if isinstance(error, Mapping) else payload
        message_raw = error_payload.get("message")
        code_raw = error_payload.get("code")
        message = str(message_raw)[:500] if message_raw else None
        error_code = str(code_raw) if code_raw is not None else None
        return message, error_code

    def _parse_json(self, raw_body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderRetryableError(
                "provider returned invalid json",
                category="PROVIDER_UNAVAILABLE",
            ) from exc
        return payload if isinstance(payload, dict) else {}

    def _extract_capabilities(self, row: Mapping[str, Any]) -> set[str]:
        capabilities: set[str] = set()
        for key in ("supported_parameters", "capabilities", "supported_features", "features"):
            value = self._nested_get(row, key)
            if isinstance(value, Mapping):
                for capability_key, capability_value in value.items():
                    if capability_value:
                        capabilities.add(str(capability_key).lower())
            elif isinstance(value, list | tuple | set):
                for capability in value:
                    capabilities.add(str(capability).lower())
        return capabilities

    def _extract_input_modalities(self, row: Mapping[str, Any]) -> set[str]:
        modalities: set[str] = set()
        for key in (
            "input_modalities",
            "modalities",
            "supported_modalities",
            "architecture.input_modalities",
        ):
            value = self._nested_get(row, key)
            if isinstance(value, list | tuple | set):
                for modality in value:
                    modalities.add(str(modality).lower())
        return modalities

    def _extract_int(self, row: Mapping[str, Any], *keys: str) -> int | None:
        for key in keys:
            value = self._nested_get(row, key)
            coerced = self._coerce_int(value)
            if coerced is not None:
                return coerced
        return None

    def _extract_str(self, row: Mapping[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = self._nested_get(row, key)
            if value is None:
                continue
            out = str(value).strip()
            if out:
                return out
        return None

    def _extract_bool(self, row: Mapping[str, Any], *keys: str) -> bool | None:
        for key in keys:
            value = self._nested_get(row, key)
            if isinstance(value, bool):
                return value
            if isinstance(value, int):
                return value != 0
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"1", "true", "yes", "on"}:
                    return True
                if normalized in {"0", "false", "no", "off"}:
                    return False
        return None

    def _nested_get(self, row: Mapping[str, Any], key: str) -> Any:
        if "." not in key:
            return row.get(key)
        current: Any = row
        for part in key.split("."):
            if not isinstance(current, Mapping):
                return None
            current = current.get(part)
        return current

    def _coerce_int(self, value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, float):
            return int(value) if value >= 0 else None
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                return int(text)
        return None
