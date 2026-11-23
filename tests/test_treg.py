"""Unit tests for treg LinkedIn enrich adapters (no live network)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from agent.integrations import treg


@pytest.fixture(autouse=True)
def _treg_env(monkeypatch):
    monkeypatch.setenv("TREG_TOKEN", "test-token")
    monkeypatch.setenv("LINKEDIN_ENRICH_PROVIDER", "treg")
    monkeypatch.delenv("TREG_ORG", raising=False)


def test_linkedin_enrich_provider_falls_back_without_token(monkeypatch):
    monkeypatch.delenv("TREG_TOKEN", raising=False)
    monkeypatch.setenv("LINKEDIN_ENRICH_PROVIDER", "treg")
    assert treg.linkedin_enrich_provider() == "apify"


def test_linkedin_enrich_provider_apify_default(monkeypatch):
    monkeypatch.delenv("LINKEDIN_ENRICH_PROVIDER", raising=False)
    assert treg.linkedin_enrich_provider() == "apify"


def test_get_linkedin_person_profile_maps_routed_output(monkeypatch):
    body = {
        "output": {
            "full_name": "Ada Lovelace",
            "headline": "Mathematician",
            "about": "Notes on the Analytical Engine",
            "location": "London",
            "followers": 42,
            "linkedin_url": "https://www.linkedin.com/in/ada",
            "experience": [
                {"title": "Analyst", "companyName": "Analytical Engine Co"},
            ],
        },
        "_treg": {"served_by": "brightdata"},
    }
    resp = httpx.Response(
        200,
        json=body,
        headers={"X-Treg-Cost-Micro": "1500", "X-Treg-Call-Id": "c1"},
        request=httpx.Request("POST", "https://treg.to/call/x"),
    )

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = resp

    with patch("agent.integrations.treg.httpx.Client", return_value=mock_client):
        with patch(
            "agent.integrations.apify.get_linkedin_person_profile_apify"
        ) as mock_apify:
            out = treg.get_linkedin_person_profile("https://www.linkedin.com/in/ada")
            mock_apify.assert_not_called()

    assert out["name"] == "Ada Lovelace"
    assert out["headline"] == "Mathematician"
    assert out["about"].startswith("Notes")
    assert "Analyst" in (out.get("experience_summary") or "")
    assert out["_treg_served_by"] == "brightdata"
    mock_client.post.assert_called_once()
    kwargs = mock_client.post.call_args
    assert "treg.linkedin.user.profile" in kwargs.args[0]
    assert kwargs.kwargs["json"]["linkedin_url"].endswith("/in/ada")


def test_thin_treg_profile_falls_back_to_apify_for_experience(monkeypatch):
    """Headline-only treg payloads must not skip Apify (experience_summary gate)."""
    body = {
        "output": {
            "full_name": "Thin Person",
            "headline": "Property Manager at Acme",
            "linkedin_url": "https://www.linkedin.com/in/thin",
        },
        "_treg": {"served_by": "brightdata"},
    }
    resp = httpx.Response(
        200,
        json=body,
        headers={"X-Treg-Cost-Micro": "1000"},
        request=httpx.Request("POST", "https://treg.to/call/x"),
    )
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = resp

    apify_out = {
        "name": "Thin Person",
        "linkedin_url": "https://www.linkedin.com/in/thin",
        "headline": "Property Manager at Acme",
        "experience_summary": "Property Manager @ Acme (2020–present); Agent @ Other (2018–2020)",
        "current_position": "Property Manager @ Acme",
        "education_summary": "BA Business",
        "about": "Full about from Apify",
    }

    with patch("agent.integrations.treg.httpx.Client", return_value=mock_client):
        with patch(
            "agent.integrations.apify.get_linkedin_person_profile_apify",
            return_value=apify_out,
        ) as mock_apify:
            out = treg.get_linkedin_person_profile("https://www.linkedin.com/in/thin")

    mock_apify.assert_called_once()
    assert out["_apify_fallback"] is True
    assert "Property Manager @ Acme" in (out.get("experience_summary") or "")
    assert out["current_position"] == "Property Manager @ Acme"


def test_treg_skip_apify_fallback_env(monkeypatch):
    """Talent T04 defers Apify; contactable enrich runs it later."""
    monkeypatch.setenv("TREG_SKIP_APIFY_FALLBACK", "1")
    body = {
        "output": {
            "full_name": "Thin Person",
            "headline": "Advisor",
            "linkedin_url": "https://www.linkedin.com/in/thin",
        },
    }
    resp = httpx.Response(
        200,
        json=body,
        headers={"X-Treg-Cost-Micro": "1000"},
        request=httpx.Request("POST", "https://treg.to/call/x"),
    )
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = resp

    with patch("agent.integrations.treg.httpx.Client", return_value=mock_client):
        with patch(
            "agent.integrations.apify.get_linkedin_person_profile_apify",
            return_value={"experience_summary": "should not run"},
        ) as mock_apify:
            out = treg.get_linkedin_person_profile("https://www.linkedin.com/in/thin")

    mock_apify.assert_not_called()
    assert not (out.get("experience_summary") or "").strip()
    assert out.get("current_position") == "Advisor"


def test_linkedin_handle_decodes_percent_encoding():
    assert (
        treg._linkedin_handle(
            "https://www.linkedin.com/in/andr%c3%a9s-garc%c3%ada-g%c3%b3mez-85280927"
        )
        == "andrés-garcía-gómez-85280927"
    )


def test_posts_unwrap_routed_output_and_linkedin_url(monkeypatch):
    payload = {
        "output": {
            "elements": [
                {
                    "content": "New creative for the spring drop",
                    "linkedinUrl": "https://www.linkedin.com/feed/update/urn:li:activity:1",
                    "postedAt": {"date": "2026-10-01T00:00:00Z"},
                    "engagement": {"likes": 3, "comments": 1},
                }
            ]
        }
    }

    def _fake_call(*_args, **_kwargs):
        return payload, 0.004

    monkeypatch.setattr("agent.integrations.treg.call_catalog", _fake_call)
    out = treg.get_linkedin_profile_posts(
        "https://www.linkedin.com/in/jsurda", max_posts=5
    )
    assert out["posts_status"] == "ok"
    assert len(out["posts"]) == 1
    assert "spring drop" in out["posts"][0]["text"]
    assert out["posts"][0]["url"].endswith("activity:1")


def test_get_linkedin_profile_posts_normalizes_harvest_elements(monkeypatch):
    body = {
        "elements": [
            {
                "content": "We are hiring engineers",
                "linkedinUrl": "https://www.linkedin.com/feed/update/urn:li:1",
                "postedAt": {"date": "2026-09-01T00:00:00Z"},
                "engagement": {"likes": 10, "comments": 2},
            }
        ],
        "cost": 0.004,
    }
    resp = httpx.Response(
        200,
        json=body,
        headers={"X-Treg-Cost-Micro": "4000"},
        request=httpx.Request("GET", "https://treg.to/call/x"),
    )
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = resp

    with patch("agent.integrations.treg.httpx.Client", return_value=mock_client):
        out = treg.get_linkedin_profile_posts(
            "https://www.linkedin.com/in/ada", max_posts=5
        )

    assert len(out["posts"]) == 1
    assert "hiring" in out["posts"][0]["text"].lower()
    assert out["is_hiring"] == "yes"
    assert out["posts"][0]["likes"] == 10


def test_apify_dispatch_uses_treg_when_configured(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ENRICH_PROVIDER", "treg")
    monkeypatch.setenv("TREG_TOKEN", "tok")
    with patch(
        "agent.integrations.treg.enrich_decision_maker_linkedin",
        return_value={"about": "via-treg", "posts": [], "is_hiring": None},
    ) as mock_treg:
        from agent.integrations.apify import enrich_decision_maker_linkedin

        out = enrich_decision_maker_linkedin("https://www.linkedin.com/in/ada")
    assert out["about"] == "via-treg"
    mock_treg.assert_called_once()


def test_job_titles_from_linkedin_dataset_list():
    """Apify/treg LinkedIn job search wraps rows in output:[...], not output.jobs."""
    payload = {
        "output": [
            {"title": "Loan Officer", "company": "Mortgage Solutions Of Georgia"},
            {"jobTitle": "Processor"},
            {"title": "Loan Officer"},
        ]
    }
    titles = treg._job_titles_from_payload(payload)
    assert titles == ["Loan Officer", "Processor"]


def test_job_titles_from_routed_jobs_object():
    payload = {"output": {"jobs": [{"job_details": {"title": "Account Manager"}}]}}
    assert treg._job_titles_from_payload(payload) == ["Account Manager"]


def test_unit_rates_switch_to_treg_midpoints(monkeypatch):
    from agent.utils.enrichment_plan import unit_rates

    monkeypatch.delenv("LINKEDIN_ENRICH_PROVIDER", raising=False)
    apify_rates = unit_rates()
    monkeypatch.setenv("LINKEDIN_ENRICH_PROVIDER", "treg")
    treg_rates = unit_rates()
    assert treg_rates["apify_dm_profile_usd"] < apify_rates["apify_dm_profile_usd"]
    assert treg_rates["apify_dm_posts_usd"] < apify_rates["apify_dm_posts_usd"]
