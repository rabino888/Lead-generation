"""Tests for 04_apollo_contactable.csv guardrails."""
from __future__ import annotations

import os

from agent.utils.apollo_contactable import (
    is_email_only_row,
    merge_apollo_person_fields,
)


def test_merge_preserves_existing_linkedin():
    existing = {
        "decision_maker_email": "jane@acme.com",
        "decision_maker_name": "Jane Doe",
        "decision_maker_linkedin": "https://www.linkedin.com/in/janedoe",
        "decision_maker_apollo_person_id": "abc123",
    }
    new = {
        "decision_maker_email": "jane@acme.com",
        "decision_maker_name": "",
        "decision_maker_linkedin": "",
    }
    merged = merge_apollo_person_fields(existing, new)
    assert merged["decision_maker_linkedin"] == existing["decision_maker_linkedin"]
    assert merged["decision_maker_name"] == "Jane Doe"
    assert merged["decision_maker_apollo_person_id"] == "abc123"


def test_missing_dm_linkedin_row_detected():
    os.environ.pop("APOLLO_ALLOW_MISSING_DM_LINKEDIN", None)
    from agent.utils.apollo_contactable import is_missing_dm_linkedin_row

    assert is_missing_dm_linkedin_row({"decision_maker_email": "a@b.com"})
    assert not is_missing_dm_linkedin_row(
        {
            "decision_maker_email": "a@b.com",
            "decision_maker_linkedin": "https://www.linkedin.com/in/jane",
        }
    )


def test_email_only_row_detected():
    assert is_email_only_row({"decision_maker_email": "a@b.com"})
    assert not is_email_only_row(
        {
            "decision_maker_email": "a@b.com",
            "decision_maker_name": "Alice",
        }
    )
