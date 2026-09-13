"""Outreach page selection, social links, blog CSV, DM mentions."""
from __future__ import annotations

from agent.models import BlogPost, WebsiteAnalysis
from agent.utils.website_outreach import (
    extract_decision_maker_mentions,
    extract_social_links,
    format_blog_posts,
    parse_blog_posts,
    select_outreach_urls,
    website_analysis_from_csv,
    website_analysis_to_csv,
)


def test_select_outreach_urls_priority_order():
    mapped = [
        "https://acme.com/contact",
        "https://acme.com/about",
        "https://acme.com/blog/2024/01/old-post",
        "https://acme.com/blog/2026/08/new-post",
        "https://acme.com/services",
        "https://acme.com/blog",
        "https://acme.com/careers",
        "https://acme.com/news",
        "https://acme.com/team",
    ]
    plan = select_outreach_urls(mapped, "https://acme.com", max_pages=8)
    assert plan.urls[0].rstrip("/") == "https://acme.com"
    joined = " ".join(plan.urls)
    assert "/careers" in joined
    assert plan.urls.index("https://acme.com/careers") < plan.urls.index("https://acme.com/blog")
    assert plan.urls.index("https://acme.com/blog") < plan.urls.index("https://acme.com/about")
    assert "https://acme.com/blog/2026/08/new-post" in plan.urls
    assert plan.urls.index("https://acme.com/blog/2026/08/new-post") < plan.urls.index(
        "https://acme.com/blog/2024/01/old-post"
    )
    assert plan.blog_url == "https://acme.com/blog"
    assert plan.news_url == "https://acme.com/news"
    assert plan.careers_url == "https://acme.com/careers"
    assert "https://acme.com/contact" not in plan.urls


def test_select_skips_job_postings_and_blog_pagination():
    mapped = [
        "https://acme.com/careers/senior-engineer",
        "https://acme.com/careers",
        "https://acme.com/blog/page/2",
        "https://acme.com/blog/how-we-hire",
    ]
    plan = select_outreach_urls(mapped, "https://acme.com", max_pages=6)
    assert "https://acme.com/careers" in plan.urls
    assert "https://acme.com/careers/senior-engineer" not in plan.urls
    assert "https://acme.com/blog/page/2" not in plan.urls
    assert "https://acme.com/blog/how-we-hire" in plan.urls


def test_extract_social_links_skips_share_buttons():
    md = """
    Footer:
    https://facebook.com/sharer/sharer.php?u=x
    https://www.facebook.com/acmecorp
    https://twitter.com/intent/tweet
    https://x.com/acme_jobs
    https://instagram.com/p/abc123
    https://www.instagram.com/acme.official
    https://www.tiktok.com/@acmehq
    """
    links = extract_social_links(md)
    assert links["facebook"] == "https://www.facebook.com/acmecorp"
    assert links["x"] == "https://x.com/acme_jobs"
    assert links["instagram"] == "https://www.instagram.com/acme.official"
    assert links["tiktok"] == "https://www.tiktok.com/@acmehq"


def test_extract_decision_maker_mentions_full_name():
    md = (
        "Our team. Jane Doe is VP Engineering and previously led R&D in Austin. "
        "Contact sales for a demo. Jane Doe joined in 2019."
    )
    mentions = extract_decision_maker_mentions(md, "Jane Doe")
    assert len(mentions) == 2
    assert all("Jane Doe" in m for m in mentions)


def test_blog_posts_csv_roundtrip():
    posts = [
        BlogPost(
            title="How we hire in Spain",
            published_at="2026-08-12",
            description="A look at our Malaga hub.",
            url="https://acme.com/blog/hire-spain",
        ),
        BlogPost(title="Q2 update", published_at="2026-07-01"),
    ]
    encoded = format_blog_posts(posts)
    parsed = parse_blog_posts(encoded)
    assert parsed[0].title == "How we hire in Spain"
    assert parsed[0].published_at == "2026-08-12"
    assert "Malaga" in (parsed[0].description or "")
    assert parsed[0].url.endswith("/hire-spain")
    assert parsed[1].title == "Q2 update"


def test_website_analysis_csv_drops_legacy_fields():
    wa = WebsiteAnalysis(
        services_offered=["R&D staff"],
        about_summary="We build custom machines.",
        has_blog=True,
        blog_url="https://acme.com/blog",
        social_x="https://x.com/acme",
        website_open_roles=["Mechanical engineer"],
        website_is_hiring="yes",
        website_careers_url="https://acme.com/careers",
        website_summary="Acme builds factory robots.",
        blog_posts=[BlogPost(title="New factory", published_at="2026-08-01")],
    )
    row = website_analysis_to_csv(wa)
    assert "tech_stack" not in row
    assert "has_chatbot" not in row
    assert "social_proof" not in row
    assert "last_blog_post" not in row
    assert row["social_x"] == "https://x.com/acme"
    assert "New factory" in row["blog_posts"]
    roundtrip = website_analysis_from_csv(row)
    assert roundtrip is not None
    assert roundtrip.website_is_hiring == "yes"
    assert roundtrip.blog_posts[0].title == "New factory"
    assert roundtrip.social_x == "https://x.com/acme"


def test_legacy_last_blog_post_column_still_loads():
    wa = website_analysis_from_csv(
        {"website_summary": "Hello", "last_blog_post": "2025-01-15"}
    )
    assert wa is not None
    assert wa.blog_posts[0].published_at == "2025-01-15"
