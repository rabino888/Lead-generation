"""Tests for website repair guardrails and markdown cache."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.integrations.llm import (
    clear_gemini_daily_quota_marker,
    is_gemini_daily_quota_exhausted,
    mark_gemini_daily_quota_exhausted,
    website_providers,
)
from agent.utils.website_markdown_cache import load_markdown, save_markdown
from agent.utils.website_repair_guard import (
    RepairLock,
    assert_repair_run_budget,
    classify_repair_rows,
    count_recent_repair_runs,
    llm_only_eligible,
    row_needs_repair,
)


def test_row_needs_repair_empty_summary():
    row = {"website": "https://acme.com", "website_summary": ""}
    assert row_needs_repair(row) is True


def test_row_needs_repair_failed_marker():
    row = {
        "website": "https://acme.com",
        "website_summary": "Pitch analysis failed: bad json",
    }
    assert row_needs_repair(row) is True


def test_row_needs_repair_ok_summary():
    row = {
        "website": "https://acme.com",
        "website_summary": "Acme builds widgets for enterprise buyers.",
    }
    assert row_needs_repair(row) is False


def test_markdown_cache_roundtrip(tmp_path: Path):
    campaign_dir = tmp_path / "camp"
    save_markdown(
        campaign_dir,
        "https://www.acme.com",
        "# About\nWe build things for enterprise teams worldwide.\n\n---\n\n"
        "### [/careers]\nHiring engineers and product managers across US and EU.",
        careers_url="https://www.acme.com/careers",
    )
    cached = load_markdown(campaign_dir, "acme.com")
    assert cached is not None
    assert "About" in cached.markdown
    assert cached.careers_url == "https://www.acme.com/careers"


def test_llm_only_eligible_with_cache(tmp_path: Path):
    campaign_dir = tmp_path / "camp"
    save_markdown(campaign_dir, "https://acme.com", "x" * 200)
    row = {"website": "https://acme.com", "website_summary": ""}
    assert llm_only_eligible(row, campaign_dir) is True


def test_llm_only_eligible_partial_signals_without_cache(tmp_path: Path):
    row = {
        "website": "https://acme.com",
        "website_summary": "Analysis failed: timeout",
        "website_is_hiring": "yes",
        "services_offered": "SaaS; consulting",
    }
    assert llm_only_eligible(row, tmp_path / "camp") is True


def test_classify_repair_rows_auto(tmp_path: Path):
    campaign_dir = tmp_path / "camp"
    save_markdown(campaign_dir, "https://cached.com", "y" * 150)
    rows = [
        {"company_name": "Cached", "website": "https://cached.com", "website_summary": ""},
        {"company_name": "Fresh", "website": "https://fresh.com", "website_summary": ""},
    ]
    llm_rows, crawl_rows, skipped = classify_repair_rows(rows, campaign_dir)
    assert len(llm_rows) == 1
    assert llm_rows[0]["company_name"] == "Cached"
    assert len(crawl_rows) == 1
    assert crawl_rows[0]["company_name"] == "Fresh"
    assert skipped == []


def test_repair_lock_blocks_second_acquire(tmp_path: Path):
    lock_a = RepairLock(tmp_path)
    lock_b = RepairLock(tmp_path)
    lock_a.acquire()
    with pytest.raises(RuntimeError, match="already running"):
        lock_b.acquire()
    lock_a.release()
    lock_b.acquire()
    lock_b.release()


def test_repair_run_cap(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WEBSITE_REPAIR_MAX_RUNS_PER_24H", "2")
    campaign_dir = tmp_path / "automata_us_rnd"
    campaign_dir.mkdir()
    now = datetime.now(UTC).isoformat()
    manifest = {
        "schema_version": "2",
        "campaign_id": "automata_us_rnd",
        "runs": [
            {"stage": "website_repair", "started_at_utc": now},
            {"stage": "website_repair", "started_at_utc": now},
        ],
    }
    (campaign_dir / "cost_runs.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert count_recent_repair_runs(campaign_dir) == 2
    with pytest.raises(RuntimeError, match="run cap"):
        assert_repair_run_budget(campaign_dir)


def test_gemini_quota_marker_skips_gemini(monkeypatch, tmp_path: Path):
    marker = tmp_path / ".gemini_daily_quota"
    monkeypatch.setattr("agent.integrations.llm._GEMINI_QUOTA_MARKER", marker)
    clear_gemini_daily_quota_marker()
    monkeypatch.delenv("WEBSITE_LLM_PROVIDERS", raising=False)
    assert "gemini" in website_providers()
    mark_gemini_daily_quota_exhausted()
    assert is_gemini_daily_quota_exhausted()
    assert website_providers() == ("openai",)
    clear_gemini_daily_quota_marker()
