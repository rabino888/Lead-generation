"""Tests for cost report builder (schema v4)."""
from __future__ import annotations

import json
from pathlib import Path

from agent.utils.cost_report import (
    _apollo_from_entry,
    _firecrawl_from_entry,
    _llm_from_entry,
    SCHEMA_VERSION,
)


def test_apollo_uses_credits_consumed():
    usd, sources = _apollo_from_entry(
        {"run_id": "run_x"},
        {"credits_consumed": 400, "contactable": 50},
        0.02,
        Path("/tmp"),
        None,
    )
    assert usd == 8.0
    assert sources["apollo"]["credits"] == 400
    assert sources["apollo"]["basis"] == "apollo_credits"
    assert sources["apollo"]["is_est"] is False


def test_apollo_legacy_estimate_marked_est():
    usd, sources = _apollo_from_entry(
        {},
        {"org_enrich_calls": 10, "email_reveals": 5},
        0.02,
        Path("/tmp"),
        None,
    )
    assert usd == 0.3
    assert sources["apollo_org_enrich"]["is_est"] is True
    assert sources["apollo_org_enrich"]["basis"] == "apollo_credit_estimate"


def test_firecrawl_free_plan_zero_usd():
    usd, sources = _firecrawl_from_entry(
        {"credits": 115, "plan_credits": 1000, "plan_name": "free"},
        {},
        {"plan_name": "free", "plan_credits": 1000, "usd_per_credit": 0},
        0.01,
    )
    assert usd == 0.0
    assert sources["firecrawl"]["credits"] == 115
    assert sources["firecrawl"]["plan_remaining_est"] == 885


def test_llm_shows_provider_in_label():
    usd, sources = _llm_from_entry(
        {"tokens": 1000, "providers": {"gemini": 1000}, "primary_provider": "gemini"},
        {},
        0.003,
    )
    assert usd == 0.003
    assert "gemini" in sources["llm"]["display_label"]
    assert sources["llm"]["is_est"] is True


def test_sample_report_schema_version():
    sample = Path("examples/campaign_cost_dashboard/sample_report.json")
    data = json.loads(sample.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION
    assert "usd_billed" not in data["totals"]
