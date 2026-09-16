"""client_runs_dir smoke vs full paths + location-bound ICP flag."""
from __future__ import annotations

from agent.utils.campaign_config import CampaignGeo, CampaignIcpConfig
from agent.utils.client_paths import client_runs_dir, is_smoke_run
from agent.utils.icp_rules import icp_is_location_bound, resolve_require_location_match


def test_smoke_runs_under_client_subfolder():
    assert is_smoke_run(keyword="smoke-gate:demo")
    path = client_runs_dir("decoracel", keyword="smoke-gate:x")
    assert path.as_posix().endswith("clients/decoracel/smoke/runs")
    full = client_runs_dir("decoracel", keyword="full-batch")
    assert path.as_posix().endswith("clients/decoracel/smoke/runs") or "smoke" in path.as_posix()
    assert "smoke" not in full.as_posix().split("decoracel")[-1] or full.as_posix().endswith(
        "clients/decoracel/runs"
    )
    assert full == client_runs_dir("decoracel")


def test_location_bound_auto_enables_match():
    bound = CampaignIcpConfig(
        campaign_id="c",
        geo=CampaignGeo(primary_location="Spain", location_hints=["málaga"], segments=["Malaga"]),
        contact_locations=["Spain"],
        require_location_match=None,
    )
    assert icp_is_location_bound(bound)
    assert resolve_require_location_match(bound) is True

    unbound = CampaignIcpConfig(campaign_id="c", require_location_match=None)
    assert icp_is_location_bound(unbound) is False
    assert resolve_require_location_match(unbound) is False

    forced_off = CampaignIcpConfig(
        campaign_id="c",
        geo=CampaignGeo(primary_location="Spain"),
        require_location_match=False,
    )
    assert resolve_require_location_match(forced_off) is False
