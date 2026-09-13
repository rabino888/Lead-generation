"""Tests for enrichment plan + builder estimate/API."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.utils.enrichment_plan import (
    default_plan,
    estimate_plan,
    load_plan,
    normalize_plan,
    save_plan,
    validate_plan,
)


def test_essential_modules_cannot_be_off():
    plan = default_plan("x")
    plan["modules"]["icp_match"] = False
    plan = normalize_plan(plan, campaign_id="x")
    assert plan["modules"]["icp_match"] is True
    assert validate_plan(plan) == []


def test_company_estimate_scales_with_leads():
    plan = default_plan("co", campaign_type="company_outreach")
    plan["target_leads"] = 10
    a = estimate_plan(plan)
    plan["target_leads"] = 20
    b = estimate_plan(plan)
    assert a["usd_run_total"] > 0
    assert abs(b["usd_run_total"] - 2 * a["usd_run_total"]) < 0.02
    assert a["confidence"] == "est"
    ids = {x["module_id"] for x in a["line_items"]}
    assert "apollo_email" in ids
    assert "website_scrape" in ids


def test_talent_estimate_profile_on_all_seeds():
    plan = default_plan("ta", campaign_type="talent_search")
    plan["seed_count"] = 100
    plan["talent_pass_rates"] = {
        "after_dedupe": 0.9,
        "after_icp": 0.4,
        "email_success": 0.7,
    }
    est = estimate_plan(plan)
    profile = next(x for x in est["line_items"] if x["module_id"] == "talent_profile")
    assert profile["units"] == 100
    assert est["funnel"]["seeds"] == 100
    assert abs(est["funnel"]["after_icp"] - 36.0) < 0.01  # 100*0.9*0.4
    assert est["campaign_type"] == "talent_search"


def test_save_and_load_plan(tmp_path: Path):
    camp = tmp_path / "demo"
    camp.mkdir()
    plan = default_plan("demo")
    plan["target_leads"] = 33
    plan["modules"]["apollo_phone"] = True
    saved = save_plan(camp, plan)
    assert (camp / "enrichment_plan.json").is_file()
    loaded = load_plan(camp, "demo")
    assert loaded["target_leads"] == 33
    assert loaded["modules"]["apollo_phone"] is True
    assert loaded["updated_at_utc"] == saved["updated_at_utc"]


@pytest.fixture
def builder_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    camp = tmp_path / "campaigns" / "sample_co"
    camp.mkdir(parents=True)
    (camp / "icp.json").write_text(
        json.dumps(
            {
                "job_titles": ["CEO", "CTO"],
                "contact_locations": ["United States"],
                "company_size_min": 1,
                "company_size_max": 50,
            }
        ),
        encoding="utf-8",
    )
    (camp / "campaign.json").write_text(
        json.dumps({"name": "Sample CO", "client_id": "sample", "client_name": "Sample"}),
        encoding="utf-8",
    )
    from agent.dashboard.builder_routes import router
    from agent.dashboard.static_mount import mount_portal_static

    app = FastAPI()
    app.include_router(router)
    mount_portal_static(app)
    return TestClient(app)


def test_builder_list_and_get(builder_client):
    r = builder_client.get("/builder/api/campaigns")
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["campaigns"][0]["campaign_id"] == "sample_co"
    g = builder_client.get("/builder/api/campaigns/sample_co")
    assert g.status_code == 200
    assert g.json()["display_name"] == "Sample CO"
    assert "disk_path" in g.json()
    assert g.json()["disk_path"].replace("\\", "/").endswith("campaigns/sample_co")
    assert g.json()["plan"]["campaign_type"] == "company_outreach"


def test_builder_estimate_and_save(builder_client, tmp_path):
    plan = default_plan("sample_co")
    plan["target_leads"] = 5
    est = builder_client.post(
        "/builder/api/campaigns/sample_co/estimate",
        json={"plan": plan},
    )
    assert est.status_code == 200
    assert est.json()["usd_run_total"] > 0

    plan["modules"]["apollo_phone"] = True
    put = builder_client.put(
        "/builder/api/campaigns/sample_co/plan",
        json={"plan": plan},
    )
    assert put.status_code == 200
    path = tmp_path / "campaigns" / "sample_co" / "enrichment_plan.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["modules"]["apollo_phone"] is True
    assert data["modules"]["icp_match"] is True


def test_builder_ui_served(builder_client):
    bare = builder_client.get("/builder", follow_redirects=False)
    assert bare.status_code in (302, 307)
    assert "/dashboard" in bare.headers.get("location", "")

    client_only = builder_client.get("/builder?client=tbomedia", follow_redirects=False)
    assert client_only.status_code in (302, 307)
    assert "/dashboard/clients/tbomedia" in client_only.headers.get("location", "")

    r = builder_client.get("/builder?new=1")
    assert r.status_code == 200
    assert "Build new campaign" in r.text
    assert "create-icp-mode" in r.text
    assert "/builder/api/clients/" in r.text
    assert "create-icp-source" in r.text
    assert "create-label" in r.text
    assert "Use existing ICP" in r.text
    assert "Create new ICP" in r.text
    assert "Enrichment modules for this campaign" in r.text
    assert "Select a client" not in r.text
    assert "board-ctype" not in r.text
    assert "seg-co" not in r.text
    assert "ICP + seed setup" in r.text
    assert "setup-brief-block" in r.text
    assert "setup-brief-edit" in r.text
    assert "btn-save-brief" in r.text
    assert "Client brief" in r.text
    assert "enrich-prompt" in r.text
    assert "Would you like to configure the enrichment modules now?" in r.text
    assert "btn-continue-modules" in r.text
    assert "Continue to enrichment modules" in r.text
    assert "Industry (one per line)" in r.text
    assert "btn-copy-icp-prompt" in r.text
    assert "btn-reload-icp" in r.text
    assert "ICP in Cursor" in r.text or "Plan ICP with Cursor" in r.text
    assert "Your ICP has been created" in r.text
    assert "setup-note-path" in r.text
    assert "personalised AI" in r.text or "personalized AI" in r.text
    assert "automata_us_rnd/icp.json" in r.text
    assert "showModules: false" in r.text
    assert "showEnrichPrompt" in r.text
    assert "local text heuristics" in r.text or "Prefills the form locally" in r.text or "Cursor planning" in r.text
    assert "formatApiDetail" in r.text
    assert "WEBSITE_PROVIDERS" not in r.text
    assert "openai.ChatCompletion" not in r.text
    assert "/static/portal.css" in r.text
    assert "Create new client" in r.text or "Use existing client" in r.text


def test_builder_create_includes_brief_on_summary(builder_client, tmp_path):
    r = builder_client.post(
        "/builder/api/campaigns",
        json={
            "client_id": "sample",
            "client_name": "Sample Co",
            "label": "Spain TA 50-200",
            "brief": "Spain TA managers · 50–200 · Madrid",
            "campaign_type": "company_outreach",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()["campaign"]
    campaign_id = body["campaign_id"]
    assert body["display_name"] == "Spain TA 50-200"
    assert "spain_ta_50_200" in campaign_id
    g = builder_client.get(f"/builder/api/campaigns/{campaign_id}")
    assert g.status_code == 200
    assert g.json()["brief"] == "Spain TA managers · 50–200 · Madrid"
    assert (tmp_path / "clients" / "sample" / "campaigns" / campaign_id / "icp.json").is_file()


def test_builder_meta_updates_brief(builder_client, tmp_path):
    created = builder_client.post(
        "/builder/api/campaigns",
        json={
            "client_id": "sample",
            "client_name": "Sample Co",
            "label": "Brief edit list",
            "brief": "Old brief",
            "campaign_type": "company_outreach",
        },
    )
    assert created.status_code == 200, created.text
    campaign_id = created.json()["campaign"]["campaign_id"]
    r = builder_client.put(
        f"/builder/api/campaigns/{campaign_id}/meta",
        json={"display_name": "Brief edit list", "brief": "Updated client brief"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["campaign"]["brief"] == "Updated client brief"
    meta = json.loads(
        (tmp_path / "campaigns" / campaign_id / "campaign.json").read_text(encoding="utf-8")
    )
    assert meta["brief"] == "Updated client brief"


def test_builder_create_prefills_icp_from_brief(builder_client, tmp_path):
    brief = (
        "COOs, CTOs, marketing directors of marketing companies "
        "in USA between 1 and 50 employees."
    )
    r = builder_client.post(
        "/builder/api/campaigns",
        json={
            "client_id": "mkt",
            "client_name": "Mkt Co",
            "brief": brief,
            "campaign_type": "company_outreach",
        },
    )
    assert r.status_code == 200, r.text
    campaign_id = r.json()["campaign"]["campaign_id"]
    saved = json.loads(
        (tmp_path / "clients" / "mkt" / "campaigns" / campaign_id / "icp.json").read_text(
            encoding="utf-8"
        )
    )
    assert "COO" in saved["job_titles"]
    assert "CTO" in saved["job_titles"]
    assert "Marketing Director" in saved["job_titles"]
    assert saved["contact_locations"] == ["United States"]
    assert saved["company_size_min"] == 1
    assert saved["company_size_max"] == 50
    assert any("marketing" in (x or "").lower() for x in (saved.get("industries") or []))
    summary_icp = r.json()["campaign"]["icp"]
    assert summary_icp["company_size_min"] == 1
    assert summary_icp["company_size_max"] == 50
    assert "COO" in summary_icp["job_titles"]
    mirror = tmp_path / "clients" / "mkt" / "icps"
    assert mirror.is_dir()
    assert any(mirror.iterdir())


def test_builder_rename_meta(builder_client, tmp_path):
    r = builder_client.put(
        "/builder/api/campaigns/sample_co/meta",
        json={"display_name": "Sample renamed ICP"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["campaign"]["display_name"] == "Sample renamed ICP"
    meta = json.loads(
        (tmp_path / "campaigns" / "sample_co" / "campaign.json").read_text(encoding="utf-8")
    )
    assert meta["display_name"] == "Sample renamed ICP"
    mirror = tmp_path / "clients" / "sample" / "campaigns" / "sample_co" / "campaign.json"
    assert mirror.is_file()
    assert json.loads(mirror.read_text(encoding="utf-8"))["display_name"] == "Sample renamed ICP"


def test_builder_create_campaign(builder_client, tmp_path):
    r = builder_client.post(
        "/builder/api/campaigns",
        json={
            "client_id": "sample",
            "client_name": "Sample Co",
            "brief": "Spain TA managers",
            "campaign_type": "company_outreach",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    campaign_id = body["campaign"]["campaign_id"]
    assert campaign_id == "sample_outreach"
    assert body["campaign"]["client_id"] == "sample"
    assert body.get("icp_id")
    camp = tmp_path / "clients" / "sample" / "campaigns" / campaign_id
    assert (camp / "campaign.json").is_file()
    assert (camp / "enrichment_plan.json").is_file()
    assert (camp / "icp.json").is_file()
    meta = json.loads((camp / "campaign.json").read_text(encoding="utf-8"))
    assert meta["brief"] == "Spain TA managers"
    assert meta["name"] == "Sample Co"
    assert meta["icp_id"] == body["icp_id"]
    icp_lib = tmp_path / "clients" / "sample" / "icps" / body["icp_id"] / "icp.json"
    assert icp_lib.is_file()


def test_builder_list_client_icps(builder_client, tmp_path):
    created = builder_client.post(
        "/builder/api/campaigns",
        json={
            "client_id": "sample",
            "client_name": "Sample Co",
            "label": "Headhunting US 1-50",
            "brief": "COOs",
            "campaign_type": "company_outreach",
        },
    )
    assert created.status_code == 200, created.text
    icp_id = created.json()["icp_id"]
    r = builder_client.get("/builder/api/clients/sample/icps")
    assert r.status_code == 200, r.text
    ids = [x["icp_id"] for x in r.json()["icps"]]
    assert icp_id in ids
    assert any(x["display_name"] == "Headhunting US 1-50" for x in r.json()["icps"])

    g = builder_client.get(f"/builder/api/clients/sample/icps/{icp_id}")
    assert g.status_code == 200, g.text
    assert g.json()["icp_id"] == icp_id
    assert "data" in g.json()

    ren = builder_client.put(
        f"/builder/api/clients/sample/icps/{icp_id}/meta",
        json={"display_name": "Headhunting renamed"},
    )
    assert ren.status_code == 200, ren.text
    assert ren.json()["summary"]["display_name"] == "Headhunting renamed"


def test_builder_create_explicit_id_still_works(builder_client, tmp_path):
    r = builder_client.post(
        "/builder/api/campaigns",
        json={
            "campaign_id": "new_list_1",
            "client_id": "sample",
            "brief": "x",
            "campaign_type": "talent_search",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["campaign"]["campaign_id"] == "new_list_1"
    assert r.json()["campaign"]["campaign_type"] == "talent_search"


def test_builder_list_includes_client(builder_client):
    r = builder_client.get("/builder/api/campaigns")
    assert r.status_code == 200
    row = r.json()["campaigns"][0]
    assert row["client_id"] == "sample"
    assert "client_name" in row


def test_builder_icp_and_seeds(builder_client, tmp_path):
    camp = tmp_path / "campaigns" / "sample_co"
    (camp / "seed_sources.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "id": "csv_ingest",
                        "type": "csv_ingest",
                        "name": "CSV",
                        "enabled": True,
                        "config": {"csv_path": "seeds.csv"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    g = builder_client.get("/builder/api/campaigns/sample_co/icp")
    assert g.status_code == 200
    assert g.json()["help"]["no_llm"] is True
    assert "CEO" in g.json()["data"]["job_titles"]

    put = builder_client.put(
        "/builder/api/campaigns/sample_co/icp",
        json={
            "data": {
                "job_titles": ["VP Sales"],
                "contact_locations": ["Spain"],
                "company_size_min": 10,
                "company_size_max": 200,
            }
        },
    )
    assert put.status_code == 200
    saved = json.loads((camp / "icp.json").read_text(encoding="utf-8"))
    assert saved["job_titles"] == ["VP Sales"]
    assert saved["campaign_id"] == "sample_co"

    sg = builder_client.get("/builder/api/campaigns/sample_co/seed_sources")
    assert sg.status_code == 200
    assert sg.json()["data"]["sources"][0]["type"] == "csv_ingest"

    sp = builder_client.put(
        "/builder/api/campaigns/sample_co/seed_sources",
        json={
            "data": {
                "sources": [
                    {
                        "id": "csv_ingest",
                        "type": "csv_ingest",
                        "enabled": False,
                        "config": {"csv_path": "other.csv"},
                    }
                ]
            }
        },
    )
    assert sp.status_code == 200
    seeds = builder_client.get("/builder/api/campaigns/sample_co/seed_sources").json()["data"]
    assert seeds["sources"][0]["enabled"] is False


def test_builder_plan_locks_campaign_type(builder_client, tmp_path):
    plan = default_plan("sample_co", campaign_type="company_outreach")
    plan["target_leads"] = 10
    r = builder_client.put(
        "/builder/api/campaigns/sample_co/plan",
        json={"plan": {**plan, "campaign_type": "talent_search", "seed_count": 100}},
    )
    assert r.status_code == 200
    assert r.json()["plan"]["campaign_type"] == "company_outreach"
    saved = json.loads(
        (tmp_path / "campaigns" / "sample_co" / "enrichment_plan.json").read_text(encoding="utf-8")
    )
    assert saved["campaign_type"] == "company_outreach"

    e = builder_client.post(
        "/builder/api/campaigns/sample_co/estimate",
        json={"plan": {**plan, "campaign_type": "talent_search"}},
    )
    assert e.status_code == 200
    assert e.json()["campaign_type"] == "company_outreach"


def test_company_estimate_rates_are_realistic():
    """Defaults should track Automata-scale Apify truth (~$0.10/lead order), not $0.50+."""
    plan = default_plan("co", campaign_type="company_outreach")
    plan["target_leads"] = 50
    est = estimate_plan(plan)
    assert est["usd_per_lead"] < 0.25
    assert est["usd_run_total"] < 12.0
    profile = next(x for x in est["line_items"] if x["module_id"] == "apify_dm_profile")
    assert profile["unit_usd"] <= 0.02
