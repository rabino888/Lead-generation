"""Homepage liveness and site-identity checks before Apollo."""
from __future__ import annotations

from agent.utils.website_seed_check import (
    HomepageSnapshot,
    apply_website_seed_gate,
    homepage_problem,
)


def _snap(status: int, html: str, final_url: str) -> HomepageSnapshot:
    return HomepageSnapshot(status=status, final_url=final_url, html=html)


def test_shopify_unavailable_is_rejected():
    html = "<html><body><h1>Sorry, this store is currently unavailable</h1></body></html>"
    reason = homepage_problem(
        "Liquid Oxygen",
        "https://liquidoxygen.com",
        _snap(200, html, "https://liquidoxygen.com"),
    )
    assert reason == "site_shopify_unavailable"


def test_password_protected_shopify_stays():
    html = "<html><body><h1>This store is password protected</h1> Enter using password</body></html>"
    reason = homepage_problem(
        "Grande Cosmetics",
        "https://grandecosmetics.com",
        _snap(200, html, "https://grandecosmetics.com"),
    )
    assert reason is None


def test_closed_business_is_rejected():
    html = (
        "<p>After more than 75 years in the business, Exacta Garment "
        "had to close in November 2022.</p>"
    )
    reason = homepage_problem(
        "Exacta Garment",
        "https://www.exactagarment.net",
        _snap(200, html, "https://www.exactagarment.net"),
    )
    assert reason == "site_closed"


def test_closed_door_seminar_stays():
    html = "<p>A closed-door seminar on frontier technology and the future of beauty.</p>"
    reason = homepage_problem(
        "Nanomed Skincare",
        "https://www.nanomed-skincare.com",
        _snap(200, html, "https://www.nanomed-skincare.com"),
    )
    assert reason is None


def test_http_429_and_403_stay():
    for status in (429, 403, 401):
        reason = homepage_problem(
            "Kat Burki",
            "https://katburki.com",
            HomepageSnapshot(status=status, final_url="https://katburki.com", html="Too many requests"),
        )
        assert reason is None


def test_http_522_is_rejected():
    reason = homepage_problem(
        "Whip Hand Cosmetics",
        "https://whiphandcosmetics.com",
        HomepageSnapshot(status=522, final_url="https://whiphandcosmetics.com", html=""),
    )
    assert reason == "site_http_522"


def test_jobs_platform_is_the_wrong_site():
    reason = homepage_problem(
        "Farmacy Beauty",
        "https://apply.workable.com",
        _snap(200, "<title>Workable</title><p>Jobs</p>", "https://apply.workable.com/some-co"),
    )
    assert reason == "wrong_site:platform:apply.workable.com"


def test_social_platform_is_the_wrong_site():
    reason = homepage_problem(
        "The House of Asmara Dior",
        "https://nextdoor.com",
        _snap(200, "<title>Nextdoor</title>", "https://nextdoor.com"),
    )
    assert reason == "wrong_site:platform:nextdoor.com"


def test_platform_named_in_the_company_stays():
    reason = homepage_problem(
        "Nextdoor",
        "https://nextdoor.com",
        _snap(200, "<title>Nextdoor</title>", "https://nextdoor.com"),
    )
    assert reason is None


def test_parent_store_stays():
    html = "<title>REVOLVE: Women's Clothing and Designer Apparel</title><p>Shop new arrivals</p>"
    reason = homepage_problem(
        "Alliance Apparel",
        "https://www.revolve.com",
        _snap(200, html, "https://www.revolve.com"),
    )
    assert reason is None


def test_hosting_reseller_is_the_wrong_business():
    html = (
        "<title>Solus Host</title><p>Domain registration, web hosting, "
        "and cloud servers for resellers.</p>"
    )
    reason = homepage_problem(
        "SAV APPAREL & TEXTILES",
        "https://savsourcing.com",
        _snap(200, html, "https://savsourcing.com"),
        industry="Textile Manufacturing",
    )
    assert reason == "wrong_site:different_business"


def test_parked_domain_is_rejected():
    html = "<html><body>This domain is for sale. Buy this domain today.</body></html>"
    reason = homepage_problem(
        "Rousso",
        "https://roussony.com",
        _snap(200, html, "https://roussony.com"),
    )
    assert reason == "site_parked"


def test_unreachable_is_rejected_and_total_outage_keeps_the_batch():
    def down(_url: str) -> HomepageSnapshot:
        return HomepageSnapshot(status=None, final_url="", error="http")

    rows = [
        {"company_name": "One", "website": "https://one.example"},
        {"company_name": "Two", "website": "https://two.example"},
    ]
    kept, rejected = apply_website_seed_gate([], [], fetcher=down)
    assert kept == [] and rejected == []

    kept, rejected = apply_website_seed_gate(rows, [], fetcher=down)
    assert kept == rows or [dict(r) for r in kept] == [dict(r) for r in rows]
    assert rejected == []

    def mixed(url: str) -> HomepageSnapshot:
        if "dead" in url:
            return HomepageSnapshot(status=None, final_url=url, error="http")
        return HomepageSnapshot(
            status=200,
            final_url=url,
            html="<title>Live Brand cosmetics</title><p>Shop</p>",
        )

    mixed_rows = [
        {"company_name": "Dead Co", "website": "https://dead.example"},
        {"company_name": "Live Brand", "website": "https://live.example"},
    ]
    kept, rejected = apply_website_seed_gate(mixed_rows, [], fetcher=mixed)
    assert [r["company_name"] for r in kept] == ["Live Brand"]
    assert rejected[0]["pre_apollo_reason"] == "site_unreachable"
