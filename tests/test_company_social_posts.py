"""Company social posts helpers — soft-fail when URLs missing."""
from __future__ import annotations

from agent.models import WebsiteAnalysis
from agent.utils.company_social_posts import (
    enrich_website_social_posts,
    instagram_handle_from_url,
)


def test_instagram_handle_from_url():
    assert instagram_handle_from_url("https://www.instagram.com/nike/") == "nike"
    assert instagram_handle_from_url("https://instagram.com/p/abc") == ""


def test_enrich_skips_without_urls():
    wa = WebsiteAnalysis(website_summary="x")
    out = enrich_website_social_posts(wa, facebook=True, instagram=True)
    assert out.facebook_post_summary is None
    assert out.instagram_post_summary is None


def test_legacy_module_alias_loads():
    from agent.utils.enrichment_plan import normalize_plan

    plan = normalize_plan(
        {
            "campaign_type": "company_outreach",
            "modules": {"apify_company_jobs": True, "apify_dm_posts": False},
            "target_leads": 5,
        },
        campaign_id="alias",
    )
    assert plan["modules"]["linkedin_company_jobs"] is True
    assert plan["modules"]["linkedin_dm_posts"] is False
    assert "facebook_company_posts" in plan["modules"]
