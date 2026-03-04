from __future__ import annotations

import concurrent.futures
import time

import tiktoken

import src.tokens as tokens_module
from src.tokens import (
    MESSAGE_OVERHEAD_TOKENS,
    REQUEST_OVERHEAD_TOKENS,
    _candidate_hf_repo_ids,
    _detect_text_content_type,
    estimate_required_tokens,
    schedule_tokenizer_preload,
)


def _wait_for_exact_estimate(
    *,
    tokenizer_family: str,
    model_hint: str,
    content: str,
    expected: int,
    timeout_seconds: float = 1.0,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    last = 0
    while time.monotonic() < deadline:
        last = estimate_required_tokens(
            [{"role": "user", "content": content}],
            safety_buffer=0.0,
            tokenizer_family=tokenizer_family,
            model_hint=model_hint,
        )
        if last == expected:
            return last
        time.sleep(0.01)
    return last


def test_estimate_required_tokens_uses_model_hint_tiktoken_encoding():
    messages = [{"role": "user", "content": "Hello world, token counter."}]
    encoding = tiktoken.encoding_for_model("gpt-4o-mini")
    expected = (
        REQUEST_OVERHEAD_TOKENS
        + MESSAGE_OVERHEAD_TOKENS
        + len(encoding.encode(messages[0]["content"], disallowed_special=()))
    )

    actual = estimate_required_tokens(
        messages,
        safety_buffer=0.0,
        tokenizer_family="gpt",
        model_hint="gpt-4o-mini",
    )

    assert actual == expected


def test_estimate_required_tokens_uses_prefixed_openai_model_hint_tiktoken_encoding():
    messages = [{"role": "user", "content": "Hello world, token counter."}]
    encoding = tiktoken.encoding_for_model("gpt-4o-mini")
    expected = (
        REQUEST_OVERHEAD_TOKENS
        + MESSAGE_OVERHEAD_TOKENS
        + len(encoding.encode(messages[0]["content"], disallowed_special=()))
    )

    actual = estimate_required_tokens(
        messages,
        safety_buffer=0.0,
        tokenizer_family="GPT",
        model_hint="openai/gpt-4o-mini",
    )

    assert actual == expected


def test_estimate_required_tokens_uses_openai_family_fallback_encoding_when_model_is_new():
    messages = [{"role": "user", "content": "Hello world, token counter."}]
    encoding = tiktoken.get_encoding("o200k_base")
    expected = (
        REQUEST_OVERHEAD_TOKENS
        + MESSAGE_OVERHEAD_TOKENS
        + len(encoding.encode(messages[0]["content"], disallowed_special=()))
    )

    actual = estimate_required_tokens(
        messages,
        safety_buffer=0.0,
        tokenizer_family="GPT",
        model_hint="openai/gpt-5.3-chat",
    )

    assert actual == expected


def test_estimate_required_tokens_uses_hf_tokenizer_for_non_oai_model_hint(monkeypatch):
    class _FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            assert add_special_tokens is False
            return list(range(max(len(text) // 2, 1)))

    load_calls: list[str] = []

    class _FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=True, trust_remote_code=False):
            load_calls.append(repo_id)
            if repo_id == "Qwen/Qwen2.5-7B-Instruct":
                return _FakeTokenizer()
            raise OSError("repo not found")

    monkeypatch.setattr(tokens_module, "AutoTokenizer", _FakeAutoTokenizer)
    tokens_module._clear_hf_tokenizer_cache()

    scheduled = schedule_tokenizer_preload("qwen/qwen2.5-7b-instruct:free")
    actual = _wait_for_exact_estimate(
        tokenizer_family="qwen2",
        model_hint="qwen/qwen2.5-7b-instruct:free",
        content="abcdefghij",
        expected=REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 5,
    )

    assert scheduled is True
    assert actual == REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 5
    assert "Qwen/Qwen2.5-7B-Instruct" in load_calls


def test_estimate_required_tokens_uses_hf_org_alias_for_non_oai_model_hint(monkeypatch):
    class _FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            assert add_special_tokens is False
            return list(range(max(len(text) // 3, 1)))

    load_calls: list[str] = []

    class _FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=True, trust_remote_code=False):
            load_calls.append(repo_id)
            if repo_id == "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B":
                return _FakeTokenizer()
            raise OSError("repo not found")

    monkeypatch.setattr(tokens_module, "AutoTokenizer", _FakeAutoTokenizer)
    tokens_module._clear_hf_tokenizer_cache()

    schedule_tokenizer_preload("deepseek/deepseek-r1-distill-qwen-32b:free")
    actual = _wait_for_exact_estimate(
        tokenizer_family="deepseek_r1",
        model_hint="deepseek/deepseek-r1-distill-qwen-32b:free",
        content="abcdefghijkl",
        expected=REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 4,
    )

    assert actual == REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 4
    assert "deepseek/deepseek-r1-distill-qwen-32b" in load_calls
    assert "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B" in load_calls


def test_estimate_required_tokens_falls_back_when_hf_tokenizer_unavailable(monkeypatch):
    class _FailingAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=True, trust_remote_code=False):
            raise OSError("repo not found")

    monkeypatch.setattr(tokens_module, "AutoTokenizer", _FailingAutoTokenizer)
    tokens_module._clear_hf_tokenizer_cache()

    messages = [{"role": "user", "content": "x" * 40}]
    actual = estimate_required_tokens(
        messages,
        safety_buffer=0.0,
        tokenizer_family="llama3",
        model_hint="meta-llama/llama-3.3-70b-instruct:free",
    )

    assert actual > REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS


def test_detect_text_content_type_distinguishes_prose_code_and_json():
    assert _detect_text_content_type("Write a short summary about the routing system.") == "prose"
    assert _detect_text_content_type('{"type":"object","properties":{"name":{"type":"string"}}}') == "json"
    assert (
        _detect_text_content_type(
            "def greet(name):\n    message = f'hello {name}'\n    return message\n"
        )
        == "code"
    )


def test_estimate_required_tokens_uses_family_and_content_type_calibration_for_prose():
    content = " ".join(
        ["The gateway should estimate token usage conservatively before routing."] * 20
    )

    actual = estimate_required_tokens(
        [{"role": "user", "content": content}],
        safety_buffer=0.0,
        tokenizer_family="Claude",
        model_hint=None,
    )

    assert actual == 295


def test_estimate_required_tokens_uses_family_and_content_type_calibration_for_code():
    content = "\n".join(
        [f"def func_{i}(value): return value * {i}" for i in range(80)]
    )

    actual = estimate_required_tokens(
        [{"role": "user", "content": content}],
        safety_buffer=0.0,
        tokenizer_family="Claude",
        model_hint=None,
    )

    assert actual == 1206


def test_estimate_required_tokens_uses_family_and_content_type_calibration_for_json():
    content = {
        "schema": {
            "type": "object",
            "properties": {
                f"field_{i}": {
                    "type": "string",
                    "description": "sample description",
                }
                for i in range(60)
            },
            "required": [f"field_{i}" for i in range(20)],
        },
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "lookup",
                    "arguments": '{"query":"widgets","limit":10}',
                },
            }
        ],
    }

    actual = estimate_required_tokens(
        [{"role": "user", "content": content}],
        safety_buffer=0.0,
        tokenizer_family="Claude",
        model_hint=None,
    )

    assert actual == 1385


def test_candidate_hf_repo_ids_include_deepseek_org_alias_and_canonical_case():
    candidates = _candidate_hf_repo_ids("deepseek/deepseek-r1-distill-qwen-32b:free")

    assert "deepseek/deepseek-r1-distill-qwen-32b" in candidates
    assert "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B" in candidates


def test_candidate_hf_repo_ids_normalize_alphanumeric_repo_tokens():
    candidates = _candidate_hf_repo_ids("qwen/qwen3-235b-a22b-thinking-2507:free")

    assert "Qwen/Qwen3-235B-A22B-Thinking-2507" in candidates


def test_candidate_hf_repo_ids_include_stepfun_alias_and_quantization_suffix():
    candidates = _candidate_hf_repo_ids("stepfun/step-3.5-flash:free")

    assert "stepfun-ai/Step-3.5-Flash-FP8" in candidates


def test_candidate_hf_repo_ids_include_cohere_command_alias():
    candidates = _candidate_hf_repo_ids("cohere/command-r-plus-08-2024")

    assert "CohereLabs/c4ai-command-r-plus-08-2024" in candidates


def test_candidate_hf_repo_ids_include_mistral_release_suffix():
    candidates = _candidate_hf_repo_ids("mistralai/mistral-small-3.1-24b-instruct:free")

    assert "mistralai/Mistral-Small-3.1-24B-Instruct-2503" in candidates


def test_candidate_hf_repo_ids_include_nvidia_prefix_and_bf16_suffix():
    candidates = _candidate_hf_repo_ids("nvidia/nemotron-nano-12b-v2-vl:free")

    assert "nvidia/NVIDIA-Nemotron-Nano-12B-V2-VL-BF16" in candidates


def test_candidate_hf_repo_ids_include_zai_org_alias_and_fp8_suffix():
    candidates = _candidate_hf_repo_ids("z-ai/glm-4.5-air:free")

    assert "zai-org/GLM-4.5-Air-FP8" in candidates


def test_estimate_required_tokens_uses_deepseek_alias_repo_when_available(monkeypatch):
    class _FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            assert add_special_tokens is False
            return list(range(max(len(text) // 3, 1)))

    load_calls: list[str] = []

    class _FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=True, trust_remote_code=False):
            load_calls.append(repo_id)
            if repo_id == "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B":
                return _FakeTokenizer()
            raise OSError("repo not found")

    monkeypatch.setattr(tokens_module, "AutoTokenizer", _FakeAutoTokenizer)
    tokens_module._clear_hf_tokenizer_cache()

    schedule_tokenizer_preload("deepseek/deepseek-r1-distill-qwen-32b:free")
    actual = _wait_for_exact_estimate(
        tokenizer_family="deepseek",
        model_hint="deepseek/deepseek-r1-distill-qwen-32b:free",
        content="abcdefghi",
        expected=REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 3,
    )

    assert actual == REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 3
    assert "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B" in load_calls


def test_estimate_required_tokens_uses_hf_org_alias_for_deepseek_family(monkeypatch):
    class _FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            assert add_special_tokens is False
            return list(range(7))

    load_calls: list[str] = []

    class _FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=True, trust_remote_code=False):
            load_calls.append(repo_id)
            if repo_id == "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B":
                return _FakeTokenizer()
            raise OSError("repo not found")

    monkeypatch.setattr(tokens_module, "AutoTokenizer", _FakeAutoTokenizer)
    tokens_module._clear_hf_tokenizer_cache()

    schedule_tokenizer_preload("deepseek/deepseek-r1-distill-qwen-32b:free")
    actual = _wait_for_exact_estimate(
        tokenizer_family="deepseek",
        model_hint="deepseek/deepseek-r1-distill-qwen-32b:free",
        content="deepseek exact count",
        expected=REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 7,
    )

    assert actual == REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 7
    assert "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B" in load_calls


def test_estimate_required_tokens_uses_meta_llama_repo_variant(monkeypatch):
    class _FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            assert add_special_tokens is False
            return list(range(9))

    load_calls: list[str] = []

    class _FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=True, trust_remote_code=False):
            load_calls.append(repo_id)
            if repo_id == "meta-llama/Meta-Llama-3-70B-Instruct":
                return _FakeTokenizer()
            raise OSError("repo not found")

    monkeypatch.setattr(tokens_module, "AutoTokenizer", _FakeAutoTokenizer)
    tokens_module._clear_hf_tokenizer_cache()

    schedule_tokenizer_preload("meta-llama/llama-3-70b-instruct:free")
    actual = _wait_for_exact_estimate(
        tokenizer_family="llama3",
        model_hint="meta-llama/llama-3-70b-instruct:free",
        content="meta llama exact count",
        expected=REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 9,
    )

    assert actual == REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 9
    assert "meta-llama/Meta-Llama-3-70B-Instruct" in load_calls


def test_estimate_required_tokens_uses_stepfun_alias_repo_when_available(monkeypatch):
    class _FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            assert add_special_tokens is False
            return list(range(6))

    load_calls: list[str] = []

    class _FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=True, trust_remote_code=False):
            load_calls.append(repo_id)
            if repo_id == "stepfun-ai/Step-3.5-Flash-FP8":
                return _FakeTokenizer()
            raise OSError("repo not found")

    monkeypatch.setattr(tokens_module, "AutoTokenizer", _FakeAutoTokenizer)
    tokens_module._clear_hf_tokenizer_cache()

    schedule_tokenizer_preload("stepfun/step-3.5-flash:free")
    actual = _wait_for_exact_estimate(
        tokenizer_family="other",
        model_hint="stepfun/step-3.5-flash:free",
        content="stepfun exact count",
        expected=REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 6,
    )

    assert actual == REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 6
    assert "stepfun-ai/Step-3.5-Flash-FP8" in load_calls


def test_estimate_required_tokens_uses_nvidia_repo_variant_when_available(monkeypatch):
    class _FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            assert add_special_tokens is False
            return list(range(8))

    load_calls: list[str] = []

    class _FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=True, trust_remote_code=False):
            load_calls.append(repo_id)
            if repo_id == "nvidia/NVIDIA-Nemotron-Nano-12B-V2-VL-BF16":
                return _FakeTokenizer()
            raise OSError("repo not found")

    monkeypatch.setattr(tokens_module, "AutoTokenizer", _FakeAutoTokenizer)
    tokens_module._clear_hf_tokenizer_cache()

    schedule_tokenizer_preload("nvidia/nemotron-nano-12b-v2-vl:free")
    actual = _wait_for_exact_estimate(
        tokenizer_family="other",
        model_hint="nvidia/nemotron-nano-12b-v2-vl:free",
        content="nvidia exact count",
        expected=REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 8,
    )

    assert actual == REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 8
    assert "nvidia/NVIDIA-Nemotron-Nano-12B-V2-VL-BF16" in load_calls


def test_estimate_required_tokens_uses_cohere_alias_repo_when_available(monkeypatch):
    class _FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            assert add_special_tokens is False
            return list(range(11))

    load_calls: list[str] = []

    class _FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=True, trust_remote_code=False):
            load_calls.append(repo_id)
            if repo_id == "CohereLabs/c4ai-command-r-plus-08-2024":
                return _FakeTokenizer()
            raise OSError("repo not found")

    monkeypatch.setattr(tokens_module, "AutoTokenizer", _FakeAutoTokenizer)
    tokens_module._clear_hf_tokenizer_cache()

    schedule_tokenizer_preload("cohere/command-r-plus-08-2024")
    actual = _wait_for_exact_estimate(
        tokenizer_family="Cohere",
        model_hint="cohere/command-r-plus-08-2024",
        content="cohere exact count",
        expected=REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 11,
    )

    assert actual == REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 11
    assert "CohereLabs/c4ai-command-r-plus-08-2024" in load_calls


def test_estimate_required_tokens_uses_explicit_tiktoken_family_for_json_fields():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "arguments": '{"query":"widgets-and-gadgets"}',
                    },
                }
            ],
        }
    ]
    encoding = tiktoken.get_encoding("cl100k_base")
    encoded_tool_json = len(
        encoding.encode(
            '[{"id":"call_1","type":"function","function":{"name":"lookup","arguments":"{\\"query\\":\\"widgets-and-gadgets\\"}"}}]',
            disallowed_special=(),
        )
    )
    expected = REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 2 + encoded_tool_json

    actual = estimate_required_tokens(
        messages,
        safety_buffer=0.0,
        tokenizer_family="cl100k_base",
    )

    assert actual == expected


def test_estimate_required_tokens_retries_after_transient_hf_tokenizer_failure(monkeypatch):
    class _FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            assert add_special_tokens is False
            return list(range(5))

    calls = {"count": 0}

    class _FlakyAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=True, trust_remote_code=False):
            if repo_id != "Qwen/Qwen2.5-7B-Instruct":
                raise OSError("repo not found")
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("temporary network error")
            return _FakeTokenizer()

    monkeypatch.setattr(tokens_module, "AutoTokenizer", _FlakyAutoTokenizer)
    tokens_module._clear_hf_tokenizer_cache()

    first = estimate_required_tokens(
        [{"role": "user", "content": "abcdefghij"}],
        safety_buffer=0.0,
        tokenizer_family="qwen2",
        model_hint="qwen/qwen2.5-7b-instruct:free",
    )
    second = estimate_required_tokens(
        [{"role": "user", "content": "abcdefghij"}],
        safety_buffer=0.0,
        tokenizer_family="qwen2",
        model_hint="qwen/qwen2.5-7b-instruct:free",
    )
    third = _wait_for_exact_estimate(
        tokenizer_family="qwen2",
        model_hint="qwen/qwen2.5-7b-instruct:free",
        content="abcdefghij",
        expected=REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 5,
    )

    assert first > REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS
    assert second > REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS
    assert third == REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 5
    assert calls["count"] == 2


def test_estimate_required_tokens_does_not_block_on_first_hf_preload(monkeypatch):
    class _SlowTokenizer:
        def encode(self, text, add_special_tokens=False):
            assert add_special_tokens is False
            return list(range(5))

    class _SlowAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=True, trust_remote_code=False):
            time.sleep(0.2)
            if repo_id == "Qwen/Qwen2.5-7B-Instruct":
                return _SlowTokenizer()
            raise OSError("repo not found")

    monkeypatch.setattr(tokens_module, "AutoTokenizer", _SlowAutoTokenizer)
    tokens_module._clear_hf_tokenizer_cache()

    started = time.monotonic()
    first = estimate_required_tokens(
        [{"role": "user", "content": "abcdefghij"}],
        safety_buffer=0.0,
        tokenizer_family="qwen2",
        model_hint="qwen/qwen2.5-7b-instruct:free",
    )
    elapsed = time.monotonic() - started
    second = _wait_for_exact_estimate(
        tokenizer_family="qwen2",
        model_hint="qwen/qwen2.5-7b-instruct:free",
        content="abcdefghij",
        expected=REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 5,
        timeout_seconds=1.5,
    )

    assert first > REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS
    assert elapsed < 0.15
    assert second == REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS + 5


def test_describe_exception_includes_type_when_message_is_empty():
    assert tokens_module._describe_exception(RuntimeError()) == "RuntimeError"


def test_tokenizer_future_done_logs_cancelled_preload_without_warning(monkeypatch):
    recorded: list[tuple[str, dict[str, object]]] = []

    def fake_runtime_log(_logger, event, **kwargs):
        recorded.append((event, kwargs))

    monkeypatch.setattr(tokens_module, "runtime_log", fake_runtime_log)
    tokens_module._clear_hf_tokenizer_cache()

    future: concurrent.futures.Future[object | None] = concurrent.futures.Future()
    future.cancel()
    tokens_module._HF_TOKENIZER_FUTURES["cancelled-model"] = future

    tokens_module._tokenizer_future_done("cancelled-model", future)

    assert "cancelled-model" not in tokens_module._HF_TOKENIZER_FUTURES
    assert recorded == [
        (
            "tokenizer.hf.preload_cancelled",
            {
                "verbosity": "debug",
                "message": "Background tokenizer preload cancelled",
                "model_hint": "cancelled-model",
            },
        )
    ]


def test_tokenizer_future_done_logs_exception_type_when_preload_fails(monkeypatch):
    recorded: list[tuple[str, dict[str, object]]] = []

    def fake_runtime_log(_logger, event, **kwargs):
        recorded.append((event, kwargs))

    monkeypatch.setattr(tokens_module, "runtime_log", fake_runtime_log)
    tokens_module._clear_hf_tokenizer_cache()

    future: concurrent.futures.Future[object | None] = concurrent.futures.Future()
    future.set_exception(RuntimeError())
    tokens_module._HF_TOKENIZER_FUTURES["failed-model"] = future

    tokens_module._tokenizer_future_done("failed-model", future)

    assert "failed-model" not in tokens_module._HF_TOKENIZER_FUTURES
    assert recorded == [
        (
            "tokenizer.hf.preload_failed",
            {
                "verbosity": "verbose",
                "level": 30,
                "message": "Background tokenizer preload failed",
                "model_hint": "failed-model",
                "error": "RuntimeError",
            },
        )
    ]
