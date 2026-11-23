"""Meta ads summary and ICP module overrides."""
from agent.utils.enrichment_plan import apply_icp_module_overrides, default_plan
from agent.utils.meta_ads import summarize_meta_ads
from agent.utils.website_outreach import website_analysis_from_csv, website_analysis_to_csv
from agent.models import WebsiteAnalysis


def test_summarize_meta_ads_yes():
    summary = summarize_meta_ads(
        {
            "output": {
                "count": 2,
                "ads": [
                    {"body": "Shop the new serum", "title": "20% off"},
                    {"ad_creative_bodies": ["Free shipping over $50"]},
                ],
            }
        }
    )
    assert summary is not None
    assert summary["meta_ads_active"] == "yes"
    assert summary["meta_ads_count"] == 2
    assert "Shop the new serum" in summary["meta_ads_summary"]
    assert "Free shipping over $50" in summary["meta_ads_summary"]


def test_summarize_meta_ads_no():
    summary = summarize_meta_ads({"output": {"ads": [], "count": 0}})
    assert summary == {
        "meta_ads_active": "no",
        "meta_ads_count": 0,
        "meta_ads_summary": "",
    }


def test_summarize_meta_ads_missing_shape():
    assert summarize_meta_ads({"raw": {}}) is None
    summary = summarize_meta_ads({"output": {"page_name": "Harbor"}})
    assert summary is not None
    assert summary["meta_ads_active"] == "no"
    assert summary["meta_ads_count"] == 0


def test_ads_fields_round_trip_csv():
    wa = WebsiteAnalysis(meta_ads_active="yes", meta_ads_count=3, meta_ads_summary="Serum drop")
    row = website_analysis_to_csv(wa)
    assert row["meta_ads_active"] == "yes"
    assert row["meta_ads_count"] == "3"
    loaded = website_analysis_from_csv(row)
    assert loaded is not None
    assert loaded.meta_ads_active == "yes"
    assert loaded.meta_ads_count == 3
    assert loaded.meta_ads_summary == "Serum drop"


def test_icp_turns_instagram_and_ads_on():
    plan = default_plan("demo", campaign_type="company_outreach")
    assert plan["modules"]["meta_ads"] is False
    assert plan["modules"]["instagram_company_posts"] is False
    apply_icp_module_overrides(
        plan,
        {"enrichment_modules": {"instagram_company_posts": True, "meta_ads": True, "not_a_module": True}},
    )
    assert plan["modules"]["instagram_company_posts"] is True
    assert plan["modules"]["meta_ads"] is True
    assert "not_a_module" not in plan["modules"]
