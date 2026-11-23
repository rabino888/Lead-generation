"""Autopilot gates. Fictional companies only. No network and no outreach repo."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.autopilot.assistant import brief_companies, brief_sequence
from agent.autopilot.gates import gate_contacts
from agent.autopilot.orchestrator import approve, reject, start
from agent.autopilot.outreach_bridge import build_commands
from agent.models import Contact
from agent.utils.apollo_contact_gate import apollo_contactable

PAGE = """
<html><head><title>Northline Labs | Workflow software</title>
<meta name="description" content="B2B software for operations teams in the United States.">
</head><body>
<p>Northline Labs sells workflow software to operations teams. Customers use the platform
to track jobs. We sell to companies in the United States.</p>
</body></html>
"""

COMPANY = {
    "company_name": "Pineyard Systems",
    "website": "https://pineyard.example",
    "industry": "software",
    "location": "United States",
    "company_size": "40",
}


def _fetch(url: str):
    return PAGE.encode(), "text/html"


RICH = {
    **COMPANY,
    "decision_maker_email": "ada@pineyard.example",
    "decision_maker_linkedin": "https://www.linkedin.com/in/ada-pineyard",
    "decision_maker_linkedin_headline": "Founder at Pineyard",
    "decision_maker_linkedin_post_summary": "Hiring operators this quarter.",
    "company_is_hiring": "yes",
    "website_summary": "Workflow software for operations teams.",
}


def _rows(ctx, *, rich: bool = False):
    return {"ok": True, "rows": [dict(RICH if rich else COMPANY)], "dropped": [], "usd": 0.0}


def _runners(calls: list[str]):
    def wrap(name):
        def _run(ctx):
            calls.append(name)
            if name == "outreach":
                return {
                    "ok": True,
                    "rows": [],
                    "qa_pass": 1,
                    "needs_review": 0,
                    "failed": 0,
                    "reasons": [],
                    "usd": 0.0,
                    "commands": [["generate", "--dry-run"]],
                }
            return _rows(ctx, rich=name in {"contacts", "enrich"})
        return _run

    return {name: wrap(name) for name in ("seed", "icp_match", "dedupe", "contacts", "enrich", "outreach")}


def test_plan_stops_before_spend_and_explains_three_gates(tmp_path):
    state = start("https://northline.example", data_root=tmp_path, fetch=_fetch, max_usd=25)
    assert state["phase"] == "awaiting_plan_approval"
    assert state["assistant"]["gate"] == "plan"
    assert "Companies after ICP match" in state["plan"]["markdown"]
    assert state["plan"]["outreach_strategy"]["optional"] is True
    assert state["plan"]["seed_source"]["id"] == "google_search"
    assert state["generate_sequence"] is False
    assert (tmp_path / "campaigns" / state["campaign_id"] / "icp.json").is_file()


def test_three_gates_and_optional_sequence(tmp_path):
    calls: list[str] = []
    runners = _runners(calls)
    state = start("https://northline.example", data_root=tmp_path, fetch=_fetch, max_usd=25)
    run_id = state["run_id"]

    state = approve(run_id, data_root=tmp_path, gate="plan", runners=runners)
    assert state["phase"] == "awaiting_company_approval"
    assert state["assistant"]["gate"] == "companies"
    assert calls == ["seed", "icp_match", "dedupe"]
    assert any(row.get("company_name") == "Pineyard Systems" for row in state["assistant"]["sample"])

    state = approve(run_id, data_root=tmp_path, gate="companies", runners=runners)
    assert state["phase"] == "awaiting_sequence_approval"
    assert state["assistant"]["gate"] == "sequence"
    assert state["assistant"]["skip_label"] == "Skip sequence"
    assert calls == ["seed", "icp_match", "dedupe", "contacts", "enrich"]

    state = approve(run_id, data_root=tmp_path, gate="sequence", generate_sequence=False, runners=runners)
    assert state["phase"] == "completed"
    assert state["sequence_skipped"] is True
    assert "outreach" not in calls


def test_sequence_runs_only_when_requested(tmp_path):
    calls: list[str] = []
    runners = _runners(calls)
    state = start("https://northline.example", data_root=tmp_path, fetch=_fetch, max_usd=25)
    run_id = state["run_id"]
    approve(run_id, data_root=tmp_path, gate="plan", runners=runners)
    approve(run_id, data_root=tmp_path, gate="companies", runners=runners)
    state = approve(
        run_id,
        data_root=tmp_path,
        gate="sequence",
        generate_sequence=True,
        runners=runners,
    )
    assert state["phase"] == "completed"
    assert calls[-1] == "outreach"
    assert state["sequence_skipped"] is False


def test_empty_seed_retries_then_stops_without_relaxing_the_checker(tmp_path, monkeypatch):
    monkeypatch.delenv("APOLLO_ALLOW_MISSING_DM_LINKEDIN", raising=False)
    seen = {"n": 0}

    def seed(ctx):
        seen["n"] += 1
        if seen["n"] == 1:
            return {"ok": True, "rows": [], "usd": 0.0}
        return _rows(ctx, rich=True)

    runners = _runners([])
    runners["seed"] = seed
    state = start("https://northline.example", data_root=tmp_path, fetch=_fetch, max_usd=25, max_attempts=3)
    state = approve(state["run_id"], data_root=tmp_path, gate="plan", runners=runners)
    assert seen["n"] >= 2
    assert state["phase"] == "awaiting_company_approval"
    assert any(entry.get("event") == "adjusted" for entry in state["log"])
    ok, reason = apollo_contactable(Contact(email="ada@northline.example", linkedin_url=""))
    assert ok is False
    assert "LinkedIn" in reason
    failed = gate_contacts(
        [{"decision_maker_email": "ada@northline.example", "decision_maker_linkedin": ""}],
        {"min_contactable": 1},
    )
    assert failed["ok"] is False


def test_cost_cap_stops_before_the_seed_runner(tmp_path):
    def boom(ctx):
        raise AssertionError("seed runner should not be called")

    runners = _runners([])
    runners["seed"] = boom
    state = start("https://northline.example", data_root=tmp_path, fetch=_fetch, max_usd=0.01)
    state = approve(state["run_id"], data_root=tmp_path, gate="plan", runners=runners)
    assert state["phase"] == "halted"
    assert state["halt_reason"] == "cost_cap"
    assert state["assistant"]["gate"] == "halted"


def test_revise_notes_rebuilds_the_plan(tmp_path):
    state = start("https://northline.example", data_root=tmp_path, fetch=_fetch)
    state = reject(state["run_id"], "Only buyers in Spain.", data_root=tmp_path, rerun=False)
    assert state["phase"] == "awaiting_plan_approval"
    assert state["revision"] == 2
    assert "Spain" in (state.get("research") or {}).get("operator_notes", "")
    assert state["assistant"]["source"] == "deterministic"


def test_outreach_commands_never_deliver(tmp_path: Path):
    commands = build_commands(
        client_id="northline",
        campaign_id="northline_autopilot",
        csv_path=str(tmp_path / "leads.csv"),
        payload_path=str(tmp_path / "run_payload.json"),
        outreach_root=tmp_path / "outreach",
    )
    flat = [" ".join(part) for part in commands]
    assert any("generate" in line and "--dry-run" in line for line in flat)
    assert all("deliver" not in line for line in flat)


def test_company_and_sequence_notes_are_distinct():
    plan = {"icp": {"industries": ["software"]}, "contact_provider": "apollo"}
    companies = brief_companies(plan, [COMPANY])
    sequence = brief_sequence(plan, [{**COMPANY, "decision_maker_email": "a@b.example", "decision_maker_linkedin": "https://linkedin.example/in/ada"}])
    assert companies["gate"] == "companies"
    assert sequence["gate"] == "sequence"
    assert sequence["skip_label"] == "Skip sequence"
    assert sequence["sample"][0].get("has_email") is True
    assert "decision_maker_email" not in sequence["sample"][0]


def test_http_plan_and_company_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    monkeypatch.setattr("agent.utils.client_intake.fetch_url_bytes", lambda url: (PAGE.encode(), "text/html"))
    from agent.dashboard.autopilot_routes import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    created = client.post("/autopilot/api/runs", json={"url": "https://northline.example", "max_usd": 25})
    assert created.status_code == 200, created.text
    run = created.json()["run"]
    assert run["phase"] == "awaiting_plan_approval"
    assert run["assistant"]["gate"] == "plan"
    page = client.get("/autopilot")
    assert page.status_code == 200
    assert "Approve three times" in page.text or "approve three times" in page.text.lower()
    assert "Skip sequence" in page.text

    calls: list[str] = []

    def _approve_with_fakes(run_id, **kwargs):
        kwargs["runners"] = _runners(calls)
        kwargs["data_root"] = tmp_path
        return approve(run_id, **kwargs)

    import agent.autopilot.orchestrator as orch

    monkeypatch.setattr(orch, "approve", _approve_with_fakes)
    approved = client.post(f"/autopilot/api/runs/{run['run_id']}/approve", json={"confirm": True, "gate": "plan"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["run"]["phase"] == "awaiting_company_approval"
    assert calls == ["seed", "icp_match", "dedupe"]


def test_local_page_chooses_maps(tmp_path):
    html = "<html><title>Harbor Cafe</title><body><p>Opening hours and our menu. Visit us downtown.</p></body></html>"

    def fetch(url: str):
        return html.encode(), "text/html"

    state = start("https://harbor-cafe.example", data_root=tmp_path, fetch=fetch)
    assert state["plan"]["seed_source"]["id"] == "google_maps"
    assert state["plan"]["page_kind"] == "local"
