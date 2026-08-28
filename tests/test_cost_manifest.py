"""Tests for cost manifest helpers."""
from __future__ import annotations

from agent.utils.cost_manifest import build_manifest_entry_from_report


def test_build_apollo_manifest_entry_uses_credits():
    report = {
        "phase": "stage_apollo",
        "run_id": "run_test_1",
        "started_at_utc": "2026-08-28T10:00:00+00:00",
        "ended_at_utc": "2026-08-28T10:05:00+00:00",
        "cost_tracker": {
            "run_totals": {
                "apollo_usd": 1.2,
                "total_usd": 1.2,
            }
        },
        "apify_truth_usd": 0,
    }
    entry = build_manifest_entry_from_report(
        report,
        label="Apollo batch",
        outcome={"contactable": 5},
        apollo_extra={"credits_consumed": 60, "contactable": 5},
    )
    assert entry["stage"] == "apollo_contact"
    assert entry["apollo"]["credits_consumed"] == 60
    assert entry["run_key"] == "stage_apollo_run_test_1"


def test_build_enrichment_manifest_entry_apify_truth():
    report = {
        "phase": "stage_enrich",
        "run_id": "run_enrich_1",
        "started_at_utc": "2026-08-28T11:00:00+00:00",
        "ended_at_utc": "2026-08-28T11:30:00+00:00",
        "apify_truth_usd": 2.5,
        "apify_drift_usd": 2.0,
        "cost_tracker": {
            "run_totals": {
                "apify_usd": 0.5,
                "firecrawl_usd": 1.0,
                "llm_usd": 0.3,
                "total_usd": 1.8,
            }
        },
    }
    entry = build_manifest_entry_from_report(report, outcome={"leads": 10})
    assert entry["stage"] == "enrichment"
    assert entry["apify"]["api_truth_usd"] == 2.5
    assert entry["cost_tracker"]["total_usd"] == 1.8
