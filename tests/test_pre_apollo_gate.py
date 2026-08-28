"""Tests for pre-Apollo hard gate."""
from __future__ import annotations

from pathlib import Path

from agent.utils.icp_rules import IcpRules
from agent.utils.pre_apollo_gate import pre_apollo_reject_reason


def _rules() -> IcpRules:
    return IcpRules(
        company_size_min=1,
        company_size_max=200,
        excluded_domains=["blocked.com"],
        excluded_keywords=["staffing agency"],
        exclude_mega_outsourcers=False,
    )


def test_rejects_excluded_domain():
    reason = pre_apollo_reject_reason(
        {"company_name": "Acme", "website": "https://blocked.com"},
        _rules(),
        by_domain={},
        by_name={},
        attempted_domains=set(),
    )
    assert reason and reason.startswith("excluded_domain:")


def test_rejects_employees_above_max():
    reason = pre_apollo_reject_reason(
        {
            "company_name": "Big Co",
            "website": "https://bigco.com",
            "company_size": "500",
        },
        _rules(),
        by_domain={},
        by_name={},
        attempted_domains=set(),
    )
    assert reason and "employees_above_max" in reason


def test_rejects_already_attempted_domain():
    reason = pre_apollo_reject_reason(
        {
            "company_name": "Retry Co",
            "website": "https://retryco.com",
            "company_size": "50",
        },
        _rules(),
        by_domain={},
        by_name={},
        attempted_domains={"retryco.com"},
    )
    assert reason and "apollo_already_attempted" in reason


def test_keeps_valid_startup():
    reason = pre_apollo_reject_reason(
        {
            "company_name": "Startup AI",
            "website": "https://startup.ai",
            "company_size": "50",
            "notes": "employees: 45",
        },
        _rules(),
        by_domain={},
        by_name={},
        attempted_domains=set(),
    )
    assert reason is None


def test_filter_pre_apollo_splits_rows(tmp_path: Path):
    from agent.utils.pre_apollo_gate import filter_pre_apollo

    campaign_dir = tmp_path
    rules = _rules()
    rows = [
        {"company_name": "Good", "website": "https://good.com", "company_size": "30"},
        {"company_name": "Bad", "website": "https://blocked.com"},
    ]
    kept, rejected = filter_pre_apollo(rows, rules, campaign_dir)
    assert len(kept) == 1
    assert len(rejected) == 1
    assert rejected[0].get("pre_apollo_reason")
