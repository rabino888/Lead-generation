"""
LLM integration — Claude primary, OpenAI + Gemini fallbacks.
Tracks token usage across the pipeline.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from agent.utils.logger import log

_tokens_used = 0


def call_llm(
    prompt: str,
    system: str = "You are a precise B2B lead qualification analyst. Always respond with valid JSON.",
    max_tokens: int = 2000,
    expect_json: bool = True,
) -> Any:
    """
    Call Claude (primary). Falls back to OpenAI then Gemini on failure.
    Returns parsed JSON if expect_json=True, else raw string.
    """
    providers = [
        ("claude", _call_claude),
        ("openai", _call_openai),
        ("gemini", _call_gemini),
    ]

    last_error = None
    for name, fn in providers:
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
    global _tokens_used
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    _tokens_used += response.usage.input_tokens + response.usage.output_tokens
    return response.content[0].text


def _call_openai(prompt: str, system: str, max_tokens: int) -> str:
    global _tokens_used
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    usage = response.usage
    if usage:
        _tokens_used += usage.total_tokens
    return response.choices[0].message.content or ""


def _call_gemini(prompt: str, system: str, max_tokens: int) -> str:
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    # Try flash first, fall back to pro
    for model_name in ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]:
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                generation_config={"max_output_tokens": max_tokens},
            )
            response = model.generate_content(f"{system}\n\n{prompt}")
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
