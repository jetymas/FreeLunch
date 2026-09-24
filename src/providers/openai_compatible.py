from __future__ import annotations

import ipaddress
import json
import os
import time
from collections.abc import AsyncGenerator, Mapping
from typing import Any
from urllib.parse import urlsplit

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

KNOWN_PROVIDER_API_HOSTS: dict[str, frozenset[str]] = {
    "openai": frozenset({"api.openai.com"}),
    "together": frozenset({"api.together.xyz"}),
    "groq": frozenset({"api.groq.com"}),
    "deepseek": frozenset({"api.deepseek.com"}),
    "xai": frozenset({"api.x.ai"}),
    "cerebras": frozenset({"api.cerebras.ai"}),
    "perplexity": frozenset({"api.perplexity.ai"}),
    "nvidia": frozenset({"integrate.api.nvidia.com"}),
    "openrouter": frozenset({"openrouter.ai"}),
}


def validate_provider_api_base(
    provider_id: str,
    api_base: str,
    *,
    allow_custom_api_base: bool = False,
) -> str:
    """Validate a configured API base before constructing its HTTP client."""
    value = api_base.strip()
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid API base URL for provider {provider_id!r}") from exc

    if parsed.scheme.lower() != "https" or not hostname:
        raise ValueError(f"provider {provider_id!r} API base must use HTTPS and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"provider {provider_id!r} API base must not contain credentials")
    if parsed.fragment or parsed.query or "#" in value or "?" in value:
        raise ValueError(f"provider {provider_id!r} API base must not contain a query or fragment")
    if port not in (None, 443):
        raise ValueError(f"provider {provider_id!r} API base must use port 443")
    if "\\" in value or any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value):
        raise ValueError(f"provider {provider_id!r} API base contains invalid URL characters")

    normalized_host = hostname.lower().rstrip(".")
    try:
        ipaddress.ip_address(normalized_host)
    except ValueError:
        pass
    else:
        raise ValueError(f"provider {provider_id!r} API base must use a DNS hostname")
    if normalized_host == "localhost" or normalized_host.endswith(
        (".localhost", ".local", ".internal")
    ):
        raise ValueError(f"provider {provider_id!r} API base must not use a local hostname")

    known_hosts = KNOWN_PROVIDER_API_HOSTS.get(provider_id.lower(), frozenset())
    if normalized_host not in known_hosts and not allow_custom_api_base:
        raise ValueError(
            f"provider {provider_id!r} API base host {normalized_host!r} is not approved; "
            "set allow_custom_api_base: true to opt in"
        )
    return value


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
    provider_id: str,
    default_api_base: str,
    default_api_key_env: str,
) -> tuple[str, str, str]:
    api_base = str(provider_config.get("api_base", default_api_base)).strip() or default_api_base
    api_base = validate_provider_api_base(
        provider_id,
        api_base,
        allow_custom_api_base=provider_config.get("allow_custom_api_base") is True,
    )
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
    max_response_bytes = 16 * 1024 * 1024
    max_sse_event_bytes = 1024 * 1024

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
        self._ensure_response_size(response.content)
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
            timeout=httpx.Timeout(timeout=None, connect=15.0, read=None, write=30.0, pool=15.0),
            follow_redirects=False,
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
                error_chunks: list[bytes] = []
                error_size = 0
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    error_size += len(chunk)
                    if error_size > self.max_response_bytes:
                        await response.aclose()
                        await client.aclose()
                        raise ProviderRetryableError(
                            "provider response exceeded size limit",
                            category="PROVIDER_UNAVAILABLE",
                        )
                    error_chunks.append(chunk)
                raw_body = b"".join(error_chunks)
                await response.aclose()
                await client.aclose()
                self._raise_for_response(response.status_code, raw_body)
        except BaseException:
            await client.aclose()
            raise

        async def event_stream() -> AsyncGenerator[bytes, None]:
            pending_lines: list[str] = []
            pending_bytes = 0
            line_buffer = bytearray()
            try:
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    line_buffer.extend(chunk)
                    while b"\n" in line_buffer:
                        line, _, remainder = line_buffer.partition(b"\n")
                        line_buffer = bytearray(remainder)
                        if line.endswith(b"\r"):
                            line = line[:-1]
                        if len(line) > self.max_sse_event_bytes:
                            raise ProviderRetryableError(
                                "provider SSE event exceeded size limit",
                                category="PROVIDER_UNAVAILABLE",
                            )
                        if not line:
                            if pending_lines:
                                yield ("\n".join(pending_lines) + "\n\n").encode("utf-8")
                                pending_lines.clear()
                                pending_bytes = 0
                            continue
                        decoded = line.decode("utf-8", errors="replace")
                        if decoded.startswith(":"):
                            continue
                        pending_bytes += len(line) + 1
                        if pending_bytes > self.max_sse_event_bytes:
                            raise ProviderRetryableError(
                                "provider SSE event exceeded size limit",
                                category="PROVIDER_UNAVAILABLE",
                            )
                        pending_lines.append(decoded)
                    if len(line_buffer) > self.max_sse_event_bytes:
                        raise ProviderRetryableError(
                            "provider SSE event exceeded size limit",
                            category="PROVIDER_UNAVAILABLE",
                        )
                if line_buffer:
                    final_line = bytes(line_buffer).removesuffix(b"\r")
                    pending_bytes += len(final_line) + 1
                    if pending_bytes > self.max_sse_event_bytes:
                        raise ProviderRetryableError(
                            "provider SSE event exceeded size limit",
                            category="PROVIDER_UNAVAILABLE",
                        )
                    pending_lines.append(final_line.decode("utf-8", errors="replace"))
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

    def _ensure_response_size(self, content: bytes) -> None:
        if len(content) > self.max_response_bytes:
            raise ProviderRetryableError(
                "provider response exceeded size limit", category="PROVIDER_UNAVAILABLE"
            )

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
                async with (
                    httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client,
                    client.stream(
                        method,
                        f"{self.api_base}{path}",
                        headers=self._headers(),
                        json=json_body,
                    ) as upstream,
                ):
                    content_length = upstream.headers.get("content-length")
                    if (
                        content_length
                        and content_length.isdigit()
                        and int(content_length) > self.max_response_bytes
                    ):
                        raise ProviderRetryableError(
                            "provider response exceeded size limit",
                            category="PROVIDER_UNAVAILABLE",
                        )
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in upstream.aiter_bytes():
                        size += len(chunk)
                        if size > self.max_response_bytes:
                            raise ProviderRetryableError(
                                "provider response exceeded size limit",
                                category="PROVIDER_UNAVAILABLE",
                            )
                        chunks.append(chunk)
                    response = httpx.Response(
                        upstream.status_code,
                        headers=upstream.headers,
                        content=b"".join(chunks),
                        request=upstream.request,
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
