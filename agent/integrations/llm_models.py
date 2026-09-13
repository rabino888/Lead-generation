"""
Discover mid-tier LLM model IDs from provider APIs (Flash / mini / Sonnet).

Used for website summarization from Firecrawl markdown. Results are cached per
process; override with LLM_{PROVIDER}_MODEL in .env to pin a specific id.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Callable, Optional

from agent.utils.logger import log

Tier = str  # "mid" | "any"

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, list[str]]] = {}
_DEFAULT_TTL = int(os.environ.get("LLM_MODEL_CACHE_TTL_SEC", "3600"))

# Static fallbacks when list APIs fail or keys are missing.
_FALLBACK_MODELS: dict[str, list[str]] = {
    "gemini": [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-flash-latest",
    ],
    "openai": [
        "gpt-5-mini",
        "gpt-4.1-mini",
        "gpt-4o-mini",
    ],
    "claude": [
        "claude-sonnet-4-5",
        "claude-sonnet-4-0",
        "claude-3-5-sonnet-latest",
    ],
}

_ENV_MODEL_KEYS = {
    "gemini": "LLM_GEMINI_MODEL",
    "openai": "LLM_OPENAI_MODEL",
    "claude": "LLM_ANTHROPIC_MODEL",
}

# Mid-tier heuristics — website JSON extraction, not flagship reasoning.
_MID_TIER_RULES: dict[str, dict[str, Any]] = {
    "gemini": {
        "include": re.compile(r"gemini", re.I),
        "require": re.compile(r"flash", re.I),
        "exclude": re.compile(
            r"embedding|embed|aqa|imagen|veo|tts|live|computer|thinking|lite",
            re.I,
        ),
    },
    "openai": {
        "include": re.compile(r"^gpt-", re.I),
        "require": re.compile(r"mini", re.I),
        "exclude": re.compile(
            r"realtime|audio|transcribe|search|codex|instruct|legacy|davinci|babbage|tts",
            re.I,
        ),
    },
    "claude": {
        "include": re.compile(r"claude", re.I),
        "require": re.compile(r"sonnet", re.I),
        "exclude": re.compile(r"opus|haiku|instant", re.I),
    },
}


def _provider_env_key(provider: str) -> Optional[str]:
    return _ENV_MODEL_KEYS.get(provider)


def _pinned_model(provider: str) -> Optional[str]:
    key = _provider_env_key(provider)
    if not key:
        return None
    value = (os.environ.get(key) or "").strip()
    return value or None


def _version_sort_key(model_id: str) -> tuple:
    """Higher tuple sorts first — prefers newer version numbers in model ids."""
    lower = model_id.lower()
    numbers = [float(n) for n in re.findall(r"\d+\.?\d*", lower)]
    version_score = max(numbers) if numbers else 0.0
    # Prefer explicit mini/flash/sonnet over generic aliases.
    alias_penalty = 1.0 if re.search(r"latest|preview|exp", lower) else 0.0
    return (version_score, -alias_penalty, lower)


def _filter_model_ids(provider: str, model_ids: list[str], tier: Tier) -> list[str]:
    rules = _MID_TIER_RULES.get(provider, {})
    include = rules.get("include")
    require = rules.get("require")
    exclude = rules.get("exclude")

    out: list[str] = []
    seen: set[str] = set()
    for raw in model_ids:
        mid = (raw or "").strip()
        if mid.startswith("models/"):
            mid = mid.split("/", 1)[1]
        if not mid or mid in seen:
            continue
        seen.add(mid)
        if tier == "mid":
            if include and not include.search(mid):
                continue
            if require and not require.search(mid):
                continue
            if exclude and exclude.search(mid):
                continue
        out.append(mid)
    out.sort(key=_version_sort_key, reverse=True)
    return out


def _list_gemini_models() -> list[str]:
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    ids: list[str] = []
    for m in genai.list_models():
        methods = getattr(m, "supported_generation_methods", None) or []
        if "generateContent" not in methods:
            continue
        name = getattr(m, "name", "") or ""
        ids.append(name.replace("models/", ""))
    return ids


def _list_openai_models() -> list[str]:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return [m.id for m in client.models.list().data]


def _list_claude_models() -> list[str]:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    # SDK returns paginated iterator of model objects.
    return [m.id for m in client.models.list()]


_LISTERS: dict[str, Callable[[], list[str]]] = {
    "gemini": _list_gemini_models,
    "openai": _list_openai_models,
    "claude": _list_claude_models,
}


def refresh_llm_model_cache(provider: Optional[str] = None) -> None:
    """Clear cached model lists (call after key rotation or to force re-discovery)."""
    with _CACHE_LOCK:
        if provider:
            _CACHE.pop(provider, None)
        else:
            _CACHE.clear()


def discover_models(
    provider: str,
    *,
    tier: Tier = "mid",
    use_cache: bool = True,
    ttl_sec: Optional[int] = None,
) -> list[str]:
    """
    Return model ids to try for a provider, newest mid-tier first.

    Order: env pin (LLM_*_MODEL) → API discovery → static fallbacks.
    """
    provider = provider.lower().strip()
    pinned = _pinned_model(provider)
    if pinned:
        return [pinned]

    ttl = ttl_sec if ttl_sec is not None else _DEFAULT_TTL
    cache_key = f"{provider}:{tier}"
    now = time.time()

    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if use_cache and cached and now - cached[0] < ttl:
            return list(cached[1])

    discovered: list[str] = []
    lister = _LISTERS.get(provider)
    api_keys = {
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "claude": "ANTHROPIC_API_KEY",
    }
    if lister and os.environ.get(api_keys.get(provider, "")):
        try:
            discovered = _filter_model_ids(provider, lister(), tier)
            if discovered:
                log.info(
                    "LLM model discovery (%s): %s",
                    provider,
                    ", ".join(discovered[:3]),
                )
        except Exception as e:
            log.warning("LLM model discovery failed for %s: %s", provider, e)

    fallbacks = list(_FALLBACK_MODELS.get(provider, []))
    merged: list[str] = []
    seen: set[str] = set()
    # API-discovered ids first (newest), then static fallbacks.
    for mid in discovered + fallbacks:
        if mid not in seen:
            seen.add(mid)
            merged.append(mid)

    with _CACHE_LOCK:
        _CACHE[cache_key] = (now, merged)
    return merged


def get_primary_model(provider: str, *, tier: Tier = "mid") -> Optional[str]:
    """Best model id for a provider, or None if no key / no candidates."""
    models = discover_models(provider, tier=tier)
    return models[0] if models else None


def get_resolved_website_models() -> dict[str, str]:
    """Primary mid-tier model per provider (for logs / cost dashboard)."""
    out: dict[str, str] = {}
    for provider in ("gemini", "openai", "claude"):
        primary = get_primary_model(provider, tier="mid")
        if primary:
            out[provider] = primary
    return out


def log_resolved_website_models() -> dict[str, str]:
    """Log and return resolved website-stage models."""
    resolved = get_resolved_website_models()
    if resolved:
        log.info(
            "Website LLM models (mid-tier): %s",
            ", ".join(f"{k}={v}" for k, v in resolved.items()),
        )
    return resolved
