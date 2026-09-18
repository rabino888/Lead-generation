"""Talent orchestrator (TS-7/TS-8): refuse company_outreach; phone gate."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

from scripts.run_talent_campaign import main, talent_plan_gate


def test_talent_plan_gate_accepts_talent_search():
    assert talent_plan_gate({"campaign_type": "talent_search"}) is None


def test_talent_plan_gate_refuses_company_outreach():
    err = talent_plan_gate({"campaign_type": "company_outreach"})
    assert err is not None
    assert "company_outreach" in err
    assert "run_deterministic_campaign" in err


def test_talent_plan_gate_default_is_company():
    err = talent_plan_gate({})
    assert err is not None
    assert "company_outreach" in err


def test_main_refuses_company_outreach_plan(monkeypatch, tmp_path, capsys):
    """Orchestrator exits 2 when loaded plan is company_outreach (tmpdir stub)."""
    campaign = MagicMock()
    campaign.campaign_id = "co_demo"
    campaign.campaign_dir = tmp_path
    campaign.stages_dir = tmp_path / "stages"
    campaign.stages_dir.mkdir()

    monkeypatch.setattr(
        "scripts.run_talent_campaign.load_campaign",
        lambda _cid: campaign,
    )
    monkeypatch.setattr(
        "scripts.run_talent_campaign.ensure_stages_dir",
        lambda _c: campaign.stages_dir,
    )
    monkeypatch.setattr(
        "scripts.run_talent_campaign.load_plan",
        lambda _d, _cid: {"campaign_type": "company_outreach"},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_talent_campaign.py", "--campaign", "co_demo"],
    )

    rc = main()
    assert rc == 2
    err = capsys.readouterr().err
    assert "company_outreach" in err
    assert "run_deterministic_campaign" in err


def test_talent_phone_gate_exported():
    from scripts.run_talent_campaign import talent_phone_gate as gate

    action, err = gate(
        {"modules": {"apollo_phone": False}},
        skip_phone=False,
    )
    assert action == "skip_plan_off"
    assert err is None
