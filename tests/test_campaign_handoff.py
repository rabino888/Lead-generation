"""Tests for campaign ICP handoff context."""
from __future__ import annotations

import json
from pathlib import Path

from agent.utils.campaign_handoff import (
    build_outreach_handoff,
    load_campaign_output_context,
    write_outreach_handoff,
)


def test_load_campaign_output_context(tmp_path: Path):
    campaign_dir = tmp_path / "demo_campaign"
    campaign_dir.mkdir()
    (campaign_dir / "icp.json").write_text(
        json.dumps(
            {
                "campaign_id": "demo_campaign",
                "industries": ["software"],
                "company_size_min": 10,
                "company_size_max": 200,
                "job_titles": ["CEO"],
                "contact_locations": ["United States"],
                "geo": {"primary_location": "United States"},
                "website_analysis_mode": "hiring_first",
            }
        ),
        encoding="utf-8",
    )
    (campaign_dir / "run_payload.json").write_text(
        json.dumps(
            {
                "sender": {
                    "name": "Demo Client",
                    "core_offer": "Test offer",
                    "services": ["SaaS"],
                    "target_pain_points": ["manual ops"],
                },
                "icp": {"industry": "software", "location": "United States"},
            }
        ),
        encoding="utf-8",
    )

    ctx = load_campaign_output_context("demo_campaign", root=tmp_path)
    assert ctx is not None
    assert ctx["summary"]["client_name"] == "Demo Client"
    assert ctx["summary"]["website_analysis_mode"] == "hiring_first"
    assert "CEO" in ctx["summary"]["job_titles"]


def test_outreach_handoff_written(tmp_path: Path):
    campaign_dir = tmp_path / "demo_campaign"
    campaign_dir.mkdir()
    (campaign_dir / "icp.json").write_text(
        json.dumps({"campaign_id": "demo_campaign", "job_titles": ["CTO"]}),
        encoding="utf-8",
    )
    (campaign_dir / "run_payload.json").write_text(
        json.dumps({"sender": {"name": "Demo"}, "icp": {}}),
        encoding="utf-8",
    )

    handoff = build_outreach_handoff(
        campaign_id="demo_campaign",
        run_id="run_test_1",
        csv_path=str(tmp_path / "run_test_1.csv"),
        sender={"name": "Demo"},
        icp_runtime={"job_titles": ["CTO"]},
        root=tmp_path,
    )
    out = tmp_path / "run_test_1_handoff.json"
    write_outreach_handoff(out, handoff)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["campaign_id"] == "demo_campaign"
    assert loaded["icp_machine"]["job_titles"] == ["CTO"]
    assert loaded["outreach_cli"]["csv"].endswith("run_test_1.csv")
