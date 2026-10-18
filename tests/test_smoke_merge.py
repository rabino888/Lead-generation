"""Smoke contactable leads must survive into the full-batch delivery."""
from __future__ import annotations

from agent.utils.list_run import merge_contactable_rows


def test_merge_appends_smoke_leads_missing_from_full_batch():
    full = [{"company_name": "Insure123", "decision_maker_email": "zain@insure123.com", "website": "https://www.insure123.com"}]
    smoke = [
        {"company_name": "Kraft Lake", "decision_maker_email": "cdaniel@farmersagent.com", "website": "https://www.farmers.com"},
        {"company_name": "Insure123", "decision_maker_email": "zain@insure123.com", "website": "https://www.insure123.com"},
    ]
    merged = merge_contactable_rows(full, smoke)
    emails = [row["decision_maker_email"] for row in merged]
    assert emails == ["zain@insure123.com", "cdaniel@farmersagent.com"]


def test_merge_skips_smoke_rows_without_email():
    merged = merge_contactable_rows(
        [],
        [{"company_name": "Empty", "decision_maker_email": "", "website": "https://example.com"}],
    )
    assert merged == []
