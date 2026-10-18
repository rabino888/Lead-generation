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


def test_staffing_industry_allows_recruiter_names_without_flag():
    rules = IcpRules(
        industries=["staffing and recruiting"],
        company_size_min=1,
        company_size_max=200,
        require_location_match=False,
    )
    assert rules.allow_recruiter_companies is None
    ok, reasons = match_company(
        {
            "company_name": "Scion Staffing",
            "website": "https://www.scionstaffing.com",
            "industry": "Staffing and Recruiting",
            "location": "San Francisco, CA, United States",
            "company_size": "46",
        },
        rules,
    )
    assert ok is True, reasons


def test_explicit_false_keeps_recruiter_gate_on_staffing_icp():
    rules = IcpRules(
        industries=["staffing and recruiting"],
        allow_recruiter_companies=False,
        company_size_min=1,
        company_size_max=200,
        require_location_match=False,
    )
    ok, reasons = match_company(
        {
            "company_name": "Scion Staffing",
            "website": "https://www.scionstaffing.com",
            "industry": "Staffing and Recruiting",
            "company_size": "46",
        },
        rules,
    )
    assert ok is False
    assert "recruiter_or_staffing_name" in reasons


def test_small_boutique_passes_spam_check():
    rules = IcpRules(
        industries=["staffing and recruiting"],
        company_size_min=1,
        company_size_max=200,
        require_location_match=False,
    )
    ok, reasons = match_company(
        {
            "company_name": "ACUITY Talent Acquisition",
            "website": "https://www.acuitytalent.com",
            "industry": "Staffing and Recruiting",
            "company_size": "2",
        },
        rules,
    )
    assert ok is True, reasons


def test_small_edu_institute_rejected_as_spam():
    rules = IcpRules(
        industries=["staffing and recruiting"],
        company_size_min=1,
        company_size_max=200,
        require_location_match=False,
    )
    ok, reasons = match_company(
        {
            "company_name": "Talent Acquisition Institute",
            "website": "https://www.talentacquisitioninstitute.org",
            "industry": "Higher Education",
            "company_size": "1",
        },
        rules,
    )
    assert ok is False
    assert any(r.startswith("small_company_spam:") for r in reasons)


def test_small_biz_tld_rejected_as_spam():
    rules = IcpRules(
        industries=["staffing and recruiting"],
        company_size_min=1,
        company_size_max=200,
        require_location_match=False,
        excluded_industries=[],
    )
    ok, reasons = match_company(
        {
            "company_name": "Genius Hub Workforce and Recruitment Agency",
            "website": "https://www.geniushub.biz",
            "industry": "Staffing and Recruiting",
            "company_size": "2",
        },
        rules,
    )
    assert ok is False
    assert any("spam_tld" in r for r in reasons)


def test_staffing_icp_allows_recruiter_names():
    rules = IcpRules(
        allow_recruiter_companies=True,
        excluded_name_patterns=r"linkedin\b|robert half|randstad",
        excluded_industries=["computer software"],
        company_size_min=10,
        company_size_max=200,
        require_location_match=False,
    )
    ok, reasons = match_company(
        {
            "company_name": "Scion Staffing",
            "website": "https://www.scionstaffing.com",
            "industry": "Staffing and Recruiting",
            "notes": "LinkedIn company search",
            "source_url": "https://www.linkedin.com/company/scion-staffing",
            "location": "San Francisco, CA, United States",
            "company_size": "46",
        },
        rules,
    )
    assert ok is True, reasons


def test_linkedin_source_url_does_not_trip_name_pattern():
    rules = _rules()
    rules.excluded_name_patterns = r"linkedin\b"
    rules.excluded_industries = []
    ok, reasons = match_company(
        {
            "company_name": "Northline Marketing",
            "website": "https://northline.example",
            "industry": "Marketing",
            "notes": "LinkedIn company search",
            "source_url": "https://www.linkedin.com/company/northline",
            "location": "Spain",
            "company_size": "20",
        },
        rules,
    )
    assert ok is True, reasons


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
