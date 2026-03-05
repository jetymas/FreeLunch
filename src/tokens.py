from __future__ import annotations

import concurrent.futures
import json
import math
import re
import threading
from functools import lru_cache
from typing import Any

from src.runtime_logging import get_logger, runtime_log

try:
    import tiktoken as _tiktoken
except ImportError:  # pragma: no cover - exercised by environments without the dependency installed
    tiktoken: Any = None
else:
    tiktoken = _tiktoken

try:
    from transformers import AutoTokenizer as _AutoTokenizer
except ImportError:  # pragma: no cover - exercised by environments without the dependency installed
    AutoTokenizer: Any = None
else:
    AutoTokenizer = _AutoTokenizer

IMAGE_TOKEN_COST = 256
MESSAGE_OVERHEAD_TOKENS = 6
REQUEST_OVERHEAD_TOKENS = 16
STRUCTURED_OUTPUT_OVERHEAD_TOKENS = 16
TOOLS_OVERHEAD_TOKENS = 32
MESSAGE_METADATA_OVERHEAD_TOKENS = 2

_TEXT_PIECE_RE = re.compile(r"\s+|\w+|[^\w\s]", re.UNICODE)
_JSON_MESSAGE_FIELDS = ("tool_calls", "function_call", "audio")
_TEXT_MESSAGE_FIELDS = ("name", "tool_call_id", "refusal")
_MODEL_HINT_SUFFIX_RE = re.compile(r":[A-Za-z0-9._-]+$")
_JSON_LIKE_RE = re.compile(r"^\s*[\[{].*[\]}]\s*$", re.DOTALL)
_KNOWN_REPO_TOKEN_CASE = {
    "llama": "Llama",
    "meta": "Meta",
    "mistral": "Mistral",
    "mixtral": "Mixtral",
    "qwen": "Qwen",
    "gemma": "Gemma",
    "glm": "GLM",
    "deepseek": "DeepSeek",
    "instruct": "Instruct",
    "chat": "Chat",
    "coder": "Coder",
    "math": "Math",
    "base": "Base",
    "small": "Small",
    "medium": "Medium",
    "large": "Large",
    "vision": "Vision",
    "fp8": "FP8",
    "vl": "VL",
}
_KNOWN_ORG_CASE = {
    "qwen": "Qwen",
    "deepseek": "deepseek-ai",
    "stepfun": "stepfun-ai",
    "z-ai": "zai-org",
}
_KNOWN_ORG_ALIASES = {
    "cohere": ("CohereLabs", "CohereForAI"),
    "coherelabs": ("cohere", "CohereForAI"),
    "cohereforai": ("cohere", "CohereLabs"),
    "deepseek-ai": ("deepseek",),
    "deepseek": ("deepseek-ai",),
    "meta": ("meta-llama",),
    "mistral": ("mistralai",),
    "stepfun-ai": ("stepfun",),
    "stepfun": ("stepfun-ai",),
    "zai-org": ("z-ai",),
    "z-ai": ("zai-org",),
}
_KNOWN_TIKTOKEN_ENCODINGS = {
    "o200k_base",
    "o200k_harmony",
    "cl100k_base",
    "p50k_base",
    "p50k_edit",
    "r50k_base",
    "gpt2",
}
_TIKTOKEN_MODEL_PREFIX_ENCODINGS = (
    ("gpt-5", "o200k_base"),
    ("gpt-4o", "o200k_base"),
    ("gpt-audio", "o200k_base"),
    ("chatgpt-4o", "o200k_base"),
    ("o1", "o200k_base"),
    ("o3", "o200k_base"),
    ("o4", "o200k_base"),
    ("gpt-4", "cl100k_base"),
    ("gpt-3.5", "cl100k_base"),
)
_HF_TOKENIZER_CACHE: dict[str, Any] = {}
_HF_TOKENIZER_FUTURES: dict[str, concurrent.futures.Future[Any | None]] = {}
_HF_TOKENIZER_CACHE_LOCK = threading.RLock()
_HF_TOKENIZER_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None
_HEURISTIC_DEFAULT_PROFILE = {
    "prose_bytes_per_token": 5.2,
    "code_bytes_per_token": 2.55,
    "json_bytes_per_token": 3.1,
}
_HEURISTIC_PROFILES = {
    "gpt": {
        "prose_bytes_per_token": 5.8,
        "code_bytes_per_token": 2.7,
        "json_bytes_per_token": 3.35,
    },
    "qwen": {
        "prose_bytes_per_token": 5.35,
        "code_bytes_per_token": 2.45,
        "json_bytes_per_token": 3.05,
    },
    "deepseek": {
        "prose_bytes_per_token": 5.4,
        "code_bytes_per_token": 2.55,
        "json_bytes_per_token": 3.1,
    },
    "mistral": {
        "prose_bytes_per_token": 5.3,
        "code_bytes_per_token": 2.55,
        "json_bytes_per_token": 3.1,
    },
    "llama": {
        "prose_bytes_per_token": 5.25,
        "code_bytes_per_token": 2.5,
        "json_bytes_per_token": 3.05,
    },
    "cohere": {
        "prose_bytes_per_token": 5.1,
        "code_bytes_per_token": 2.45,
        "json_bytes_per_token": 3.0,
    },
    "claude": dict(_HEURISTIC_DEFAULT_PROFILE),
    "gemini": {
        "prose_bytes_per_token": 5.15,
        "code_bytes_per_token": 2.5,
        "json_bytes_per_token": 3.05,
    },
    "grok": {
        "prose_bytes_per_token": 5.15,
        "code_bytes_per_token": 2.5,
        "json_bytes_per_token": 3.05,
    },
    "nova": {
        "prose_bytes_per_token": 5.15,
        "code_bytes_per_token": 2.5,
        "json_bytes_per_token": 3.05,
    },
    "router": dict(_HEURISTIC_DEFAULT_PROFILE),
}

logger = get_logger(__name__)


def _describe_exception(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"[:500]
    return type(exc).__name__[:500]


def _ensure_hf_tokenizer_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _HF_TOKENIZER_EXECUTOR
    with _HF_TOKENIZER_CACHE_LOCK:
        if _HF_TOKENIZER_EXECUTOR is None:
            _HF_TOKENIZER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="freelunch-hf-tokenizer",
            )
        return _HF_TOKENIZER_EXECUTOR


@lru_cache(maxsize=64)
def _get_tiktoken_encoding(name: str) -> Any | None:
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding(name)
    except Exception:
        return None


@lru_cache(maxsize=128)
def _encoding_for_model(model_hint: str) -> Any | None:
    if tiktoken is None:
        return None
    try:
        return tiktoken.encoding_for_model(model_hint)
    except Exception:
        return None


def _encoding_name_from_tokenizer_family(tokenizer_family: str | None) -> str | None:
    family = str(tokenizer_family or "").strip().lower()
    if not family:
        return None
    if family in _KNOWN_TIKTOKEN_ENCODINGS:
        return family
    if "o200k" in family:
        return "o200k_base"
    if "cl100k" in family:
        return "cl100k_base"
    if "p50k_edit" in family:
        return "p50k_edit"
    if "p50k" in family or "codex" in family:
        return "p50k_base"
    if "r50k" in family or family == "gpt2":
        return "r50k_base"
    return None


def _resolve_tiktoken_encoding(
    *,
    tokenizer_family: str | None = None,
    model_hint: str | None = None,
) -> Any | None:
    if model_hint:
        for candidate_model in _candidate_tiktoken_model_names(str(model_hint)):
            model_encoding = _encoding_for_model(candidate_model)
            if model_encoding is not None:
                return model_encoding
        for candidate_model in _candidate_tiktoken_model_names(str(model_hint)):
            fallback_encoding_name = _fallback_tiktoken_encoding_name(candidate_model)
            if fallback_encoding_name is None:
                continue
            return _get_tiktoken_encoding(fallback_encoding_name)

    encoding_name = _encoding_name_from_tokenizer_family(tokenizer_family)
    if encoding_name is not None:
        return _get_tiktoken_encoding(encoding_name)
    return None


def _candidate_tiktoken_model_names(model_hint: str) -> tuple[str, ...]:
    hint = str(model_hint or "").strip()
    if not hint:
        return ()
    sanitized = _MODEL_HINT_SUFFIX_RE.sub("", hint)
    candidates: list[str] = []
    for candidate in (
        sanitized,
        sanitized.split("/", 1)[1] if "/" in sanitized else "",
    ):
        normalized = candidate.strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return tuple(candidates)


def _fallback_tiktoken_encoding_name(model_name: str) -> str | None:
    normalized = str(model_name or "").strip().lower()
    if not normalized:
        return None
    for prefix, encoding_name in _TIKTOKEN_MODEL_PREFIX_ENCODINGS:
        if normalized.startswith(prefix):
            return encoding_name
    return None


def _normalize_repo_token(token: str) -> str:
    if not token:
        return token
    token_lower = token.lower()
    if token_lower in _KNOWN_REPO_TOKEN_CASE:
        return _KNOWN_REPO_TOKEN_CASE[token_lower]
    if re.fullmatch(r"\d+[a-z]+", token_lower):
        prefix_length = len(token_lower.rstrip("abcdefghijklmnopqrstuvwxyz"))
        return token_lower[:prefix_length] + token_lower[prefix_length:].upper()
    if re.fullmatch(r"[a-z]\d+[a-z]?", token_lower):
        prefix = token_lower[0].upper()
        suffix = token_lower[1:]
        if suffix and suffix[-1].isalpha():
            return prefix + suffix[:-1] + suffix[-1].upper()
        return prefix + suffix
    if re.fullmatch(r"v\d.*", token_lower):
        return token_lower[0].upper() + token_lower[1:]
    if token[0].isalpha():
        return token[0].upper() + token[1:]
    return token


def _canonicalize_repo_id(repo_id: str) -> str:
    if "/" not in repo_id:
        return repo_id
    org, repo = repo_id.split("/", 1)
    normalized_org = _KNOWN_ORG_CASE.get(org.lower(), org)
    normalized_repo = "-".join(_normalize_repo_token(token) for token in repo.split("-"))
    return f"{normalized_org}/{normalized_repo}"


def _candidate_org_names(org: str) -> tuple[str, ...]:
    org_lower = org.lower()
    candidates: list[str] = []
    for candidate in (
        org,
        _KNOWN_ORG_CASE.get(org_lower, org),
        *_KNOWN_ORG_ALIASES.get(org_lower, ()),
    ):
        normalized = str(candidate).strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return tuple(candidates)


def _candidate_repo_names(org: str, repo: str) -> tuple[str, ...]:
    canonical_repo = "-".join(_normalize_repo_token(token) for token in repo.split("-"))
    candidates: list[str] = []
    for candidate in (repo, canonical_repo):
        normalized = candidate.strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    if org.lower() == "meta-llama" and canonical_repo.startswith("Llama-"):
        meta_prefixed = f"Meta-{canonical_repo}"
        if meta_prefixed not in candidates:
            candidates.append(meta_prefixed)

    if (
        org.lower() == "stepfun-ai"
        and canonical_repo.startswith("Step-")
        and not canonical_repo.endswith(("-FP8", "-BF16"))
    ):
        for suffix in ("-FP8", "-BF16"):
            suffixed = f"{canonical_repo}{suffix}"
            if suffixed not in candidates:
                candidates.append(suffixed)

    if org.lower() == "mistralai" and canonical_repo == "Mistral-Small-3.1-24B-Instruct":
        dated = f"{canonical_repo}-2503"
        if dated not in candidates:
            candidates.append(dated)

    if org.lower() == "nvidia" and canonical_repo.startswith("Nemotron"):
        prefixed = f"NVIDIA-{canonical_repo}"
        if prefixed not in candidates:
            candidates.append(prefixed)
        for suffix in ("-BF16", "-FP8", "-NVFP4", "-Base"):
            suffixed = f"{prefixed}{suffix}"
            if suffixed not in candidates:
                candidates.append(suffixed)
        if canonical_repo.startswith("Nemotron-Nano-") and canonical_repo.endswith("-VL"):
            vl_middle = canonical_repo.removeprefix("Nemotron-Nano-").removesuffix("-VL")
            vl_reordered = f"NVIDIA-Nemotron-Nano-VL-{vl_middle}"
            if vl_reordered not in candidates:
                candidates.append(vl_reordered)
            for suffix in ("-BF16", "-FP8", "-NVFP4"):
                suffixed = f"{vl_reordered}{suffix}"
                if suffixed not in candidates:
                    candidates.append(suffixed)

    if org.lower() == "zai-org" and canonical_repo == "GLM-4.5-Air":
        fp8 = f"{canonical_repo}-FP8"
        if fp8 not in candidates:
            candidates.append(fp8)

    if org.lower() in {"cohere", "coherelabs", "cohereforai"} and repo.lower().startswith(
        "command-"
    ):
        c4ai_repo = f"c4ai-{repo.lower()}"
        if c4ai_repo not in candidates:
            candidates.append(c4ai_repo)

    return tuple(candidates)


def _candidate_hf_repo_ids(model_hint: str | None) -> tuple[str, ...]:
    hint = str(model_hint or "").strip()
    if not hint or "/" not in hint:
        return ()

    sanitized = _MODEL_HINT_SUFFIX_RE.sub("", hint)
    org, repo = sanitized.split("/", 1)
    candidates: list[str] = []
    for candidate in (sanitized, _canonicalize_repo_id(sanitized)):
        normalized = candidate.strip()
        if not normalized or "/" not in normalized:
            continue
        if normalized not in candidates:
            candidates.append(normalized)
    for candidate_org in _candidate_org_names(org):
        for candidate_repo in _candidate_repo_names(candidate_org, repo):
            normalized = f"{candidate_org}/{candidate_repo}".strip()
            if normalized and normalized not in candidates:
                candidates.append(normalized)
    return tuple(candidates)


def _load_hf_tokenizer_blocking(model_hint: str) -> Any | None:
    if AutoTokenizer is None:
        return None
    for repo_id in _candidate_hf_repo_ids(model_hint):
        try:
            tokenizer = AutoTokenizer.from_pretrained(
                repo_id,
                use_fast=True,
                trust_remote_code=False,
            )
            with _HF_TOKENIZER_CACHE_LOCK:
                _HF_TOKENIZER_CACHE[model_hint] = tokenizer
            runtime_log(
                logger,
                "tokenizer.hf.loaded",
                verbosity="verbose",
                message="Loaded Hugging Face tokenizer",
                model_hint=model_hint,
                repo_id=repo_id,
            )
            return tokenizer
        except Exception as exc:
            runtime_log(
                logger,
                "tokenizer.hf.load_failed",
                verbosity="debug",
                message="Failed to load Hugging Face tokenizer candidate",
                model_hint=model_hint,
                repo_id=repo_id,
                error=_describe_exception(exc),
            )
            continue
    return None


def _clear_hf_tokenizer_cache() -> None:
    global _HF_TOKENIZER_EXECUTOR
    with _HF_TOKENIZER_CACHE_LOCK:
        futures = tuple(_HF_TOKENIZER_FUTURES.values())
        _HF_TOKENIZER_CACHE.clear()
        _HF_TOKENIZER_FUTURES.clear()
        executor = _HF_TOKENIZER_EXECUTOR
        _HF_TOKENIZER_EXECUTOR = None
    for future in futures:
        future.cancel()
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def _tokenizer_future_done(model_hint: str, future: concurrent.futures.Future[Any | None]) -> None:
    try:
        tokenizer = future.result()
    except concurrent.futures.CancelledError:
        tokenizer = None
        runtime_log(
            logger,
            "tokenizer.hf.preload_cancelled",
            verbosity="debug",
            message="Background tokenizer preload cancelled",
            model_hint=model_hint,
        )
    except Exception as exc:
        tokenizer = None
        runtime_log(
            logger,
            "tokenizer.hf.preload_failed",
            verbosity="verbose",
            level=30,
            message="Background tokenizer preload failed",
            model_hint=model_hint,
            error=_describe_exception(exc),
        )
    with _HF_TOKENIZER_CACHE_LOCK:
        current = _HF_TOKENIZER_FUTURES.get(model_hint)
        if current is future:
            del _HF_TOKENIZER_FUTURES[model_hint]
            if tokenizer is not None:
                _HF_TOKENIZER_CACHE[model_hint] = tokenizer
    if tokenizer is not None:
        runtime_log(
            logger,
            "tokenizer.hf.preload_succeeded",
            verbosity="verbose",
            message="Background tokenizer preload completed",
            model_hint=model_hint,
        )


def schedule_tokenizer_preload(model_hint: str | None) -> bool:
    normalized_hint = str(model_hint or "").strip()
    if not normalized_hint or AutoTokenizer is None:
        return False
    if _resolve_tiktoken_encoding(model_hint=normalized_hint) is not None:
        return False
    if not _candidate_hf_repo_ids(normalized_hint):
        return False

    with _HF_TOKENIZER_CACHE_LOCK:
        if normalized_hint in _HF_TOKENIZER_CACHE:
            return False
        existing = _HF_TOKENIZER_FUTURES.get(normalized_hint)
        if existing is not None and not existing.done():
            return False
        executor = _ensure_hf_tokenizer_executor()
        future = executor.submit(_load_hf_tokenizer_blocking, normalized_hint)
        _HF_TOKENIZER_FUTURES[normalized_hint] = future

    def _complete(completed: concurrent.futures.Future[Any | None]) -> None:
        _tokenizer_future_done(normalized_hint, completed)

    future.add_done_callback(_complete)
    runtime_log(
        logger,
        "tokenizer.hf.preload_scheduled",
        verbosity="debug",
        message="Scheduled background tokenizer preload",
        model_hint=normalized_hint,
    )
    return True


def shutdown_tokenizer_preloads() -> None:
    global _HF_TOKENIZER_EXECUTOR
    with _HF_TOKENIZER_CACHE_LOCK:
        executor = _HF_TOKENIZER_EXECUTOR
        _HF_TOKENIZER_EXECUTOR = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def _load_hf_tokenizer(model_hint: str) -> Any | None:
    if AutoTokenizer is None:
        return None
    with _HF_TOKENIZER_CACHE_LOCK:
        cached = _HF_TOKENIZER_CACHE.get(model_hint)
        pending = _HF_TOKENIZER_FUTURES.get(model_hint)
    if cached is not None:
        runtime_log(
            logger,
            "tokenizer.hf.cache_hit",
            verbosity="debug",
            message="Reused cached Hugging Face tokenizer",
            model_hint=model_hint,
        )
        return cached
    if pending is not None and not pending.done():
        runtime_log(
            logger,
            "tokenizer.hf.preload_pending",
            verbosity="debug",
            message="Tokenizer preload still pending; request path will use heuristic sizing",
            model_hint=model_hint,
        )
        return None
    schedule_tokenizer_preload(model_hint)
    return None


def _resolve_exact_tokenizer(
    *,
    tokenizer_family: str | None = None,
    model_hint: str | None = None,
) -> Any | None:
    tiktoken_encoding = _resolve_tiktoken_encoding(
        tokenizer_family=tokenizer_family,
        model_hint=model_hint,
    )
    if tiktoken_encoding is not None:
        runtime_log(
            logger,
            "tokenizer.exact_resolved",
            verbosity="debug",
            message="Resolved exact tokenizer via tiktoken",
            tokenizer_family=tokenizer_family,
            model_hint=model_hint,
            exact_backend="tiktoken",
        )
        return ("tiktoken", tiktoken_encoding)

    if model_hint:
        hf_tokenizer = _load_hf_tokenizer(str(model_hint))
        if hf_tokenizer is not None:
            runtime_log(
                logger,
                "tokenizer.exact_resolved",
                verbosity="debug",
                message="Resolved exact tokenizer via Hugging Face",
                tokenizer_family=tokenizer_family,
                model_hint=model_hint,
                exact_backend="huggingface",
            )
            return ("hf", hf_tokenizer)
    return None


def _exact_text_token_count(
    text: str,
    *,
    tokenizer_family: str | None = None,
    model_hint: str | None = None,
) -> int | None:
    tokenizer = _resolve_exact_tokenizer(
        tokenizer_family=tokenizer_family,
        model_hint=model_hint,
    )
    if tokenizer is None:
        return None

    tokenizer_kind, tokenizer_impl = tokenizer
    try:
        if tokenizer_kind == "tiktoken":
            return max(len(tokenizer_impl.encode(text, disallowed_special=())), 1)
        if tokenizer_kind == "hf":
            return max(len(tokenizer_impl.encode(text, add_special_tokens=False)), 1)
    except Exception:
        return None
    return None


def _heuristic_profile(tokenizer_family: str | None) -> dict[str, float]:
    family = str(tokenizer_family or "").strip().lower()
    if not family:
        return dict(_HEURISTIC_DEFAULT_PROFILE)
    for key, profile in _HEURISTIC_PROFILES.items():
        if key in family:
            return dict(profile)
    return dict(_HEURISTIC_DEFAULT_PROFILE)


def _detect_text_content_type(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "prose"
    if _JSON_LIKE_RE.match(stripped):
        return "json"

    newline_count = text.count("\n")
    lowered = text.lower()
    code_markers = (
        "def ",
        "class ",
        "return ",
        "import ",
        "from ",
        "function ",
        "const ",
        "let ",
        "var ",
        "=>",
        "#include",
        "public ",
        "private ",
        "protected ",
        "fn ",
        "```",
    )
    punctuation_count = sum(1 for char in text if not char.isalnum() and not char.isspace())
    punctuation_density = punctuation_count / max(len(text), 1)
    if newline_count >= 2 and (
        any(marker in lowered for marker in code_markers) or punctuation_density >= 0.12
    ):
        return "code"
    if any(marker in lowered for marker in code_markers):
        return "code"
    return "prose"


def _heuristic_text_token_count(
    text: str,
    *,
    tokenizer_family: str | None = None,
    content_type: str | None = None,
) -> int:
    if not text:
        return 0

    resolved_content_type = content_type or _detect_text_content_type(text)
    profile = _heuristic_profile(tokenizer_family)
    bytes_per_token = profile.get(
        f"{resolved_content_type}_bytes_per_token",
        _HEURISTIC_DEFAULT_PROFILE["prose_bytes_per_token"],
    )
    token_count = max(math.ceil(len(text.encode("utf-8")) / bytes_per_token), 1)

    # Keep a small floor for short snippets so the lighter heuristic does not
    # accidentally undercount terse prompts.
    word_count = len(re.findall(r"\w+", text, re.UNICODE))
    if resolved_content_type == "prose" and word_count > 0:
        token_count = max(token_count, math.ceil(word_count * 0.75))
    elif resolved_content_type == "code" and word_count > 0:
        token_count = max(token_count, math.ceil(word_count * 0.45))
    elif resolved_content_type == "json" and word_count > 0:
        token_count = max(token_count, math.ceil(word_count * 0.55))

    return token_count


def _text_token_estimate(
    text: str,
    tokenizer_family: str | None = None,
    *,
    model_hint: str | None = None,
) -> int:
    if not text:
        return 0
    exact_count = _exact_text_token_count(
        text,
        tokenizer_family=tokenizer_family,
        model_hint=model_hint,
    )
    if exact_count is not None:
        return exact_count
    runtime_log(
        logger,
        "tokenizer.heuristic_fallback",
        verbosity="debug",
        message="Falling back to heuristic token estimation",
        tokenizer_family=tokenizer_family,
        model_hint=model_hint,
        content_type=_detect_text_content_type(text),
    )
    return _heuristic_text_token_count(
        text,
        tokenizer_family=tokenizer_family,
    )


def _json_token_estimate(
    value: Any,
    tokenizer_family: str | None = None,
    *,
    model_hint: str | None = None,
) -> int:
    return _text_token_estimate(
        json.dumps(value, ensure_ascii=True, separators=(",", ":")),
        tokenizer_family=tokenizer_family,
        model_hint=model_hint,
    )


def _is_image_part(part: Any) -> bool:
    if not isinstance(part, dict):
        return False
    part_type = str(part.get("type", "")).lower()
    return "image" in part_type or "image_url" in part or "input_image" in part


def _estimate_message_tokens(
    message: dict[str, Any],
    tokenizer_family: str | None = None,
    *,
    model_hint: str | None = None,
) -> int:
    total = MESSAGE_OVERHEAD_TOKENS
    total += _estimate_message_content_tokens(
        message.get("content"),
        tokenizer_family=tokenizer_family,
        model_hint=model_hint,
    )

    for field in _TEXT_MESSAGE_FIELDS:
        if field in message and message.get(field):
            total += MESSAGE_METADATA_OVERHEAD_TOKENS
            total += _text_token_estimate(
                str(message.get(field)),
                tokenizer_family=tokenizer_family,
                model_hint=model_hint,
            )

    for field in _JSON_MESSAGE_FIELDS:
        if field in message and message.get(field) is not None:
            total += MESSAGE_METADATA_OVERHEAD_TOKENS
            total += _json_token_estimate(
                message.get(field),
                tokenizer_family=tokenizer_family,
                model_hint=model_hint,
            )

    return total


def _estimate_message_content_tokens(
    content: Any,
    tokenizer_family: str | None = None,
    *,
    model_hint: str | None = None,
) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return _text_token_estimate(
            content,
            tokenizer_family=tokenizer_family,
            model_hint=model_hint,
        )
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, str):
                total += _text_token_estimate(
                    part,
                    tokenizer_family=tokenizer_family,
                    model_hint=model_hint,
                )
                continue
            if _is_image_part(part):
                total += IMAGE_TOKEN_COST
                continue
            if isinstance(part, dict):
                if str(part.get("type", "")).lower() in {"text", "input_text"}:
                    total += _text_token_estimate(
                        str(part.get("text", "")),
                        tokenizer_family=tokenizer_family,
                        model_hint=model_hint,
                    )
                else:
                    total += _json_token_estimate(
                        part,
                        tokenizer_family=tokenizer_family,
                        model_hint=model_hint,
                    )
                continue
            total += _text_token_estimate(
                str(part),
                tokenizer_family=tokenizer_family,
                model_hint=model_hint,
            )
        return total
    if isinstance(content, dict):
        if _is_image_part(content):
            return IMAGE_TOKEN_COST
        if str(content.get("type", "")).lower() in {"text", "input_text"}:
            return _text_token_estimate(
                str(content.get("text", "")),
                tokenizer_family=tokenizer_family,
                model_hint=model_hint,
            )
        return _json_token_estimate(
            content,
            tokenizer_family=tokenizer_family,
            model_hint=model_hint,
        )
    return _text_token_estimate(
        str(content),
        tokenizer_family=tokenizer_family,
        model_hint=model_hint,
    )


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
    model_hint: str | None = None,
) -> int:
    total = REQUEST_OVERHEAD_TOKENS
    for message in messages or []:
        total += _estimate_message_tokens(
            message,
            tokenizer_family=tokenizer_family,
            model_hint=model_hint,
        )

    if tools:
        total += TOOLS_OVERHEAD_TOKENS + _json_token_estimate(
            tools,
            tokenizer_family=tokenizer_family,
            model_hint=model_hint,
        )
    if response_format:
        total += STRUCTURED_OUTPUT_OVERHEAD_TOKENS + _json_token_estimate(
            response_format,
            tokenizer_family=tokenizer_family,
            model_hint=model_hint,
        )

    return max(math.ceil(total * (1.0 + safety_buffer)), 1)
