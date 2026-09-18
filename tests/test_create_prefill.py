"""Modular create-prefill: URLs + resolve across clients / ICP-only."""
from __future__ import annotations

import json
from pathlib import Path

from agent.utils.client_store import ensure_client, save_icp
from agent.utils.create_prefill import (
    assert_prefill_for_all_ready_icps,
    build_campaign_create_url,
    merge_client_options,
    resolve_create_prefill,
)


def _ready_icp(tmp_path: Path, client_id: str, icp_id: str, **extra):
    ensure_client(tmp_path, client_id, client_id.replace("_", " ").title())
    data = {
        "schema_version": "1",
        "industries": ["software"],
        "excluded_industries": ["staffing and recruiting"],
        "geo": {
            "primary_location": "United States",
            "country_codes": {"United States": "US"},
            "location_hints": ["usa"],
            "segments": ["US"],
        },
        "company_size_min": 10,
        "company_size_max": 50,
        "employee_ranges": ["11,50"],
        "include_keywords": ["saas"],
        "exclude_keywords": ["freelance only"],
        "excluded_name_patterns": r"freelance|staffing",
        "excluded_domains": ["google.com"],
        "job_titles": ["CEO", "CTO"],
        "contact_locations": ["United States"],
        "website_analysis_mode": "full",
    }
    data.update(extra)
    save_icp(
        tmp_path,
        client_id,
        icp_id,
        icp_data=data,
        display_name=icp_id.replace("_", " ").title(),
        brief="Ready ICP for tests",
        campaign_type="company_outreach",
        client_name=client_id,
        create_new=True,
    )


def test_build_campaign_create_url():
    url = build_campaign_create_url(
        client_id="vista_business_brain",
        icp_id="ecom_brand_owners_us",
        campaign_type="company_outreach",
    )
    assert url.startswith("/builder?")
    assert "client=vista_business_brain" in url
    assert "new=1" in url
    assert "icp=ecom_brand_owners_us" in url
    assert "type=company_outreach" in url


def test_merge_client_options_includes_icp_only():
    merged = merge_client_options(
        [{"client_id": "vista", "client_name": "Vista"}],
        [{"client_id": "automata", "client_name": "Automata"}],
    )
    ids = [c["client_id"] for c in merged]
    assert ids == ["automata", "vista"]


def test_resolve_prefill_icp_only_client(tmp_path: Path):
    _ready_icp(tmp_path, "solo_client", "solo_icp")
    pre = resolve_create_prefill(
        tmp_path,
        client_id="solo_client",
        icp_id="solo_icp",
        campaign_type="company_outreach",
    )
    assert pre["ok"] is True
    assert pre["client_mode"] == "existing"
    assert pre["icp_mode"] == "existing"
    assert pre["icp_id"] == "solo_icp"
    assert pre["suggested_label"].endswith("(new list)")
    assert "solo_client" in pre["create_url"]
    assert "icp=solo_icp" in pre["create_url"]


def test_resolve_prefill_rejects_incomplete(tmp_path: Path):
    ensure_client(tmp_path, "thinco", "Thin Co")
    save_icp(
        tmp_path,
        "thinco",
        "thin_icp",
        icp_data={"job_titles": ["CEO"]},  # intentionally thin
        display_name="Thin",
        brief="",
        campaign_type="company_outreach",
        create_new=True,
    )
    pre = resolve_create_prefill(tmp_path, client_id="thinco", icp_id="thin_icp")
    assert pre["ok"] is False
    assert pre["icp_id"] is None
    assert any("incomplete" in e.lower() for e in pre["errors"])


def test_assert_prefill_for_all_ready_icps_multi_client(tmp_path: Path):
    _ready_icp(tmp_path, "alpha", "alpha_icp")
    _ready_icp(tmp_path, "beta", "beta_icp_a")
    _ready_icp(tmp_path, "beta", "beta_icp_b")
    report = assert_prefill_for_all_ready_icps(tmp_path)
    assert report["checked"] == 3
    assert report["failures"] == []


def test_live_data_all_ready_icps_prefill():
    """Regression: every ready ICP on disk must resolve for Generate campaign."""
    root = Path(__file__).resolve().parents[1] / "data"
    if not (root / "clients").is_dir():
        return
    report = assert_prefill_for_all_ready_icps(root)
    assert report["checked"] >= 1
    assert report["failures"] == [], report["failures"]
