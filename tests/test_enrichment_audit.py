"""Post-run check that enabled enrichment modules actually landed on the rows."""
from __future__ import annotations

from agent.stages.contacts import _apply_company_jobs, _jobs_for_url
from agent.utils.enrichment_audit import audit_enriched_rows


def _row(**kwargs):
    base = {
        "company_name": "Avex",
        "decision_maker_linkedin": "https://www.linkedin.com/in/jsurda",
        "decision_maker_linkedin_headline": "Founder",
        "decision_maker_linkedin_post_summary": "",
        "company_linkedin_description": "Agency",
        "company_is_hiring": "",
        "company_open_jobs_count": "0",
        "company_open_jobs_summary": "",
        "website_summary": "We build stores",
    }
    base.update(kwargs)
    return base


def test_audit_fails_when_posts_and_jobs_are_blank():
    plan = {
        "modules": {
            "linkedin_dm_posts": True,
            "linkedin_company_jobs": True,
            "linkedin_dm_profile": True,
            "website_scrape": True,
        }
    }
    audit = audit_enriched_rows(plan, [_row(), _row(company_name="DTC")])
    by_id = {c["module_id"]: c for c in audit["checks"]}
    assert audit["ok"] is False
    assert by_id["linkedin_dm_posts"]["status"] == "fail"
    assert by_id["linkedin_dm_posts"]["with_evidence"] == 0
    assert by_id["linkedin_company_jobs"]["status"] == "fail"
    assert by_id["linkedin_dm_profile"]["status"] == "ok"
    assert by_id["website_scrape"]["status"] == "ok"
    assert by_id["apollo_phone"]["status"] == "off"
    assert "Decision-maker LinkedIn posts" in audit["summary"]


def test_audit_accepts_confirmed_zero_jobs_and_a_post_summary():
    plan = {"modules": {"linkedin_dm_posts": True, "linkedin_company_jobs": True}}
    rows = [
        _row(
            decision_maker_linkedin_post_summary="Shipped a new landing page",
            company_is_hiring="no",
        )
    ]
    audit = audit_enriched_rows(plan, rows)
    by_id = {c["module_id"]: c for c in audit["checks"]}
    assert by_id["linkedin_dm_posts"]["status"] == "ok"
    assert by_id["linkedin_company_jobs"]["status"] == "ok"
    assert audit["ok"] is True


def test_jobs_lookup_matches_http_and_https_and_writes_a_zero():
    jobs_by_url = {
        "http://www.linkedin.com/company/avexdesigns": {
            "is_hiring": "no",
            "open_jobs_count": 0,
            "job_titles": [],
            "summary": None,
        }
    }
    found = _jobs_for_url(
        jobs_by_url, "https://linkedin.com/company/avexdesigns/"
    )
    data = {"is_hiring": None, "open_jobs_count": 0, "open_jobs_summary": None}
    _apply_company_jobs(data, found)
    assert data["is_hiring"] == "no"
    assert data["open_jobs_count"] == 0


def test_jobs_lookup_keeps_titles_when_the_url_scheme_differs():
    jobs_by_url = {
        "https://www.linkedin.com/company/seedcms": {
            "is_hiring": "yes",
            "open_jobs_count": 2,
            "job_titles": ["Designer", "AE"],
            "summary": "Designer; AE",
        }
    }
    found = _jobs_for_url(jobs_by_url, "http://linkedin.com/company/seedcms")
    data = {}
    _apply_company_jobs(data, found)
    assert data["is_hiring"] == "yes"
    assert data["open_jobs_summary"] == "Designer; AE"
