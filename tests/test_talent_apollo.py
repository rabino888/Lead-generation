"""Talent T05: Apollo email-by-LinkedIn (mocked — no live API)."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from agent.integrations.apollo import (
    enrich_person_by_linkedin,
    get_credits_used,
    reset_credits_used,
)
from agent.models import Contact
from agent.utils.talent_apollo import (
    REJECT_MATCH_FAILED,
    REJECT_NO_EMAIL,
    REJECT_PROFILE_NOT_OK,
    apply_talent_apollo,
    run_talent_apollo_stage,
)
from agent.utils.talent_people import (
    STAGE_T04_PROFILES,
    STAGE_T05_APOLLO_CONTACTABLE,
    STAGE_T05_APOLLO_REJECTED,
    T01_FIELDS,
)


def _base_row(**kwargs) -> dict:
    row = {k: "" for k in T01_FIELDS}
    row.update(
        {
            "person_id": "p-1",
            "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
            "full_name": "Ada Lovelace",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "title": "Engineer",
            "location": "Spain",
            "company_name": "Analytical Engines",
            "profile_status": "ok",
        }
    )
    row.update(kwargs)
    return row


def _mock_contact(*, email: Optional[str] = "ada@example.com", **kwargs) -> Contact:
    return Contact(
        apollo_person_id=kwargs.get("apollo_person_id", "apoll-ada"),
        name=kwargs.get("name", "Ada Lovelace"),
        title=kwargs.get("title", "Engineer"),
        email=email,
        email_status=kwargs.get("email_status", "verified"),
        linkedin_url=kwargs.get(
            "linkedin_url", "https://www.linkedin.com/in/ada-lovelace"
        ),
        location=kwargs.get("location", "Spain"),
    )


def test_contactable_when_email_revealed():
    def enrich(url, **_kwargs):
        return _mock_contact()

    ok, rej = apply_talent_apollo([_base_row()], enrich_fn=enrich)
    assert len(ok) == 1
    assert len(rej) == 0
    assert ok[0]["email"] == "ada@example.com"
    assert ok[0]["apollo_person_id"] == "apoll-ada"
    assert ok[0]["apollo_reveal_status"] == "revealed"
    assert ok[0]["email_status"] == "verified"


def test_rejected_when_no_personal_email():
    def enrich(url, **_kwargs):
        return _mock_contact(email=None)

    ok, rej = apply_talent_apollo([_base_row()], enrich_fn=enrich)
    assert ok == []
    assert len(rej) == 1
    assert rej[0]["reject_reason"] == REJECT_NO_EMAIL
    assert rej[0]["email"] == ""


def test_rejected_when_match_fails():
    def enrich(url, **_kwargs):
        return None

    ok, rej = apply_talent_apollo([_base_row()], enrich_fn=enrich)
    assert ok == []
    assert rej[0]["reject_reason"] == REJECT_MATCH_FAILED


def test_skips_failed_profiles_by_default():
    calls: list[str] = []

    def enrich(url, **_kwargs):
        calls.append(url)
        return _mock_contact()

    rows = [
        _base_row(person_id="ok", linkedin_url="https://www.linkedin.com/in/ok"),
        _base_row(
            person_id="bad",
            linkedin_url="https://www.linkedin.com/in/bad",
            profile_status="failed",
        ),
    ]
    ok, rej = apply_talent_apollo(rows, enrich_fn=enrich)
    assert len(ok) == 1
    assert ok[0]["person_id"] == "ok"
    assert len(rej) == 1
    assert rej[0]["reject_reason"] == REJECT_PROFILE_NOT_OK
    assert len(calls) == 1


def test_include_failed_profiles_attempts_apollo():
    calls: list[str] = []

    def enrich(url, **_kwargs):
        calls.append(url)
        return _mock_contact(email="x@y.com", linkedin_url=url)

    rows = [
        _base_row(
            person_id="bad",
            linkedin_url="https://www.linkedin.com/in/bad",
            profile_status="failed",
        ),
    ]
    ok, rej = apply_talent_apollo(
        rows, include_failed_profiles=True, enrich_fn=enrich
    )
    assert len(ok) == 1
    assert rej == []
    assert len(calls) == 1


def test_run_stage_writes_csvs(tmp_path: Path):
    camp = tmp_path / "talent_camp"
    stages = camp / "stages"
    stages.mkdir(parents=True)
    t04 = stages / STAGE_T04_PROFILES
    with t04.open("w", encoding="utf-8", newline="") as f:
        fields = list(T01_FIELDS) + ["profile_status"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow(_base_row())
        w.writerow(
            _base_row(
                person_id="p-2",
                linkedin_url="https://www.linkedin.com/in/no-email",
                full_name="No Email",
            )
        )

    def enrich(url, **_kwargs):
        if "no-email" in url:
            return _mock_contact(email=None, linkedin_url=url, apollo_person_id="x2")
        return _mock_contact(linkedin_url=url)

    ok_path, rej_path, n_ok, n_rej = run_talent_apollo_stage(
        camp, enrich_fn=enrich
    )
    assert ok_path.name == STAGE_T05_APOLLO_CONTACTABLE
    assert rej_path.name == STAGE_T05_APOLLO_REJECTED
    assert n_ok == 1
    assert n_rej == 1

    with ok_path.open(encoding="utf-8-sig", newline="") as f:
        contactable = list(csv.DictReader(f))
    with rej_path.open(encoding="utf-8-sig", newline="") as f:
        rejected = list(csv.DictReader(f))
    assert contactable[0]["email"] == "ada@example.com"
    assert rejected[0]["reject_reason"] == REJECT_NO_EMAIL


def test_include_failed_loads_failed_csv(tmp_path: Path):
    from agent.utils.talent_people import STAGE_T04_PROFILE_FAILED, write_people_csv

    camp = tmp_path / "camp"
    stages = camp / "stages"
    stages.mkdir(parents=True)
    ok_row = _base_row(profile_status="ok")
    fail_row = _base_row(
        person_id="p-fail",
        linkedin_url="https://www.linkedin.com/in/failed-one",
        full_name="Fail One",
        profile_status="failed",
    )
    write_people_csv(stages / STAGE_T04_PROFILES, [ok_row])
    # Failed file may have extra profile columns — write via DictWriter
    with (stages / STAGE_T04_PROFILE_FAILED).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fail_row.keys()))
        w.writeheader()
        w.writerow(fail_row)

    seen: list[str] = []

    def enrich(url, **_kwargs):
        seen.append(url)
        return _mock_contact(linkedin_url=url, email=f"{url.split('/')[-1]}@ex.com")

    _ok, _rej, n_ok, _n_rej = run_talent_apollo_stage(
        camp, include_failed_profiles=True, enrich_fn=enrich
    )
    assert n_ok == 2
    assert any("failed-one" in u for u in seen)
    assert any("ada-lovelace" in u for u in seen)


def test_enrich_person_by_linkedin_uses_people_match():
    """Unit-test Apollo helper with mocked _post — no live API."""
    reset_credits_used()
    fake = {
        "credits_consumed": 1,
        "person": {
            "id": "p99",
            "name": "Ada Lovelace",
            "email": "ada@engines.dev",
            "email_status": "verified",
            "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
            "title": "Engineer",
        },
    }
    with patch("agent.integrations.apollo._post", return_value=fake) as mock_post:
        contact = enrich_person_by_linkedin(
            "https://www.linkedin.com/in/ada-lovelace",
            first_name="Ada",
            last_name="Lovelace",
            organization_name="Analytical Engines",
        )
    assert contact is not None
    assert contact.email == "ada@engines.dev"
    assert contact.apollo_person_id == "p99"
    assert get_credits_used() == 1
    assert mock_post.call_count == 1
    _args, kwargs = mock_post.call_args
    assert _args[0] == "people/match"
    param_list = kwargs.get("params") or []
    keys = [k for k, _ in param_list]
    assert "linkedin_url" in keys
    assert ("reveal_personal_emails", "true") in param_list
    assert ("reveal_phone_number", "false") in param_list


def test_enrich_person_by_linkedin_fallback_name_org():
    reset_credits_used()
    empty = {"credits_consumed": 0, "person": None}
    found = {
        "credits_consumed": 1,
        "person": {
            "id": "p2",
            "email": "ada@engines.dev",
            "email_status": "verified",
            "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
        },
    }

    def side_effect(endpoint, payload=None, params=None):
        assert endpoint == "people/match"
        keys = {k for k, _ in (params or [])}
        if "organization_name" in keys:
            return found
        return empty

    with patch("agent.integrations.apollo._post", side_effect=side_effect):
        contact = enrich_person_by_linkedin(
            "https://www.linkedin.com/in/ada-lovelace",
            first_name="Ada",
            last_name="Lovelace",
            organization_name="Analytical Engines",
            domain="engines.dev",
        )
    assert contact is not None
    assert contact.email == "ada@engines.dev"
    assert get_credits_used() == 1


def test_missing_input_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        run_talent_apollo_stage(tmp_path / "missing")
