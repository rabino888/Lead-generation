"""Normalize anyapi.linkedin.user.profile.full payloads."""
from __future__ import annotations

from agent.integrations.treg import _anyapi_profile_payload, _profile_has_work_history


def test_anyapi_unwrap_found_data():
    payload = {
        "output": {
            "found": True,
            "data": {
                "firstName": "Hedde",
                "lastName": "Weijers",
                "about": "Years in residential development.",
                "experience": [
                    {
                        "position": "Real estate consultant",
                        "companyName": "Max Brokers",
                        "startDate": "Jan 2026",
                        "endDate": "Present",
                    }
                ],
                "url": "https://www.linkedin.com/in/hedde-weijers-705264215",
            },
        },
        "costUsd": 0.0029,
    }
    item = _anyapi_profile_payload(payload)
    assert item["firstName"] == "Hedde"
    assert len(item["experience"]) == 1


def test_anyapi_not_found():
    assert _anyapi_profile_payload({"output": {"found": False, "data": {}}}) == {}


def test_profile_has_work_history():
    assert _profile_has_work_history({"experience_summary": "Role @ Co"})
    assert not _profile_has_work_history({"experience_summary": ""})
    assert not _profile_has_work_history({"current_position": "Headline only"})
