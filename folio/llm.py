"""LLM access: client, model catalogue, and JSON calls with validation and fallbacks."""

from __future__ import annotations

import json
import re
from typing import TypeVar

import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

T = TypeVar("T", bound=BaseModel)

# Model ids containing any of these aren't general chat models (speech, safety
# classifiers, embeddings), or are narrow (single-language, agentic web tools).
EXCLUDED_MARKERS = (
    "whisper", "tts", "guard", "embed", "moderation", "allam", "orpheus", "playai",
    "compound", "distil", "safeguard", "vision-preview",
)

# Friendly names for well-known ids, in order of preference for the default.
CATALOG: list[tuple[str, str, str]] = [  # (id, label, subtitle)
    ("openai/gpt-oss-120b", "Best quality", "GPT-OSS 120B"),
    ("llama-3.3-70b-versatile", "Balanced", "Llama 3.3 70B"),
    ("llama-3.1-8b-instant", "Fastest", "Llama 3.1 8B"),
    ("openai/gpt-oss-20b", "Fast reasoning", "GPT-OSS 20B"),
    ("meta-llama/llama-4-maverick-17b-128e-instruct", "Llama 4 Maverick", "Meta"),
    ("meta-llama/llama-4-scout-17b-16e-instruct", "Llama 4 Scout", "Meta"),
    ("qwen/qwen3-32b", "Qwen3 32B", "Alibaba"),
    ("moonshotai/kimi-k2-instruct", "Kimi K2", "Moonshot AI"),
    ("moonshotai/kimi-k2-instruct-0905", "Kimi K2 (0905)", "Moonshot AI"),
]
_LABELS = {mid: (label, sub) for mid, label, sub in CATALOG}
# Default preference: the configured model, then a good all-rounder with generous free limits.
DEFAULT_ORDER = [LLM_MODEL, "llama-3.3-70b-versatile", "openai/gpt-oss-120b", "llama-3.1-8b-instant"]


def client(api_key: str = "") -> OpenAI:
    """A client for this request. A server key (from .env) always wins over a browser key."""
    return OpenAI(api_key=LLM_API_KEY or api_key or "missing", base_url=LLM_BASE_URL, max_retries=1, timeout=90)


def is_chat_model(model_id: str) -> bool:
    mid = model_id.lower()
    return not any(m in mid for m in EXCLUDED_MARKERS)


def friendly_name(model_id: str) -> str:
    """Label shown in the model picker, e.g. "Best quality"."""
    return friendly(model_id)[0]


def friendly(model_id: str) -> tuple[str, str]:
    """(label, subtitle) for the model picker; unknown ids get a tidied name and the raw id."""
    if model_id in _LABELS:
        return _LABELS[model_id]
    return _pretty(model_id), model_id


def _pretty(model_id: str) -> str:
    base = model_id.rsplit("/", 1)[-1]
    return re.sub(r"[-_]+", " ", base).strip().title()


def chat_models(ids: list[str]) -> tuple[list[dict], str]:
    """Filter and label provider model ids; return ([{id, label}], default id)."""
    usable = [m for m in ids if is_chat_model(m)]
    rank = {mid: i for i, (mid, _, _) in enumerate(CATALOG)}
    usable.sort(key=lambda m: (rank.get(m, len(rank)), m))
    default = next((m for m in DEFAULT_ORDER if m in usable), usable[0] if usable else "")
    return [{"id": m, "label": friendly(m)[0], "sub": friendly(m)[1]} for m in usable], default


def list_model_ids(api_key: str = "") -> list[str]:
    return [m.id for m in client(api_key).models.list().data]


# --- JSON -------------------------------------------------------------------------
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def parse_json(text: str) -> dict | None:
    """Find and parse the first JSON object in a model reply (tolerates fences and chatter)."""
    if not text:
        return None
    text = strip_think(text)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start:i + 1])
                        if isinstance(value, dict):
                            return value
                    except json.JSONDecodeError:
                        pass
                    break
        start = text.find("{", start + 1)
    return None


def complete_json(llm: OpenAI, model: str, messages: list[dict], schema: type[T], max_tokens: int = 1200,
                  extra: dict | None = None) -> T | None:
    """One JSON-producing call, validated against `schema`. Returns None on any failure.

    Uses the provider's JSON mode where supported; if the provider rejects it
    (or the model's output fails the provider's own JSON validation), retries
    once without it and parses leniently.
    """
    for mode in ({"response_format": {"type": "json_object"}}, {}):
        try:
            response = llm.chat.completions.create(
                model=model, messages=messages, temperature=0, max_tokens=max_tokens, **mode, **(extra or {}),
            )
        except openai.BadRequestError:
            continue  # JSON mode unsupported or json_validate_failed: try plain
        except openai.APIError:
            return None  # rate limit, auth, network: this extra step is skipped
        data = parse_json(response.choices[0].message.content or "")
        if data is None:
            continue
        try:
            return schema.model_validate(data)
        except ValidationError:
            continue
    return None
