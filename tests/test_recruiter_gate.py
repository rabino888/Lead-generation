"""Recruiter / staffing name gate at ICP match."""
from __future__ import annotations

from agent.utils.icp_match import RECRUITER_NAME_RE, match_company
from agent.utils.icp_rules import IcpRules


def _rules() -> IcpRules:
    return IcpRules(
        excluded_keywords=[],
        excluded_industries=["staffing and recruiting"],
        excluded_name_patterns="",
        industries=["real estate"],
        job_titles=["CEO"],
        contact_locations=["Spain"],
        location="Spain",
        location_hints=["spain", "málaga", "marbella"],
        company_size_min=1,
        company_size_max=50,
        require_location_match=True,
    )


def test_recruiter_name_rejected():
    assert RECRUITER_NAME_RE.search("Alfa New Talent")
    ok, reasons = match_company(
        {
            "company_name": "Alfa New Talent",
            "website": "https://alfanewtalent.com",
            "industry": "Real Estate",
            "notes": "hiring: Agente inmobiliario",
            "location": "Spain",
        },
        _rules(),
    )
    assert ok is False
    assert "recruiter_or_staffing_name" in reasons


def test_real_estate_agency_still_passes_name_gate():
    assert not RECRUITER_NAME_RE.search("Drumelia Real Estate")
    ok, _ = match_company(
        {
            "company_name": "Drumelia Real Estate",
            "website": "https://www.drumelia.com",
            "industry": "real estate",
            "notes": "",
            "location": "Marbella",
            "company_size": "20",
        },
        _rules(),
    )
    assert ok is True
