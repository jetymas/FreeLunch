from __future__ import annotations

import json
import math
from typing import Any

IMAGE_TOKEN_COST = 256
MESSAGE_OVERHEAD_TOKENS = 6
REQUEST_OVERHEAD_TOKENS = 16
STRUCTURED_OUTPUT_OVERHEAD_TOKENS = 16
TOOLS_OVERHEAD_TOKENS = 32


def _chars_per_token(tokenizer_family: str | None) -> float:
    family = str(tokenizer_family or "").strip().lower()
    if not family:
        return 4.0
    if "qwen" in family:
        return 3.0
    if any(token in family for token in ("llama", "mistral", "sentencepiece", "spm")):
        return 3.2
    if any(token in family for token in ("gpt", "cl100k", "o200k", "tiktoken")):
        return 4.0
    return 3.6


def _text_token_estimate(text: str, tokenizer_family: str | None = None) -> int:
    if not text:
        return 0
    return max(math.ceil(len(text) / _chars_per_token(tokenizer_family)), 1)


def _json_token_estimate(value: Any, tokenizer_family: str | None = None) -> int:
    return _text_token_estimate(
        json.dumps(value, ensure_ascii=True, separators=(",", ":")),
        tokenizer_family=tokenizer_family,
    )


def _is_image_part(part: Any) -> bool:
    if not isinstance(part, dict):
        return False
    part_type = str(part.get("type", "")).lower()
    return (
        "image" in part_type
        or "image_url" in part
        or "input_image" in part
    )


def _estimate_message_content_tokens(content: Any, tokenizer_family: str | None = None) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return _text_token_estimate(content, tokenizer_family=tokenizer_family)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, str):
                total += _text_token_estimate(part, tokenizer_family=tokenizer_family)
                continue
            if _is_image_part(part):
                total += IMAGE_TOKEN_COST
                continue
            if isinstance(part, dict):
                if str(part.get("type", "")).lower() in {"text", "input_text"}:
                    total += _text_token_estimate(
                        str(part.get("text", "")),
                        tokenizer_family=tokenizer_family,
                    )
                else:
                    total += _json_token_estimate(part, tokenizer_family=tokenizer_family)
                continue
            total += _text_token_estimate(str(part), tokenizer_family=tokenizer_family)
        return total
    if isinstance(content, dict):
        if _is_image_part(content):
            return IMAGE_TOKEN_COST
        if str(content.get("type", "")).lower() in {"text", "input_text"}:
            return _text_token_estimate(
                str(content.get("text", "")),
                tokenizer_family=tokenizer_family,
            )
        return _json_token_estimate(content, tokenizer_family=tokenizer_family)
    return _text_token_estimate(str(content), tokenizer_family=tokenizer_family)


def request_contains_vision(messages: list[dict[str, Any]] | None) -> bool:
    if not messages:
        return False
    for message in messages:
        content = message.get("content")
        if isinstance(content, dict) and _is_image_part(content):
            return True
        if isinstance(content, list) and any(_is_image_part(part) for part in content):
            return True
    return False


def estimate_required_tokens(
    messages: list[dict[str, Any]] | None,
    *,
    tools: Any = None,
    response_format: Any = None,
    safety_buffer: float = 0.15,
    tokenizer_family: str | None = None,
) -> int:
    total = REQUEST_OVERHEAD_TOKENS
    for message in messages or []:
        total += MESSAGE_OVERHEAD_TOKENS
        total += _estimate_message_content_tokens(
            message.get("content"),
            tokenizer_family=tokenizer_family,
        )

    if tools:
        total += TOOLS_OVERHEAD_TOKENS + _json_token_estimate(
            tools,
            tokenizer_family=tokenizer_family,
        )
    if response_format:
        total += STRUCTURED_OUTPUT_OVERHEAD_TOKENS + _json_token_estimate(
            response_format,
            tokenizer_family=tokenizer_family,
        )

    return max(math.ceil(total * (1.0 + safety_buffer)), 1)
