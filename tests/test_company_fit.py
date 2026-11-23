"""ICP fit: ecommerce brand lists reject agencies; staffing lists keep them."""
from __future__ import annotations

from agent.utils.icp_match import industry_matches_allowed, match_company
from agent.utils.icp_rules import IcpRules
from agent.utils.list_qa import review_rows
from agent.utils.raw_replies import bind_raw_replies, clear_raw_replies, store_raw_reply, unbind_raw_replies


def _ecom_rules() -> IcpRules:
    return IcpRules(
        industries=["retail", "apparel & fashion", "consumer goods"],
        excluded_industries=["marketing and advertising", "management consulting", "internet"],
        require_industry_match=True,
        company_size_min=10,
        company_size_max=200,
    )


def _staffing_rules() -> IcpRules:
    return IcpRules(
        industries=["staffing and recruiting", "human resources", "marketing and advertising"],
        excluded_industries=["retail", "consumer goods", "management consulting"],
        require_industry_match=True,
        company_size_min=1,
        company_size_max=200,
    )


def test_advertising_services_fails_ecommerce_icp():
    ok, reasons = match_company(
        {
            "company_name": "Avex E-commerce",
            "website": "https://avex.example",
            "industry": "Advertising Services",
        },
        _ecom_rules(),
    )
    assert ok is False
    assert reasons[0].startswith("excluded_industry:")


def test_agency_name_fails_ecommerce_icp_without_industry():
    ok, reasons = match_company(
        {
            "company_name": "DTC SEO Agency",
            "website": "https://dtcseo.example",
            "industry": "",
        },
        _ecom_rules(),
    )
    assert ok is False
    assert reasons == ["service_provider"]


def test_shopify_partner_fails_ecommerce_icp():
    ok, reasons = match_company(
        {
            "company_name": "SeedCMS Shopify Platinum Partner",
            "website": "https://seedcms.example",
            "industry": "Retail",
        },
        _ecom_rules(),
    )
    assert ok is False
    assert reasons == ["service_provider"]


def test_publisher_fails_ecommerce_allow_list():
    ok, reasons = match_company(
        {
            "company_name": "Practical Ecommerce",
            "website": "https://practicalecommerce.example",
            "industry": "Book and Periodical Publishing",
        },
        _ecom_rules(),
    )
    assert ok is False
    assert reasons == ["industry_mismatch"]


def test_personal_care_manufacturing_matches_without_generic_manufacturing():
    allowed = [
        "retail",
        "apparel & fashion",
        "cosmetics",
        "food & beverages",
        "health, wellness and fitness",
        "personal care product manufacturing",
    ]
    assert industry_matches_allowed("Personal Care Product Manufacturing", allowed)
    assert industry_matches_allowed("Retail Apparel and Fashion", allowed)
    assert industry_matches_allowed("Food and Beverage Manufacturing", allowed)
    assert industry_matches_allowed("Wellness and Fitness Services", allowed)
    assert not industry_matches_allowed("Automation Machinery Manufacturing", allowed)
    assert not industry_matches_allowed("Manufacturing", allowed)


def test_retail_store_passes_ecommerce_icp():
    ok, reasons = match_company(
        {
            "company_name": "Harbor Outfitters",
            "website": "https://harbor.example",
            "industry": "Retail Apparel and Fashion",
        },
        _ecom_rules(),
    )
    assert ok is True
    assert reasons == [] or reasons == ["icp_match"] or "service_provider" not in reasons


def test_advertising_services_passes_staffing_icp():
    ok, reasons = match_company(
        {
            "company_name": "Brightline Media",
            "website": "https://brightline.example",
            "industry": "Advertising Services",
        },
        _staffing_rules(),
    )
    assert ok is True, reasons


def test_agency_name_passes_staffing_icp():
    ok, reasons = match_company(
        {
            "company_name": "Northline Agency",
            "website": "https://northline.example",
            "industry": "Marketing and Advertising",
        },
        _staffing_rules(),
    )
    assert ok is True, reasons


def test_retail_fails_staffing_icp():
    ok, reasons = match_company(
        {
            "company_name": "Harbor Outfitters",
            "website": "https://harbor.example",
            "industry": "Retail",
        },
        _staffing_rules(),
    )
    assert ok is False
    assert reasons[0].startswith("excluded_industry:")


def test_review_flags_agencies_and_missing_instagram():
    report = review_rows(
        [
            {
                "company_name": "DTC SEO Agency",
                "website": "https://dtcseo.example",
                "industry": "Marketing Services",
                "social_instagram": "",
            }
        ],
        _ecom_rules(),
        instagram_on=True,
    )
    assert report["ok"] is False
    assert report["failed"] == 1
    assert "Instagram was requested" in report["summary"]
    assert report["companies"][0]["instagram_url"] == ""


def test_raw_replies_kept_until_cleared(tmp_path):
    tokens = bind_raw_replies(tmp_path, "run_1")
    try:
        stored = store_raw_reply("harvestapi.linkedin.user.posts", {"output": {"elements": []}}, key="abc")
    finally:
        unbind_raw_replies(tokens)
    assert stored is not None and stored.is_file()
    assert "elements" in stored.read_text(encoding="utf-8")
    assert clear_raw_replies(tmp_path) == 1
    assert not (tmp_path / "raw_replies").exists()
