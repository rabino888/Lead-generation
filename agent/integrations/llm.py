"""
LLM integration — provider chains for website enrichment and structured JSON extraction.

Website enrichment: Gemini Flash → OpenAI mini (no Claude).
Mid-tier model ids are discovered from each provider API (see llm_models.py).
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Optional

from agent.integrations.llm_models import discover_models, log_resolved_website_models
from agent.utils.logger import log

_tokens_used = 0
_tokens_by_provider: dict[str, int] = {}
_models_used: dict[str, str] = {}
_tokens_lock = threading.Lock()
_models_logged = False


def _report_tokens(count: int, provider: str, model_id: str) -> None:
    """Count tokens for the run and attribute them to provider + model."""
    global _tokens_used
    with _tokens_lock:
        _tokens_used += count
        _tokens_by_provider[provider] = int(_tokens_by_provider.get(provider) or 0) + count
        _models_used[provider] = model_id
    from agent.utils.cost_tracker import get_cost_tracker
    tracker = get_cost_tracker()
    if tracker and count:
        tracker.add_usage(llm_tokens=count)


def _ensure_models_logged() -> None:
    global _models_logged
    if not _models_logged:
        log_resolved_website_models()
        _models_logged = True


_PROVIDER_FNS = {
    "claude": lambda p, s, m: _call_claude(p, s, m),
    "openai": lambda p, s, m: _call_openai(p, s, m),
    "gemini": lambda p, s, m: _call_gemini(p, s, m),
}

# Default chain for generic LLM calls (admin/diagnostics).
DEFAULT_PROVIDERS = ("claude", "openai", "gemini")
# Website enrichment: structured JSON from Firecrawl markdown — no Claude.
WEBSITE_PROVIDERS = ("gemini", "openai")

_GEMINI_QUOTA_MARKER = (
    Path(__file__).resolve().parents[2] / "data" / ".gemini_daily_quota"
)


def _gemini_quota_marker_path() -> Path:
    return _GEMINI_QUOTA_MARKER


def mark_gemini_daily_quota_exhausted() -> None:
    """Skip Gemini for website LLM until UTC date rolls over."""
    path = _gemini_quota_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"date_utc": date.today().isoformat(), "marked_at_utc": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )


def is_gemini_daily_quota_exhausted() -> bool:
    path = _gemini_quota_marker_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("date_utc") == date.today().isoformat()
    except (OSError, json.JSONDecodeError):
        return False


def clear_gemini_daily_quota_marker() -> None:
    _gemini_quota_marker_path().unlink(missing_ok=True)


def website_providers() -> tuple[str, ...]:
    """Override chain with WEBSITE_LLM_PROVIDERS=openai,gemini in .env."""
    raw = (os.environ.get("WEBSITE_LLM_PROVIDERS") or "").strip()
    if not raw:
        providers: tuple[str, ...] = WEBSITE_PROVIDERS
    else:
        providers = tuple(p.strip().lower() for p in raw.split(",") if p.strip())
    if is_gemini_daily_quota_exhausted() and "gemini" in providers:
        if os.environ.get("GEMINI_FORCE_WEBSITE", "").strip().lower() in ("1", "true", "yes"):
            return providers
        filtered = tuple(p for p in providers if p != "gemini")
        if filtered:
            log.info(
                "Gemini daily quota exhausted — website LLM chain: %s",
                ", ".join(filtered),
            )
            return filtered
    return providers


def call_llm(
    prompt: str,
    system: str = "You are a precise B2B lead qualification analyst. Always respond with valid JSON.",
    max_tokens: int = 2000,
    expect_json: bool = True,
    providers: Optional[tuple[str, ...] | list[str]] = None,
) -> Any:
    """
    Call LLM providers in order. Default: Claude → OpenAI → Gemini.
    Pass ``providers`` to restrict the chain (e.g. WEBSITE_PROVIDERS skips Claude).
    Returns parsed JSON if expect_json=True, else raw string.
    """
    chain = list(providers) if providers is not None else list(DEFAULT_PROVIDERS)
    if chain == list(WEBSITE_PROVIDERS):
        _ensure_models_logged()

    last_error = None
    for name in chain:
        fn = _PROVIDER_FNS.get(name)
        if not fn:
            raise ValueError(f"Unknown LLM provider '{name}'")
        if not _has_key(name):
            continue
        try:
            result = fn(prompt, system, max_tokens)
            if expect_json:
                return _parse_json(result)
            return result
        except Exception as e:
            if name == "gemini" and _gemini_daily_quota_exceeded(e):
                mark_gemini_daily_quota_exhausted()
            log.warning("LLM provider '%s' failed: %s — trying next", name, e)
            last_error = e

    raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")


def _call_claude(prompt: str, system: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    last_error: Exception | None = None
    for model_name in discover_models("claude", tier="mid"):
        try:
            response = client.messages.create(
                model=model_name,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            _report_tokens(
                response.usage.input_tokens + response.usage.output_tokens,
                "claude",
                model_name,
            )
            return response.content[0].text
        except Exception as e:
            last_error = e
            log.debug("Claude model %s failed: %s", model_name, e)
            continue
    raise RuntimeError(f"No Claude Sonnet model available: {last_error}")


def _openai_chat(
    client,
    model_name: str,
    messages: list[dict[str, str]],
    max_tokens: int,
) -> Any:
    """OpenAI chat.completions — newer models use max_completion_tokens."""
    try:
        return client.chat.completions.create(
            model=model_name,
            max_tokens=max_tokens,
            messages=messages,
        )
    except Exception as e:
        err = str(e).lower()
        if "max_tokens" in err and "max_completion_tokens" in err:
            return client.chat.completions.create(
                model=model_name,
                max_completion_tokens=max_tokens,
                messages=messages,
            )
        raise


def _call_openai(prompt: str, system: str, max_tokens: int) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    last_error: Exception | None = None
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    for model_name in discover_models("openai", tier="mid"):
        try:
            response = _openai_chat(client, model_name, messages, max_tokens)
            usage = response.usage
            if usage:
                _report_tokens(usage.total_tokens, "openai", model_name)
            return response.choices[0].message.content or ""
        except Exception as e:
            last_error = e
            log.debug("OpenAI model %s failed: %s", model_name, e)
            continue
    raise RuntimeError(f"No OpenAI mini model available: {last_error}")


def _gemini_daily_quota_exceeded(exc: Exception) -> bool:
    msg = str(exc)
    return "429" in msg and "PerDay" in msg


def _call_gemini(prompt: str, system: str, max_tokens: int) -> str:
    import time

    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    last_error: Exception | None = None
    for model_name in discover_models("gemini", tier="mid"):
        for attempt in range(3):
            try:
                model = genai.GenerativeModel(
                    model_name=model_name,
                    generation_config={"max_output_tokens": max_tokens},
                )
                response = model.generate_content(f"{system}\n\n{prompt}")
                usage = getattr(response, "usage_metadata", None)
                if usage:
                    _report_tokens(
                        int(
                            (getattr(usage, "prompt_token_count", 0) or 0)
                            + (getattr(usage, "candidates_token_count", 0) or 0)
                        ),
                        "gemini",
                        model_name,
                    )
                return response.text
            except Exception as e:
                last_error = e
                if _gemini_daily_quota_exceeded(e):
                    mark_gemini_daily_quota_exhausted()
                    raise RuntimeError(f"Gemini daily quota exhausted: {e}") from e
                if "429" in str(e) and attempt < 2:
                    time.sleep(12 * (attempt + 1))
                    continue
                log.debug("Gemini model %s failed: %s", model_name, e)
                break
    raise RuntimeError(f"No Gemini Flash model available: {last_error}")


def _has_key(provider: str) -> bool:
    keys = {
        "claude": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }
    return bool(os.environ.get(keys.get(provider, "")))


def _parse_json(text: str) -> Any:
    """Extract and parse JSON from LLM response (handles markdown code blocks)."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        text = match.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start != -1 and end != -1:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass
        raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}")


def get_tokens_used() -> int:
    """Return total LLM tokens used in this session."""
    return _tokens_used


def reset_tokens_used() -> None:
    """Reset per-run LLM token counter."""
    global _tokens_used, _tokens_by_provider, _models_used, _models_logged
    _tokens_used = 0
    _tokens_by_provider = {}
    _models_used = {}
    _models_logged = False


def get_llm_usage_summary() -> dict[str, Any]:
    """Tokens used this session, broken down by provider and resolved model id."""
    with _tokens_lock:
        by_provider = dict(_tokens_by_provider)
        models = dict(_models_used)
        total = _tokens_used
    primary = None
    if by_provider:
        primary = max(by_provider, key=lambda k: by_provider[k])
    return {
        "tokens": total,
        "providers": by_provider,
        "models": models,
        "primary_provider": primary,
        "primary_model": models.get(primary) if primary else None,
    }


def call_gemini(
    prompt: str,
    system: str = "You are a precise B2B analyst. Always respond with valid JSON.",
    max_tokens: int = 1000,
    expect_json: bool = True,
) -> Any:
    """Call Gemini directly (no Claude/OpenAI fallback)."""
    if not _has_key("gemini"):
        raise RuntimeError("GEMINI_API_KEY is not configured")
    result = _call_gemini(prompt, system, max_tokens)
    if expect_json:
        return _parse_json(result)
    return result
