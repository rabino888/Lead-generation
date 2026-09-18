"""Client-first ICP + campaign layout."""
from __future__ import annotations

import json
from pathlib import Path

from agent.utils.client_store import (
    extract_icp_from_campaign,
    list_client_icps,
    migrate_campaign_under_client,
    resolve_campaign_dir,
    save_icp,
    set_campaign_display_name,
    sync_campaign_config_to_client,
)


def test_save_and_list_icp(tmp_path: Path):
    root = tmp_path
    save_icp(
        root,
        "acme",
        "headhunting_us",
        icp_data={"job_titles": ["COO", "CTO"], "company_size_min": 1, "company_size_max": 50},
        display_name="Headhunting US 1-50",
        brief="COOs of headhunting firms",
        client_name="Acme",
        campaign_type="company_outreach",
    )
    items = list_client_icps(root, "acme")
    assert len(items) == 1
    assert items[0]["icp_id"] == "headhunting_us"
    assert items[0]["display_name"] == "Headhunting US 1-50"
    assert items[0]["campaign_type"] == "company_outreach"
    assert items[0]["icp"]["job_titles"][:2] == ["COO", "CTO"]
    assert (
        root / "clients" / "acme" / "company_outreach" / "icps" / "headhunting_us" / "icp.json"
    ).is_file()


def test_save_talent_itp_under_typed_tree(tmp_path: Path):
    root = tmp_path
    save_icp(
        root,
        "acme",
        "spain_engineers",
        icp_data={"job_titles": ["Software Engineer"], "contact_locations": ["Spain"]},
        display_name="Spain Engineers",
        campaign_type="talent_search",
        client_name="Acme",
    )
    items = list_client_icps(root, "acme")
    assert any(x["icp_id"] == "spain_engineers" for x in items)
    talent = next(x for x in items if x["icp_id"] == "spain_engineers")
    assert talent["campaign_type"] == "talent_search"
    assert (
        root / "clients" / "acme" / "talent_outreach" / "itps" / "spain_engineers" / "icp.json"
    ).is_file()


def test_legacy_flat_icps_still_list(tmp_path: Path):
    """Existing clients with flat icps/ remain readable."""
    root = tmp_path
    legacy = root / "clients" / "acme" / "icps" / "legacy_headhunt"
    legacy.mkdir(parents=True)
    (legacy / "icp.json").write_text(
        json.dumps({"job_titles": ["COO"], "company_size_min": 1, "company_size_max": 50}),
        encoding="utf-8",
    )
    (legacy / "meta.json").write_text(
        json.dumps(
            {
                "icp_id": "legacy_headhunt",
                "display_name": "Legacy Headhunt",
                "campaign_type": "company_outreach",
            }
        ),
        encoding="utf-8",
    )
    items = list_client_icps(root, "acme")
    assert len(items) == 1
    assert items[0]["icp_id"] == "legacy_headhunt"
    assert items[0]["display_name"] == "Legacy Headhunt"


def test_extract_and_migrate_campaign(tmp_path: Path):
    root = tmp_path
    legacy = root / "campaigns" / "acme_outreach"
    legacy.mkdir(parents=True)
    (legacy / "campaign.json").write_text(
        json.dumps(
            {
                "client_id": "acme",
                "client_name": "Acme",
                "display_name": "Headhunting US 1-50",
                "campaign_type": "company_outreach",
                "brief": "COOs",
            }
        ),
        encoding="utf-8",
    )
    (legacy / "icp.json").write_text(
        json.dumps({"job_titles": ["COO"], "campaign_id": "acme_outreach"}),
        encoding="utf-8",
    )
    (legacy / "stages").mkdir()
    (legacy / "stages" / "01_raw_seeds.csv").write_text("company_name\nAcme\n", encoding="utf-8")

    icp_id = extract_icp_from_campaign(root, legacy)
    assert icp_id
    assert list_client_icps(root, "acme")
    assert (
        root / "clients" / "acme" / "company_outreach" / "icps" / icp_id / "icp.json"
    ).is_file()

    dest = migrate_campaign_under_client(root, legacy)
    assert dest == root / "clients" / "acme" / "company_outreach" / "campaigns" / "acme_outreach"
    assert (dest / "stages" / "01_raw_seeds.csv").is_file()
    meta = json.loads((dest / "campaign.json").read_text(encoding="utf-8"))
    assert meta["icp_id"] == icp_id
    resolved = resolve_campaign_dir(root, "acme_outreach")
    assert resolved is not None
    assert resolved.resolve() == dest.resolve()


def test_resolve_prefers_stages_over_config_mirror(tmp_path: Path):
    root = tmp_path
    live = root / "campaigns" / "acme_outreach"
    live.mkdir(parents=True)
    (live / "campaign.json").write_text(
        json.dumps({"client_id": "acme", "campaign_type": "company_outreach"}),
        encoding="utf-8",
    )
    (live / "stages").mkdir()
    (live / "stages" / "01_raw_seeds.csv").write_text("company_name\nX\n", encoding="utf-8")

    mirror = root / "clients" / "acme" / "company_outreach" / "campaigns" / "acme_outreach"
    mirror.mkdir(parents=True)
    (mirror / "campaign.json").write_text(
        json.dumps({"client_id": "acme", "campaign_type": "company_outreach"}),
        encoding="utf-8",
    )

    resolved = resolve_campaign_dir(root, "acme_outreach")
    assert resolved is not None
    assert resolved.resolve() == live.resolve()


def test_unique_icp_id_skips_legacy(tmp_path: Path):
    from agent.utils.client_store import unique_icp_id

    root = tmp_path
    legacy = root / "clients" / "acme" / "icps" / "headhunting_us"
    legacy.mkdir(parents=True)
    (legacy / "icp.json").write_text("{}", encoding="utf-8")
    (legacy / "meta.json").write_text(
        json.dumps({"brief": "keep me", "campaign_type": "company_outreach"}),
        encoding="utf-8",
    )
    new_id = unique_icp_id(root, "acme", "headhunting_us", campaign_type="company_outreach")
    assert new_id != "headhunting_us"
    save_icp(
        root,
        "acme",
        new_id,
        icp_data={"job_titles": ["COO"]},
        display_name="New",
        brief="new brief",
        campaign_type="company_outreach",
        create_new=True,
    )
    meta = json.loads((legacy / "meta.json").read_text(encoding="utf-8"))
    assert meta["brief"] == "keep me"


def test_sync_and_rename_under_client_folder(tmp_path: Path):
    data = tmp_path / "data"
    camp = data / "campaigns" / "demo_outreach"
    camp.mkdir(parents=True)
    (camp / "campaign.json").write_text(
        json.dumps(
            {
                "client_id": "acme",
                "client_name": "Acme",
                "display_name": "Acme",
                "name": "Acme",
                "campaign_type": "company_outreach",
            }
        ),
        encoding="utf-8",
    )
    (camp / "icp.json").write_text(json.dumps({"campaign_id": "demo_outreach"}), encoding="utf-8")
    (camp / "seed_sources.json").write_text(
        json.dumps({"campaign_id": "demo_outreach", "sources": []}), encoding="utf-8"
    )

    dest = sync_campaign_config_to_client(camp)
    assert dest == data / "clients" / "acme" / "company_outreach" / "campaigns" / "demo_outreach"
    assert (dest / "icp.json").is_file()

    meta = set_campaign_display_name(camp, "Headhunting US 1-50")
    assert meta["display_name"] == "Headhunting US 1-50"
    mirrored = json.loads((dest / "campaign.json").read_text(encoding="utf-8"))
    assert mirrored["display_name"] == "Headhunting US 1-50"
