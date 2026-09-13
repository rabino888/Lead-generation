"""Tests for LLM model discovery helpers."""
from __future__ import annotations

import os

import pytest

from agent.integrations.llm_models import (
    discover_models,
    refresh_llm_model_cache,
    _filter_model_ids,
)


def test_filter_gemini_flash_mid_tier():
    ids = _filter_model_ids(
        "gemini",
        [
            "gemini-2.0-flash",
            "gemini-2.5-flash",
            "gemini-1.5-pro",
            "gemini-embedding-001",
        ],
        "mid",
    )
    assert "gemini-2.5-flash" in ids
    assert ids[0] == "gemini-2.5-flash"
    assert "gemini-1.5-pro" not in ids
    assert "gemini-embedding-001" not in ids


def test_filter_openai_mini_mid_tier():
    ids = _filter_model_ids(
        "openai",
        ["gpt-4o-mini", "gpt-5-mini", "gpt-4o", "gpt-4o-realtime-preview"],
        "mid",
    )
    assert ids[0] == "gpt-5-mini"
    assert "gpt-4o" not in ids
    assert "gpt-4o-realtime-preview" not in ids


def test_filter_claude_sonnet_mid_tier():
    ids = _filter_model_ids(
        "claude",
        ["claude-3-5-sonnet-latest", "claude-sonnet-4-5", "claude-3-opus-20240229"],
        "mid",
    )
    assert "claude-sonnet-4-5" in ids
    assert "claude-3-opus-20240229" not in ids


def test_pinned_model_env_overrides_discovery(monkeypatch):
    monkeypatch.setenv("LLM_GEMINI_MODEL", "gemini-custom-flash")
    refresh_llm_model_cache("gemini")
    assert discover_models("gemini") == ["gemini-custom-flash"]


def test_fallback_when_no_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    refresh_llm_model_cache("gemini")
    models = discover_models("gemini", use_cache=False)
    assert models[0] == "gemini-3.6-flash"
