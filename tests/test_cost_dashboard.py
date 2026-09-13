"""Tests for the lead-list cost dashboard index + renderer."""
from __future__ import annotations

import json
from pathlib import Path

from agent.utils.cost_dashboard import (
    _count_unique_csv,
    _is_live_stage_csv,
    campaign_lead_counts,
    legacy_campaign_rollup,
)
from agent.utils.cost_dashboard_html import render_body, render_document


def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")


def test_backup_and_partial_csvs_are_skipped():
    assert _is_live_stage_csv(Path("04_apollo_contactable.csv"))
    assert not _is_live_stage_csv(Path("04_apollo_contactable.csv.bak_20260827_170955"))
    assert not _is_live_stage_csv(Path("04_apollo_rejected.csv"))
    assert not _is_live_stage_csv(Path("05_enriched_partial24.csv"))


def test_count_unique_csv_dedupes_across_batch_files(tmp_path):
    header = "website,company_name"
    _write_csv(tmp_path / "a.csv", header, ["acme.com,Acme", "beta.io,Beta"])
    _write_csv(tmp_path / "b.csv", header, ["ACME.com,Acme", "gamma.dev,Gamma"])

    count = _count_unique_csv(
        [tmp_path / "a.csv", tmp_path / "b.csv"], ("website", "company_name")
    )
    assert count == 3


def test_lead_counts_fall_back_to_root_seeds(tmp_path):
    campaign = tmp_path / "legacy_campaign"
    _write_csv(campaign / "seeds.csv", "seed_id,company_name", ["s1,Acme", "s2,Beta"])

    counts = campaign_lead_counts(campaign)
    assert counts["seeds"] == 2
    assert counts["contactable"] == 0


def test_lead_counts_ignore_backup_contactable_files(tmp_path):
    campaign = tmp_path / "c"
    header = "lead_id,decision_maker_email"
    _write_csv(campaign / "stages" / "04_apollo_contactable.csv", header, ["1,a@x.com"])
    _write_csv(
        campaign / "stages" / "04_apollo_contactable.csv.bak_1", header, ["2,b@x.com"]
    )
    _write_csv(campaign / "stages" / "04_apollo_rejected.csv", header, ["3,c@x.com"])

    assert campaign_lead_counts(campaign)["contactable"] == 1


def test_legacy_rollup_reads_pre_v4_cost_logs(tmp_path):
    campaign = tmp_path / "old"
    campaign.mkdir()
    (campaign / "cost_log_run_20260719_150513_b2c108.json").write_text(
        json.dumps(
            {
                "campaign_id": "old",
                "client_id": "acme",
                "run_id": "run_20260719_150513_b2c108",
                "phase": "stage_enrich",
                "window_utc": {"start": "2026-07-19T15:05:12+00:00", "end": "2026-07-19T15:24:30+00:00"},
                "leads_enriched": 50,
                "breakdown": {
                    "apify_usd": 1.632,
                    "apollo_usd": 1.96,
                    "firecrawl_usd": 2.5,
                    "llm_usd": 0.6,
                    "total_usd": 6.692,
                },
            }
        ),
        encoding="utf-8",
    )

    rollup = legacy_campaign_rollup(campaign)
    assert rollup["client_id"] == "acme"
    assert rollup["usd_total"] == 6.692
    assert rollup["cost_basis"] == "legacy_cost_log"
    assert rollup["by_source"]["apify"]["usd"] == 1.632
    # Only Apify came from a vendor API; the rest were modelled from log counts.
    assert rollup["by_source"]["apify"]["is_est"] is False
    assert rollup["by_source"]["llm"]["is_est"] is True
    assert len(rollup["by_run"]) == 1
    assert rollup["by_run"][0]["run_date"] == "2026-07-19"


def test_legacy_rollup_returns_none_without_logs(tmp_path):
    assert legacy_campaign_rollup(tmp_path) is None


def test_render_embeds_data_without_breaking_the_script_tag():
    index = {
        "schema_version": "4",
        "logged_at": "2026-08-30T00:00:00+00:00",
        "campaigns": [{"campaign_id": "x", "note": "</script><script>alert(1)</script>"}],
        "runs": [],
        "by_client": {},
        "totals": {"usd_total": 0},
    }
    body = render_body(index)
    assert "</script><script>alert(1)" not in body
    assert "ledger-data" in body

    doc = render_document(index)
    assert doc.startswith("<!DOCTYPE html>")
    # Content belongs in <body>, not smuggled into <head>.
    assert doc.index('id="ledger-data"') > doc.index("<body>")
    assert "\\u003c/script\\u003e" in doc or "\\u003c" in doc


def test_batch_files_count_but_merged_stages_use_canonical(tmp_path):
    """Per-batch intermediates must not be summed on top of the merged output."""
    campaign = tmp_path / "batched"
    stages = campaign / "stages"
    company = "website,company_name"
    person = "lead_id,decision_maker_email"

    # Company stages genuinely accumulate across batches.
    _write_csv(stages / "01_raw_seeds.csv", company, ["a.com,A", "b.com,B"])
    _write_csv(stages / "01_raw_seeds_batch3.csv", company, ["c.com,C"])
    # ...but a one-off slice is not a batch and must be ignored.
    _write_csv(stages / "01_raw_seeds_product.csv", company, ["z.com,Z"])

    # Person stages are merged back into the canonical file.
    _write_csv(stages / "04_apollo_contactable.csv", person, ["1,a@x.com", "2,b@x.com"])
    _write_csv(stages / "04_apollo_contactable_batch3.csv", person, ["3,c@x.com"])

    counts = campaign_lead_counts(campaign)
    assert counts["seeds"] == 3
    assert counts["contactable"] == 2


def test_render_document_uses_master_template():
    html = render_document(
        {
            "schema_version": "4",
            "logged_at": "2026-01-01T00:00:00Z",
            "scope": {"kind": "all", "id": None},
            "campaigns": [],
            "runs": [],
            "by_client": {},
            "totals": {
                "usd_total": 0,
                "campaign_count": 0,
                "tracked_campaign_count": 0,
                "run_count": 0,
                "client_count": 0,
                "delivered_leads": 0,
            },
        }
    )
    assert "Lead List Ledger" in html
    assert "ledger-data" in html
    assert "Build new campaign" in html
    assert "--accent" in html  # CSS inlined for offline
    assert "/static/portal.css" not in html
