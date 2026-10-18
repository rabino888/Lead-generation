"""Apollo people search must not AND person_seniorities with titles."""
from __future__ import annotations

from agent.integrations.apollo import enrich_contacts
from agent.models import Contact


def _capture_search(monkeypatch, people):
    seen: list[list[tuple]] = []

    def fake_post(endpoint, payload=None, params=None):
        seen.append(list(params or []))
        if endpoint == "mixed_people/api_search":
            return {"people": people, "total_entries": len(people)}
        return {"person": {}}

    monkeypatch.setattr("agent.integrations.apollo._post", fake_post)
    return seen


def test_search_does_not_send_person_seniorities(monkeypatch):
    seen = _capture_search(monkeypatch, [])
    contact, _generic, reason = enrich_contacts(
        "Box Insurance",
        "https://www.boxinsurance.com",
        ["President", "Owner", "General Manager"],
        contact_locations=["United States"],
    )
    assert contact is None
    assert "no person matched the titles" in reason
    assert seen
    keys = [key for params in seen for key, _value in params]
    assert "person_seniorities[]" not in keys
    assert ("person_titles[]", "President") in seen[0]


def test_title_mismatch_is_distinct_from_missing_person(monkeypatch):
    _capture_search(
        monkeypatch,
        [{"id": "1", "first_name": "Mark", "title": "Account Executive", "has_email": True}],
    )
    _contact, _generic, reason = enrich_contacts(
        "Box Insurance",
        "https://www.boxinsurance.com",
        ["President"],
        contact_locations=["United States"],
    )
    assert "no person matched ICP titles" in reason
    assert "Account Executive" in reason


def test_person_found_but_email_not_revealed(monkeypatch):
    _capture_search(
        monkeypatch,
        [{"id": "1", "first_name": "Adam", "title": "President", "has_email": True}],
    )
    monkeypatch.setattr("agent.integrations.apollo._reveal_person", lambda *a, **k: None)
    _contact, _generic, reason = enrich_contacts(
        "Box Insurance",
        "https://www.boxinsurance.com",
        ["President"],
        contact_locations=["United States"],
    )
    assert reason.startswith("person found, email not revealed")
    assert "Adam" in reason
    assert "President" in reason


def test_reveal_returns_contact(monkeypatch):
    _capture_search(
        monkeypatch,
        [{"id": "1", "first_name": "Adam", "title": "President", "has_email": True}],
    )
    monkeypatch.setattr(
        "agent.integrations.apollo._reveal_person",
        lambda *a, **k: Contact(name="Adam Syswerda", title="President", email="adam@boxinsurance.com"),
    )
    contact, _generic, reason = enrich_contacts(
        "Box Insurance",
        "https://www.boxinsurance.com",
        ["President"],
    )
    assert reason == ""
    assert contact is not None
    assert contact.email == "adam@boxinsurance.com"


def test_matched_title_without_email_is_logged(monkeypatch):
    _capture_search(
        monkeypatch,
        [{"id": "1", "first_name": "Bryan", "title": "Broker", "has_email": False}],
    )
    _contact, _generic, reason = enrich_contacts(
        "IMC",
        "https://imcipls.com",
        ["Broker", "Principal"],
    )
    assert "no email to reveal" in reason
    assert "Bryan" in reason
