"""LinkedIn company-search (not jobs) seed URL builders."""
from __future__ import annotations

import json
from pathlib import Path

from agent.utils.linkedin_company_seeds import (
    build_company_search_queries_from_icp,
    build_linkedin_company_search_urls_from_icp,
    can_auto_seed_linkedin_companies_from_icp,
    persist_auto_linkedin_company_seed_sources,
    preview_auto_linkedin_companies_seed,
)
from agent.utils.location_match import location_matches_icp


def test_decoracel_builds_company_search_urls():
    path = Path("data/clients/decoracel/icps/real_estate_espa_a/icp.json")
    if not path.is_file():
        path = Path("data/campaigns/decoracel_real_estate_espa_a/icp.json")
    if not path.is_file():
        return
    icp = json.loads(path.read_text(encoding="utf-8"))
    assert can_auto_seed_linkedin_companies_from_icp(icp)
    urls = build_linkedin_company_search_urls_from_icp(icp)
    assert urls
    assert all("/search/results/companies/" in u for u in urls)
    assert any("inmobiliaria" in u.lower() or "real" in u.lower() for u in urls)
    queries = build_company_search_queries_from_icp(icp)
    assert queries[0]["searchQuery"]
    preview = preview_auto_linkedin_companies_seed(icp)
    assert preview["ok"] is True
    assert preview["mode"] == "auto_linkedin_companies"


def test_persist_company_sources_disables_jobs(tmp_path: Path):
    icp = {
        "include_keywords": ["inmobiliaria"],
        "contact_locations": ["Spain"],
        "geo": {"primary_location": "Spain", "segments": ["Málaga"]},
    }
    (tmp_path / "seed_sources.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "id": "linkedin_jobs",
                        "type": "linkedin_jobs",
                        "enabled": True,
                        "config": {"urls": ["https://www.linkedin.com/jobs/search/?keywords=x"]},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    entry = persist_auto_linkedin_company_seed_sources(tmp_path, "demo", icp, seed_count=40)
    assert entry["type"] == "linkedin_companies"
    data = json.loads((tmp_path / "seed_sources.json").read_text(encoding="utf-8"))
    jobs = next(s for s in data["sources"] if (s.get("id") or s.get("type")) == "linkedin_jobs")
    assert jobs["enabled"] is False


def test_location_match_spain_mallorca():
    assert location_matches_icp(
        "Greater Palma de Mallorca Metropolitan Area",
        website="https://alfanewtalent.es",
        primary_location="Spain",
        location_hints=["spain", "málaga"],
        contact_locations=["Spain"],
    )
    assert not location_matches_icp(
        "Berlin, Germany",
        website="https://example.de",
        primary_location="Spain",
        location_hints=["spain", "málaga"],
        contact_locations=["Spain"],
    )


def test_location_match_via_es_tld_when_location_empty():
    assert location_matches_icp(
        "",
        website="https://www.homes4u.es",
        primary_location="Spain",
        contact_locations=["Spain"],
    )


def test_thin_icp_cannot_auto_seed():
    assert can_auto_seed_linkedin_companies_from_icp({"job_titles": ["CEO"]}) is False
