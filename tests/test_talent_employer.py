"""Talent employer company selection + optional enrich module."""
from __future__ import annotations

import csv
from pathlib import Path

from agent.utils.talent_employer import (
    enrich_contactable_employer_company,
    select_campaign_employer,
)
from agent.utils.talent_people import STAGE_T05_APOLLO_CONTACTABLE, talent_stage_path


def test_select_prefers_icp_relevant_company():
    row = {
        "company_name": "Side Hustle Consulting",
        "current_position": "Advisor @ Side Hustle Consulting",
        "experience_summary": (
            "Property Manager · Costa Homes Realty · Present; "
            "Advisor · Side Hustle Consulting · Present; "
            "Intern · Old Co · 2018-2019"
        ),
        "title": "Property Manager",
    }
    icp = {
        "include_keywords": ["property management", "property manager", "real estate"],
        "job_titles": ["Property Manager"],
        "exclude_keywords": ["recruitment"],
    }
    picked = select_campaign_employer(row, icp)
    assert picked is not None
    assert "Costa Homes" in picked["name"]
    assert picked["score"] > 0
    assert picked["candidate_count"] >= 2


def test_select_single_employer_fallback():
    row = {
        "company_name": "Only Co",
        "current_position": "Owner @ Only Co",
        "experience_summary": "",
    }
    picked = select_campaign_employer(row, {})
    assert picked is not None
    assert picked["name"] == "Only Co"
    assert picked["reason"] == "single_employer"


def test_enrich_contactable_employer_company(tmp_path: Path):
    camp = tmp_path / "camp"
    stages = camp / "stages"
    stages.mkdir(parents=True)
    (camp / "icp.json").write_text(
        '{"include_keywords":["property","real estate"],"job_titles":["Property Manager"]}',
        encoding="utf-8",
    )
    t05 = talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE)
    rows = [
        {
            "person_id": "p1",
            "full_name": "Ana",
            "email": "a@x.com",
            "company_name": "Gym Spot",
            "current_position": "Trainer @ Gym Spot",
            "experience_summary": (
                "Property Manager · Marbella Estates · Present; "
                "Trainer · Gym Spot · Present"
            ),
        }
    ]
    with t05.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    calls: list[str] = []

    def fake_enrich(name, website=None, company_linkedin_url=None, fetch_open_jobs=False):
        calls.append(name)
        return {
            "linkedin_url": "https://www.linkedin.com/company/marbella-estates",
            "description": "Holiday rental management on the Costa del Sol.",
            "industry": "Real Estate",
            "employee_count": 42,
        }

    enriched, skipped = enrich_contactable_employer_company(
        camp, enrich_company=fake_enrich, only_missing=True
    )
    assert enriched == 1
    assert skipped == 0
    assert calls == ["Marbella Estates"]

    with t05.open(encoding="utf-8", newline="") as fh:
        out = list(csv.DictReader(fh))
    assert out[0]["company_name"] == "Marbella Estates"
    assert out[0]["company_industry"] == "Real Estate"
    assert "Holiday rental" in out[0]["company_linkedin_description"]
    assert out[0]["company_size"] == "42"
