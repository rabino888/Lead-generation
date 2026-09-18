"""Gated talent list-run: assert_can_start + smoke skips phone."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.utils.list_run import assert_can_start_smoke_gate
from agent.utils.talent_list_run import (
    assert_talent_seeds_ready,
    start_talent_smoke_gate,
)
from scripts.run_talent_campaign import talent_phone_gate


def _ready_talent_icp() -> dict:
    return {
        "job_titles": ["Backend Engineer"],
        "contact_locations": ["Spain"],
        "person_match": {"skills": ["Python"]},
    }


def _write_talent_campaign(tmp_path: Path, *, with_people_seeds: bool) -> Path:
    camp = tmp_path / "talent_gated"
    camp.mkdir()
    (camp / "campaign.json").write_text(
        json.dumps(
            {
                "campaign_id": "talent_gated",
                "campaign_type": "talent_search",
                "display_name": "Talent gated",
            }
        ),
        encoding="utf-8",
    )
    (camp / "icp.json").write_text(json.dumps(_ready_talent_icp()), encoding="utf-8")
    (camp / "enrichment_plan.json").write_text(
        json.dumps(
            {
                "campaign_type": "talent_search",
                "modules": {"apollo_phone": True},
                "target_leads": 20,
            }
        ),
        encoding="utf-8",
    )
    if with_people_seeds:
        (camp / "people_seeds.csv").write_text(
            "linkedin_url,full_name,title,location\n"
            "https://www.linkedin.com/in/ada-lovelace,Ada Lovelace,Engineer,Madrid\n",
            encoding="utf-8",
        )
    return camp


def test_assert_can_start_allows_talent_with_people_seeds(tmp_path, monkeypatch):
    camp = _write_talent_campaign(tmp_path, with_people_seeds=True)

    mock_camp = MagicMock()
    mock_camp.campaign_id = "talent_gated"
    mock_camp.campaign_dir = camp
    mock_camp.icp_path = camp / "icp.json"
    mock_camp.seeds_csv = camp / "seeds.csv"

    monkeypatch.setattr(
        "agent.utils.campaign_config.load_campaign",
        lambda _cid: mock_camp,
    )

    assert_can_start_smoke_gate("talent_gated")


def test_assert_can_start_blocks_empty_talent(tmp_path, monkeypatch):
    camp = _write_talent_campaign(tmp_path, with_people_seeds=False)

    mock_camp = MagicMock()
    mock_camp.campaign_id = "talent_gated"
    mock_camp.campaign_dir = camp
    mock_camp.icp_path = camp / "icp.json"
    mock_camp.seeds_csv = camp / "seeds.csv"

    monkeypatch.setattr(
        "agent.utils.campaign_config.load_campaign",
        lambda _cid: mock_camp,
    )

    with pytest.raises(FileNotFoundError, match="people seeds|people_seeds|linkedin_people"):
        assert_can_start_smoke_gate("talent_gated")


def test_assert_talent_seeds_ready_direct(tmp_path):
    camp = _write_talent_campaign(tmp_path, with_people_seeds=True)
    mock = MagicMock()
    mock.campaign_dir = camp
    assert_talent_seeds_ready(mock)

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    empty = _write_talent_campaign(empty_root, with_people_seeds=False)
    mock2 = MagicMock()
    mock2.campaign_dir = empty
    with pytest.raises(FileNotFoundError):
        assert_talent_seeds_ready(mock2)


def test_assert_talent_seeds_ready_accepts_array_seed_sources(tmp_path):
    camp = _write_talent_campaign(tmp_path, with_people_seeds=False)
    (camp / "seed_sources.json").write_text(
        json.dumps(
            [
                {
                    "id": "linkedin_people_backend_es",
                    "type": "linkedin_people",
                    "name": "People",
                    "enabled": True,
                    "config": {"query": "Backend Spain", "max_results": 50},
                }
            ]
        ),
        encoding="utf-8",
    )
    mock_camp = MagicMock()
    mock_camp.campaign_dir = camp
    assert_talent_seeds_ready(mock_camp)


def test_talent_phone_skipped_on_smoke(tmp_path, monkeypatch):
    """Smoke path forces skip_phone so T06 does not run even if plan phone is on."""
    camp = _write_talent_campaign(tmp_path, with_people_seeds=True)
    stages = camp / "stages"
    stages.mkdir()

    (stages / "T01_raw_people.csv").write_text(
        "person_id,linkedin_url,full_name,first_name,last_name,headline,title,"
        "location,company_name,company_linkedin_url,source_url,seed_source_id,notes\n"
        "p1,https://www.linkedin.com/in/a,A,,,Engineer,Engineer,Madrid,Co,,"
        "https://www.linkedin.com/in/a,csv,\n",
        encoding="utf-8",
    )

    mock_camp = MagicMock()
    mock_camp.campaign_id = "talent_gated"
    mock_camp.campaign_dir = camp
    mock_camp.stages_dir = stages

    phone_calls: list[tuple] = []

    def _ok_main():
        return 0

    def _dedupe():
        (stages / "T02_deduped.csv").write_text(
            (stages / "T01_raw_people.csv").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return 0

    def _icp():
        (stages / "T03_icp_matched.csv").write_text(
            (stages / "T01_raw_people.csv").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return 0

    def _apollo():
        (stages / "T05_apollo_contactable.csv").write_text(
            "linkedin_url,full_name,title,location,company_name,email\n"
            "https://www.linkedin.com/in/a,A,Engineer,Madrid,Co,a@example.com\n",
            encoding="utf-8",
        )
        return 0

    def _phone():
        phone_calls.append(("phone",))
        return 0

    monkeypatch.setattr("agent.utils.campaign_config.load_campaign", lambda _c: mock_camp)
    monkeypatch.setattr(
        "agent.utils.enrichment_plan.load_plan",
        lambda *_a, **_k: {
            "campaign_type": "talent_search",
            "modules": {"apollo_phone": True},
        },
    )
    monkeypatch.setattr("agent.utils.icp_rules.ensure_stages_dir", lambda _c: stages)
    monkeypatch.setattr("scripts.stage_talent_seed.main", _ok_main)
    monkeypatch.setattr("scripts.stage_talent_dedupe.main", _dedupe)
    monkeypatch.setattr("scripts.stage_talent_icp.main", _icp)
    monkeypatch.setattr("scripts.stage_talent_profile.main", _ok_main)
    monkeypatch.setattr("scripts.stage_talent_apollo.main", _apollo)
    monkeypatch.setattr("scripts.stage_talent_phone.main", _phone)

    action, err = talent_phone_gate(
        {"modules": {"apollo_phone": True}},
        skip_phone=True,
    )
    assert action == "skip_flag"
    assert err is None

    state = start_talent_smoke_gate(camp, "talent_gated", smoke_leads=1, target_leads=10)
    assert state["phase"] == "awaiting_approval"
    assert phone_calls == []
    assert state["counts"].get("smoke_contactable") == 1
