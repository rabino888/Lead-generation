"""Tests for Apollo contactability gate (email + DM LinkedIn from Apollo)."""
from __future__ import annotations

import os

import pytest

from agent.models import Contact
from agent.utils.apollo_contact_gate import apollo_contactable, apollo_requires_dm_linkedin


def test_requires_dm_linkedin_by_default():
    os.environ.pop("APOLLO_ALLOW_MISSING_DM_LINKEDIN", None)
    assert apollo_requires_dm_linkedin() is True


def test_allow_missing_dm_linkedin_env():
    os.environ["APOLLO_ALLOW_MISSING_DM_LINKEDIN"] = "1"
    assert apollo_requires_dm_linkedin() is False
    os.environ.pop("APOLLO_ALLOW_MISSING_DM_LINKEDIN", None)


def test_contactable_with_email_and_linkedin():
    dm = Contact(email="a@co.com", linkedin_url="https://www.linkedin.com/in/jane")
    ok, reason = apollo_contactable(dm)
    assert ok
    assert reason == ""


def test_reject_no_email():
    dm = Contact(linkedin_url="https://www.linkedin.com/in/jane")
    ok, reason = apollo_contactable(dm)
    assert not ok
    assert "email" in reason.lower()


def test_reject_email_without_linkedin_when_required():
    os.environ.pop("APOLLO_ALLOW_MISSING_DM_LINKEDIN", None)
    dm = Contact(email="a@co.com", name="Jane")
    ok, reason = apollo_contactable(dm)
    assert not ok
    assert "linkedin" in reason.lower()


def test_email_only_ok_when_opt_out():
    os.environ["APOLLO_ALLOW_MISSING_DM_LINKEDIN"] = "1"
    dm = Contact(email="a@co.com")
    ok, _ = apollo_contactable(dm)
    assert ok
    os.environ.pop("APOLLO_ALLOW_MISSING_DM_LINKEDIN", None)
