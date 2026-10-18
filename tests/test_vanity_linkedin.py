"""Vanity LinkedIn resolve: opaque ACw → /in/slug via SERP (mocked)."""
from __future__ import annotations

from unittest.mock import patch

from agent.utils.vanity_linkedin import (
    is_opaque_linkedin_member_url,
    resolve_row_linkedin_vanity,
)


def test_opaque_detection():
    assert is_opaque_linkedin_member_url(
        "https://www.linkedin.com/in/ACwAAATQlu4BYwa8fakHDYchobWcVsHjNMhA8h4"
    )
    assert not is_opaque_linkedin_member_url(
        "https://www.linkedin.com/in/sara-mandirola-12607723"
    )


def test_already_vanity_skips_paid_call():
    row = {
        "full_name": "Sara Mandirola",
        "linkedin_url": "https://www.linkedin.com/in/sara-mandirola-12607723",
        "company_name": "LM Group",
    }
    with patch("agent.utils.vanity_linkedin.resolve_vanity_via_serp") as serp:
        out = resolve_row_linkedin_vanity(row)
    serp.assert_not_called()
    assert out["vanity_resolve_status"] == "already_vanity"
    assert "sara-mandirola" in out["linkedin_url"]


def test_serp_upgrades_opaque_url():
    row = {
        "full_name": "Sara Mandirola",
        "title": "Real Estate Advisor",
        "company_name": "LM Group Real Estate",
        "location": "Marbella, Andalusia, Spain",
        "linkedin_url": "https://www.linkedin.com/in/ACwAAATQlu4BYwa8fakHDYchobWcVsHjNMhA8h4",
    }
    with patch(
        "agent.utils.vanity_linkedin.resolve_vanity_via_serp",
        return_value=("https://www.linkedin.com/in/sara-mandirola-12607723", 0.0004),
    ):
        out = resolve_row_linkedin_vanity(row)
    assert out["vanity_resolve_status"] == "resolved"
    assert out["linkedin_url"] == "https://www.linkedin.com/in/sara-mandirola-12607723"
    assert out["linkedin_url_opaque"].startswith("https://www.linkedin.com/in/ACw")
