"""Tests for cost dashboard HTTP routes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    from agent.dashboard.routes import router
    from agent.dashboard.static_mount import mount_portal_static

    app = FastAPI()
    app.include_router(router)
    mount_portal_static(app)
    return TestClient(app)


def test_dashboard_serves_static_portal(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Lead List Ledger" in r.text or "ledger-data" in r.text
    assert "/static/portal.css" in r.text
    assert "Campaigns & ICPs" not in r.text
    assert "Total spend" in r.text
    assert "Where the money went" in r.text
    assert "Generated lists" in r.text
    assert "isGeneratedList" in r.text
    assert "generatedLists" in r.text
    assert "Build new campaign" in r.text
    assert "Associated campaigns" in r.text
    assert "icp-accordion" in r.text
    assert "renderLibraryIcps" in r.text
    assert "/builder/api/clients/" in r.text
    assert "icpFingerprint" not in r.text
    assert "Open campaign dashboard" not in r.text


def test_dashboard_api_returns_index(client, tmp_path):
    index = {"schema_version": "4", "campaigns": []}
    (tmp_path / "cost_dashboard_index.json").write_text(
        json.dumps(index), encoding="utf-8"
    )
    r = client.get("/dashboard/api")
    assert r.status_code == 200
    assert r.json()["schema_version"] == "4"


def test_dashboard_secret_required_when_set(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_SECRET", "test-secret")
    assert client.get("/dashboard").status_code == 401
    assert client.get("/dashboard?key=test-secret").status_code == 200


def test_dashboard_campaign_redirects_to_hash(client):
    r = client.get("/dashboard/campaigns/demo_list", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/dashboard#/campaign/demo_list"


def test_dashboard_leads_api(client, tmp_path):
    camp = tmp_path / "campaigns" / "demo"
    stages = camp / "stages"
    stages.mkdir(parents=True)
    (stages / "04_apollo_contactable.csv").write_text(
        "company_name,decision_maker_email\nAcme,a@x.com\n",
        encoding="utf-8",
    )
    (tmp_path / "cost_dashboard_index.json").write_text(
        '{"schema_version":"4","campaigns":[]}', encoding="utf-8"
    )
    r = client.get("/dashboard/api/campaigns/demo/leads")
    assert r.status_code == 200
    assert r.json()["total_rows"] == 1
    csv_r = client.get("/dashboard/api/campaigns/demo/leads.csv")
    assert csv_r.status_code == 200
    assert b"a@x.com" in csv_r.content
    assert (camp / "exports" / "04_apollo_contactable.csv").is_file()
