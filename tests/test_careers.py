"""Careers discovery ranking and +1 portal hop."""
from __future__ import annotations

from agent.integrations.careers import (
    ats_board_identity,
    canonical_ats_board_url,
    careers_index_needs_portal_hop,
    extract_careers_links,
    fetch_ats_open_roles,
    open_roles_from_ats,
    pick_careers_portal_hop,
    preferred_ats_board_url,
    titles_from_ats_payload,
    titles_from_icims_html,
)


def test_extract_prefers_ats_over_marketing():
    md = """
    Join us at [Careers](https://acme.com/careers).
    Or apply via https://jobs.ashbyhq.com/acme
    """
    links = extract_careers_links(md, "https://acme.com")
    assert links[0].startswith("https://jobs.ashbyhq.com/acme")


def test_extract_prefers_yc_jobs_over_hash_careers():
    md = """
    [Careers](https://www.ycombinator.com/companies/silimate/jobs)
    See also https://www.silimate.com/company#careers
    """
    links = extract_careers_links(md, "https://www.silimate.com")
    assert "ycombinator.com" in links[0]


def test_rejects_docs_api_jobs_urls():
    md = """
    API jobs: https://docs.fiddler.ai/sdk-api/rest-api/jobs
    Real board: https://jobs.ashbyhq.com/fiddler
    """
    links = extract_careers_links(md, "https://www.fiddler.ai")
    assert links
    assert all("docs.fiddler" not in u for u in links)
    assert "ashbyhq.com" in links[0]


def test_needs_hop_on_marketing_cta_page():
    md = """
    # Careers at Acme
    We're hiring talented people. [See open roles](https://jobs.ashbyhq.com/acme)
    Join our team and help build the future.
    """
    assert careers_index_needs_portal_hop(md, careers_url="https://acme.com/careers")


def test_no_hop_when_already_on_ats():
    md = "Senior Engineer\nStaff Scientist\nApply for Platform Engineer"
    assert not careers_index_needs_portal_hop(
        md, careers_url="https://jobs.ashbyhq.com/acme"
    )


def test_pick_hop_from_cta_anchor():
    md = """
    # Careers
    We're growing. [See open roles](https://job-boards.greenhouse.io/acme)
    """
    hop = pick_careers_portal_hop(
        md, "https://acme.com", current_url="https://acme.com/careers"
    )
    assert hop == "https://job-boards.greenhouse.io/acme"


def test_pick_hop_from_inline_ats_url():
    md = """
    Open roles live at https://jobs.ashbyhq.com/plenful?utm_source=site
    """
    hop = pick_careers_portal_hop(
        md, "https://plenful.com", current_url="https://plenful.com/careers"
    )
    assert hop and "ashbyhq.com/plenful" in hop


def test_ashby_board_identity_strips_job_id_and_utm():
    ident = ats_board_identity(
        "https://jobs.ashbyhq.com/ivo-inc/5c151210-be10-452a-9ba3-4fc92eb68c2c?utm_source=site"
    )
    assert ident == ("ashby", "ivo-inc")
    assert canonical_ats_board_url(
        "https://jobs.ashbyhq.com/confido?utm_source=EpLvqzJP3X"
    ) == "https://jobs.ashbyhq.com/confido"


def test_greenhouse_board_identity_from_single_posting():
    ident = ats_board_identity(
        "https://job-boards.greenhouse.io/scoutai/jobs/4643324008"
    )
    assert ident == ("greenhouse", "scoutai")


def test_titles_from_ashby_payload_skips_unlisted_and_dupes():
    titles = titles_from_ats_payload(
        "ashby",
        {
            "jobs": [
                {"title": "Senior Software Engineer", "isListed": True},
                {"title": "Secret Role", "isListed": False},
                {"title": "Senior Software Engineer", "isListed": True},
                {"title": "Careers", "isListed": True},
            ]
        },
    )
    assert titles == ["Senior Software Engineer"]


def test_fetch_ats_open_roles_uses_board_api(monkeypatch):
    seen: list[str] = []

    class Resp:
        status_code = 200

        def json(self):
            return {"jobs": [{"title": "Product Engineer", "isListed": True}]}

    def fake_get(url, **kwargs):
        seen.append(url)
        return Resp()

    monkeypatch.setattr("agent.integrations.careers.httpx.get", fake_get)
    got = open_roles_from_ats("https://jobs.ashbyhq.com/Arlo/7e420c5d-e49e-423d-97e4-b763abb3e0cd")
    assert got is not None
    assert got["careers_url"] == "https://jobs.ashbyhq.com/Arlo"
    assert got["open_roles"] == ["Product Engineer"]
    assert seen == ["https://api.ashbyhq.com/posting-api/job-board/Arlo"]


def test_titles_from_icims_job_urls():
    html = """
    <a href="https://careers-lrri.icims.com/jobs/2441/accounts-payable-specialist/job?in_iframe=1">
    <a href="https://careers-lrri.icims.com/jobs/2526/laboratory-animal-sciences-technician-ii/job">
    <a href="https://careers-lrri.icims.com/jobs/search?ss=1">
    """
    assert titles_from_icims_html(html) == [
        "Accounts Payable Specialist",
        "Laboratory Animal Sciences Technician II",
    ]


def test_preferred_ats_board_from_marketing_page():
    md = """
    [Careers](https://cinder.ai/careers)
    [See open roles](https://jobs.ashbyhq.com/cinder)
    """
    assert preferred_ats_board_url(md, "https://cinder.ai") == "https://jobs.ashbyhq.com/cinder"


def test_fetch_ats_open_roles_ignores_docs_url(monkeypatch):
    def fake_get(url, **kwargs):
        raise AssertionError("should not fetch")

    monkeypatch.setattr("agent.integrations.careers.httpx.get", fake_get)
    assert fetch_ats_open_roles("https://docs.fiddler.ai/sdk-api/rest-api/jobs") == []


def test_pick_hop_skips_current_url():
    md = "Apply at https://acme.com/careers — we're hiring!"
    hop = pick_careers_portal_hop(
        md, "https://acme.com", current_url="https://acme.com/careers"
    )
    assert hop is None
