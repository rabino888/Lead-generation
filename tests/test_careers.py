"""Careers discovery ranking and +1 portal hop."""
from __future__ import annotations

from agent.integrations.careers import (
    careers_index_needs_portal_hop,
    extract_careers_links,
    pick_careers_portal_hop,
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


def test_pick_hop_skips_current_url():
    md = "Apply at https://acme.com/careers — we're hiring!"
    hop = pick_careers_portal_hop(
        md, "https://acme.com", current_url="https://acme.com/careers"
    )
    assert hop is None
