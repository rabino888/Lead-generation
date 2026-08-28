"""
LLM integration — provider chains for website enrichment and structured JSON extraction.

Website enrichment: Gemini Flash → OpenAI (no Claude).
Tracks token usage across the pipeline.
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Optional

from agent.utils.logger import log

_tokens_used = 0
_tokens_lock = threading.Lock()


def _report_tokens(count: int) -> None:
    """Count tokens for the run and attribute them to the current lead."""
    global _tokens_used
    with _tokens_lock:
        _tokens_used += count
    from agent.utils.cost_tracker import get_cost_tracker
    tracker = get_cost_tracker()
    if tracker and count:
        tracker.add_usage(llm_tokens=count)


_PROVIDER_FNS = {
    "claude": lambda p, s, m: _call_claude(p, s, m),
    "openai": lambda p, s, m: _call_openai(p, s, m),
    "gemini": lambda p, s, m: _call_gemini(p, s, m),
}

# Default chain for generic LLM calls (admin/diagnostics).
DEFAULT_PROVIDERS = ("claude", "openai", "gemini")
# Website enrichment: structured JSON from Firecrawl markdown — no Claude.
WEBSITE_PROVIDERS = ("gemini", "openai")


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
            log.warning("LLM provider '%s' failed: %s — trying next", name, e)
            last_error = e

    raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")


def _call_claude(prompt: str, system: str, max_tokens: int) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    _report_tokens(response.usage.input_tokens + response.usage.output_tokens)
    return response.content[0].text


def _call_openai(prompt: str, system: str, max_tokens: int) -> str:
    global _tokens_used
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    # Prefer current mini; keep older IDs as fallback if the account hasn't rolled.
    last_error: Exception | None = None
    for model_name in ("gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"):
        try:
            response = client.chat.completions.create(
                model=model_name,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
            usage = response.usage
            if usage:
                _report_tokens(usage.total_tokens)
            return response.choices[0].message.content or ""
        except Exception as e:
            last_error = e
            continue
    raise RuntimeError(f"No OpenAI mini model available: {last_error}")


def _call_gemini(prompt: str, system: str, max_tokens: int) -> str:
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    # Newest Flash first; older Flash IDs kept as fallbacks.
    for model_name in (
        "gemini-3.7-flash",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-flash-latest",
    ):
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                generation_config={"max_output_tokens": max_tokens},
            )
            response = model.generate_content(f"{system}\n\n{prompt}")
            usage = getattr(response, "usage_metadata", None)
            if usage:
                _report_tokens(int(
                    (getattr(usage, "prompt_token_count", 0) or 0)
                    + (getattr(usage, "candidates_token_count", 0) or 0)
                ))
            return response.text
        except Exception:
            continue
    raise RuntimeError("No Gemini model available")


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
    # Strip markdown code fences if present
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        text = match.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last resort: find first { or [ and try from there
        for start_char, end_char in [('{', '}'), ('[', ']')]:
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
    global _tokens_used
    _tokens_used = 0


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
