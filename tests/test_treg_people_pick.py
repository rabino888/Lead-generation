"""Pure helpers for the Apollo vs treg email comparison."""
from __future__ import annotations

from agent.integrations.treg import (
    email_from_find_payload,
    people_from_search_payload,
    pick_decision_maker,
)


def test_pick_prefers_icp_title_over_first_row():
    people = [
        {"full_name": "Intern Pat", "title": "Recruiting Coordinator", "email": "pat@ex.com"},
        {"full_name": "Ada Owner", "title": "Owner", "linkedin_url": "https://www.linkedin.com/in/ada"},
    ]
    chosen = pick_decision_maker(people, ["Owner", "CEO"])
    assert chosen is not None
    assert chosen["full_name"] == "Ada Owner"


def test_people_and_email_unwrap_routed_output():
    search = {"output": {"people": [{"name": "Ada"}]}, "_treg": {"served_by": "hunter"}}
    assert people_from_search_payload(search) == [{"name": "Ada"}]
    found = {"output": {"email": "ada@agency.com"}}
    assert email_from_find_payload(found) == "ada@agency.com"
    assert email_from_find_payload({"output": {"email": None}}) == ""
