"""Local brief → ICP heuristics (no paid APIs)."""
from __future__ import annotations

from agent.utils.brief_icp import merge_brief_into_icp, parse_brief_to_icp


def test_user_marketing_brief_example():
    brief = (
        "COOs, CTOs, marketing directors of marketing companies "
        "in USA between 1 and 50 employees."
    )
    icp = parse_brief_to_icp(brief, campaign_id="demo", campaign_type="company_outreach")
    assert "COO" in icp["job_titles"]
    assert "CTO" in icp["job_titles"]
    assert "Marketing Director" in icp["job_titles"]
    assert icp["contact_locations"] == ["United States"]
    assert icp["company_size_min"] == 1
    assert icp["company_size_max"] == 50
    assert any("marketing" in k.lower() for k in icp["industries"])
    assert icp["include_keywords"] == []


def test_empty_brief_returns_empty_lists():
    icp = parse_brief_to_icp("", campaign_id="x")
    assert icp["job_titles"] == []
    assert icp["contact_locations"] == []
    assert icp["company_size_min"] is None
    assert icp["company_size_max"] is None


def test_merge_only_fills_empty():
    existing = {
        "campaign_id": "x",
        "job_titles": ["CEO"],
        "contact_locations": [],
        "company_size_min": None,
        "company_size_max": None,
        "include_keywords": [],
    }
    merged = merge_brief_into_icp(
        existing,
        "CTOs in Spain between 10 and 100 employees",
        campaign_id="x",
        only_fill_empty=True,
    )
    assert merged["job_titles"] == ["CEO"]  # kept
    assert merged["contact_locations"] == ["Spain"]
    assert merged["company_size_min"] == 10
    assert merged["company_size_max"] == 100
