"""Firmographic merge helpers."""
from __future__ import annotations

from agent.utils.firmographics import merge_firmographics_into_rows


def test_merge_prefers_enriched_website():
    rows = [
        {
            "company_name": "Lucas Fox",
            "website": "https://www.lucasfox.com",
            "industry": "",
            "company_size": "",
            "notes": "LinkedIn jobs scrape",
        }
    ]
    enrichments = [
        {
            "name": "Lucas Fox",
            "website": "https://www.lucasfox.es",
            "domain": "lucasfox.es",
            "industry": "Real Estate",
            "employees": 200,
        }
    ]
    out = merge_firmographics_into_rows(rows, enrichments, prefer_enriched_website=True)
    assert out[0]["website"] == "https://www.lucasfox.es"
    assert out[0]["industry"] == "Real Estate"
    assert "firmographics:foxlabs" in out[0]["notes"]
