from __future__ import annotations

import copy
import json
import random
import string
from typing import Any

import pytest

import src.tokens as tokens_module
from src.tokens import (
    IMAGE_TOKEN_COST,
    MESSAGE_METADATA_OVERHEAD_TOKENS,
    estimate_required_tokens,
    request_contains_vision,
)

_PROSE_VOCAB = (
    "gateway",
    "routing",
    "latency",
    "policy",
    "budget",
    "traffic",
    "model",
    "prompt",
    "quality",
    "signal",
    "ranking",
    "health",
    "fallback",
    "request",
    "response",
    "session",
    "analysis",
    "summary",
    "insight",
    "selection",
)


def _no_exact_tokenizer(
    *, tokenizer_family: str | None = None, model_hint: str | None = None
) -> None:
    del tokenizer_family, model_hint
    return None


@pytest.fixture(autouse=True)
def _force_heuristic_token_estimation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tokens_module, "_resolve_exact_tokenizer", _no_exact_tokenizer)


def _rand_word(rng: random.Random, *, min_len: int = 3, max_len: int = 10) -> str:
    size = rng.randint(min_len, max_len)
    return "".join(rng.choice(string.ascii_lowercase) for _ in range(size))


def _prose_text(rng: random.Random, *, min_words: int = 6, max_words: int = 18) -> str:
    word_count = rng.randint(min_words, max_words)
    words = [rng.choice(_PROSE_VOCAB) for _ in range(word_count)]
    sentence = " ".join(words)
    return f"{sentence.capitalize()}."


def _code_text(rng: random.Random) -> str:
    fn_name = _rand_word(rng, min_len=4, max_len=8)
    value_name = _rand_word(rng, min_len=4, max_len=8)
    increment = rng.randint(1, 9)
    return "\n".join(
        [
            f"def {fn_name}({value_name}):",
            f"    result = {value_name} + {increment}",
            "    return result",
        ]
    )


def _json_text(rng: random.Random) -> str:
    payload = {
        "id": rng.randint(1, 1000),
        "name": _rand_word(rng),
        "values": [rng.randint(0, 9) for _ in range(rng.randint(1, 5))],
        "enabled": rng.choice([True, False]),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _text_by_type(rng: random.Random, content_type: str) -> str:
    if content_type == "prose":
        return _prose_text(rng)
    if content_type == "code":
        return _code_text(rng)
    if content_type == "json":
        return _json_text(rng)
    raise ValueError(f"Unsupported content type: {content_type}")


def _append_same_type_content(rng: random.Random, text: str, content_type: str) -> str:
    if content_type == "prose":
        return f"{text} {_prose_text(rng, min_words=4, max_words=10)}"
    if content_type == "code":
        extra_fn = _rand_word(rng, min_len=4, max_len=8)
        return f"{text}\n\ndef {extra_fn}(x):\n    return x * 2"
    if content_type == "json":
        payload = json.loads(text)
        payload[f"extra_{_rand_word(rng, min_len=3, max_len=6)}"] = _rand_word(rng)
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)
    raise ValueError(f"Unsupported content type: {content_type}")


def _random_message(rng: random.Random) -> dict[str, Any]:
    role = rng.choice(["user", "assistant", "system"])
    message: dict[str, Any] = {"role": role}
    variant = rng.randint(0, 4)

    if variant == 0:
        message["content"] = _prose_text(rng)
    elif variant == 1:
        message["content"] = _code_text(rng)
    elif variant == 2:
        message["content"] = _json_text(rng)
    elif variant == 3:
        message["content"] = [
            {"type": "input_text", "text": _prose_text(rng, min_words=4, max_words=8)},
            {"type": "text", "text": _prose_text(rng, min_words=3, max_words=6)},
        ]
    else:
        message["content"] = {
            "type": "input_text",
            "text": _prose_text(rng, min_words=5, max_words=9),
        }

    if rng.random() < 0.35:
        message["name"] = _rand_word(rng)
    if rng.random() < 0.35:
        message["tool_call_id"] = f"call_{_rand_word(rng, min_len=5, max_len=10)}"
    if rng.random() < 0.35:
        message["refusal"] = _prose_text(rng, min_words=4, max_words=9)
    if rng.random() < 0.3:
        message["tool_calls"] = [
            {
                "id": f"call_{_rand_word(rng)}",
                "type": "function",
                "function": {
                    "name": _rand_word(rng),
                    "arguments": json.dumps({"q": _rand_word(rng), "limit": rng.randint(1, 5)}),
                },
            }
        ]
    if rng.random() < 0.25:
        message["function_call"] = {
            "name": _rand_word(rng),
            "arguments": json.dumps({"topic": _rand_word(rng), "count": rng.randint(1, 4)}),
        }
    if rng.random() < 0.2:
        message["audio"] = {"id": f"audio_{_rand_word(rng)}", "format": "wav"}
    return message


def _random_tools(rng: random.Random) -> Any:
    if rng.random() < 0.5:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": _rand_word(rng),
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
    ]


def _random_response_format(rng: random.Random) -> Any:
    if rng.random() < 0.5:
        return None
    return {
        "type": "json_schema",
        "json_schema": {
            "name": _rand_word(rng),
            "schema": {
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
            },
        },
    }


def test_property_estimates_are_non_negative_and_deterministic() -> None:
    rng = random.Random(7001)
    families: list[str | None] = [None, "gpt", "qwen2", "deepseek", "claude", "router"]

    for _ in range(180):
        messages = [_random_message(rng) for _ in range(rng.randint(0, 5))]
        tools = _random_tools(rng)
        response_format = _random_response_format(rng)
        family = rng.choice(families)
        safety_buffer = 0.0 if rng.random() < 0.5 else round(rng.uniform(0.01, 0.4), 3)

        first = estimate_required_tokens(
            messages,
            tools=tools,
            response_format=response_format,
            safety_buffer=safety_buffer,
            tokenizer_family=family,
            model_hint="unavailable/provider-model",
        )
        second = estimate_required_tokens(
            messages,
            tools=tools,
            response_format=response_format,
            safety_buffer=safety_buffer,
            tokenizer_family=family,
            model_hint="unavailable/provider-model",
        )

        assert first >= 1
        assert first == second


@pytest.mark.parametrize("content_type,seed", [("prose", 8011), ("code", 8012), ("json", 8013)])
def test_property_monotonic_when_appending_same_message_content(
    content_type: str,
    seed: int,
) -> None:
    rng = random.Random(seed)

    for _ in range(120):
        base_text = _text_by_type(rng, content_type)
        extended_text = _append_same_type_content(rng, base_text, content_type)
        messages_before = [{"role": "user", "content": base_text}]
        messages_after = [{"role": "user", "content": extended_text}]

        before = estimate_required_tokens(
            messages_before,
            safety_buffer=0.0,
            tokenizer_family="claude",
            model_hint="unknown/model",
        )
        after = estimate_required_tokens(
            messages_after,
            safety_buffer=0.0,
            tokenizer_family="claude",
            model_hint="unknown/model",
        )

        assert tokens_module._detect_text_content_type(base_text) == content_type
        assert tokens_module._detect_text_content_type(extended_text) == content_type
        assert after >= before


def test_property_monotonic_when_appending_messages() -> None:
    rng = random.Random(9101)

    for _ in range(140):
        base_messages = [_random_message(rng) for _ in range(rng.randint(1, 4))]
        appended_message = _random_message(rng)

        before = estimate_required_tokens(
            base_messages,
            safety_buffer=0.0,
            tokenizer_family="qwen2",
            model_hint="missing/model",
        )
        after = estimate_required_tokens(
            base_messages + [appended_message],
            safety_buffer=0.0,
            tokenizer_family="qwen2",
            model_hint="missing/model",
        )

        assert after >= before


@pytest.mark.parametrize("content_type,seed", [("prose", 9901), ("code", 9902), ("json", 9903)])
def test_property_content_types_are_stable_across_supported_profiles(
    content_type: str,
    seed: int,
) -> None:
    rng = random.Random(seed)
    families: list[str | None] = [
        None,
        "gpt",
        "qwen",
        "deepseek",
        "mistral",
        "llama",
        "cohere",
        "claude",
        "gemini",
        "grok",
        "nova",
        "router",
    ]

    for _ in range(60):
        text = _text_by_type(rng, content_type)
        assert tokens_module._detect_text_content_type(text) == content_type

        first_pass = [
            estimate_required_tokens(
                [{"role": "user", "content": text}],
                safety_buffer=0.0,
                tokenizer_family=family,
                model_hint="unresolved/model",
            )
            for family in families
        ]
        second_pass = [
            estimate_required_tokens(
                [{"role": "user", "content": text}],
                safety_buffer=0.0,
                tokenizer_family=family,
                model_hint="unresolved/model",
            )
            for family in families
        ]

        assert first_pass == second_pass
        assert all(value >= 1 for value in first_pass)


def test_property_vision_parts_have_fixed_increment_and_detection_invariants() -> None:
    rng = random.Random(10401)

    for _ in range(120):
        text_parts = [
            {"type": "input_text", "text": _prose_text(rng, min_words=3, max_words=7)}
            for _ in range(rng.randint(1, 4))
        ]
        image_count = rng.randint(1, 3)
        image_parts = [
            {
                "type": "image_url",
                "image_url": {"url": f"https://example.com/{rng.randint(1, 9999)}.png"},
            }
            for _ in range(image_count)
        ]

        content_with_images = copy.deepcopy(text_parts)
        for image_part in image_parts:
            insert_at = rng.randint(0, len(content_with_images))
            content_with_images.insert(insert_at, image_part)

        no_image_messages = [{"role": "user", "content": text_parts}]
        with_image_messages = [{"role": "user", "content": content_with_images}]

        without_vision = estimate_required_tokens(
            no_image_messages,
            safety_buffer=0.0,
            tokenizer_family="router",
            model_hint="missing/model",
        )
        with_vision = estimate_required_tokens(
            with_image_messages,
            safety_buffer=0.0,
            tokenizer_family="router",
            model_hint="missing/model",
        )

        assert request_contains_vision(no_image_messages) is False
        assert request_contains_vision(with_image_messages) is True
        assert with_vision - without_vision == image_count * IMAGE_TOKEN_COST


def test_property_message_metadata_fields_are_monotonic() -> None:
    rng = random.Random(11601)
    metadata_builders: list[tuple[str, Any]] = [
        ("name", lambda r: _rand_word(r)),
        ("tool_call_id", lambda r: f"call_{_rand_word(r)}"),
        ("refusal", lambda r: _prose_text(r, min_words=5, max_words=8)),
        (
            "function_call",
            lambda r: {"name": _rand_word(r), "arguments": json.dumps({"x": _rand_word(r)})},
        ),
        ("audio", lambda r: {"id": f"audio_{_rand_word(r)}", "format": "wav"}),
        (
            "tool_calls",
            lambda r: [
                {
                    "id": f"call_{_rand_word(r)}",
                    "type": "function",
                    "function": {
                        "name": _rand_word(r),
                        "arguments": json.dumps({"q": _rand_word(r)}),
                    },
                }
            ],
        ),
    ]

    for _ in range(120):
        message: dict[str, Any] = {
            "role": "assistant",
            "content": _prose_text(rng, min_words=4, max_words=8),
        }
        previous = estimate_required_tokens(
            [message],
            safety_buffer=0.0,
            tokenizer_family="deepseek",
            model_hint="missing/model",
        )

        order = list(metadata_builders)
        rng.shuffle(order)
        for field, builder in order:
            message[field] = builder(rng)
            current = estimate_required_tokens(
                [message],
                safety_buffer=0.0,
                tokenizer_family="deepseek",
                model_hint="missing/model",
            )
            assert current >= previous + MESSAGE_METADATA_OVERHEAD_TOKENS + 1
            previous = current
