"""Talent T06 phone: aliases, pending CSV, orchestrator gate (no live Apollo)."""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from unittest.mock import MagicMock

from agent.utils.talent_people import (
    STAGE_T05_APOLLO_CONTACTABLE,
    STAGE_T06_PHONE_ENRICHED,
    T01_FIELDS,
)
from agent.utils.talent_phone import (
    PHONE_STATUS_PENDING,
    PHONE_STATUS_REQUESTED,
    phone_prereqs_ok,
    prepare_t06_row,
    run_talent_phone_stage,
)
from scripts.request_apollo_phone_reveals import (
    _eligible_contacts,
    normalize_phone_contact_aliases,
)
from scripts.run_talent_campaign import talent_phone_gate


def _talent_row(**kwargs) -> dict[str, str]:
    row = {k: "" for k in T01_FIELDS}
    row.update(
        {
            "person_id": "p-1",
            "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
            "full_name": "Ada Lovelace",
            "title": "Engineer",
            "location": "Spain",
            "company_name": "Analytical Engines",
            "email": "ada@example.com",
            "apollo_person_id": "apoll-ada",
            "email_status": "verified",
            "apollo_reveal_status": "revealed",
        }
    )
    row.update(kwargs)
    return row


def test_normalize_phone_contact_aliases_maps_talent_columns():
    row = normalize_phone_contact_aliases(
        {
            "email": "ada@example.com",
            "apollo_person_id": "apoll-ada",
            "full_name": "Ada Lovelace",
            "person_id": "p-1",
            "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
            "company_name": "Analytical Engines",
        }
    )
    assert row["decision_maker_email"] == "ada@example.com"
    assert row["decision_maker_apollo_person_id"] == "apoll-ada"
    assert row["decision_maker_name"] == "Ada Lovelace"
    assert row["lead_id"] == "p-1"
    assert row["decision_maker_linkedin"] == "https://www.linkedin.com/in/ada-lovelace"


def test_eligible_contacts_accepts_talent_aliases():
    contacts = _eligible_contacts(
        [
            {
                "email": "ada@example.com",
                "apollo_person_id": "apoll-ada",
                "full_name": "Ada",
                "phone": "",
                "mobile_phone": "",
            }
        ],
        only_missing_phone=True,
    )
    assert len(contacts) == 1
    assert contacts[0]["decision_maker_email"] == "ada@example.com"
    assert contacts[0]["decision_maker_apollo_person_id"] == "apoll-ada"


def test_eligible_contacts_skips_when_talent_phone_present():
    contacts = _eligible_contacts(
        [
            {
                "email": "ada@example.com",
                "apollo_person_id": "apoll-ada",
                "phone": "+34111",
            }
        ],
        only_missing_phone=True,
    )
    assert contacts == []


def test_prepare_t06_row_empty_phones_pending():
    out = prepare_t06_row(_talent_row())
    assert out["phone"] == ""
    assert out["mobile_phone"] == ""
    assert out["phone_request_status"] == PHONE_STATUS_PENDING
    assert out["person_id"] == "p-1"
    assert out["email"] == "ada@example.com"
    assert out["full_name"] == "Ada Lovelace"


def test_phone_prereqs_ok_requires_key_and_webhook(monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    monkeypatch.setattr(
        "agent.utils.talent_phone.resolve_apollo_phone_webhook_url",
        lambda: None,
    )
    ok, err = phone_prereqs_ok()
    assert ok is False
    assert "APOLLO_API_KEY" in err

    ok2, err2 = phone_prereqs_ok(api_key="k", webhook_url=None)
    # webhook_url=None means "use resolver" which we stubbed to None
    assert ok2 is False
    assert "webhook" in err2.lower()

    ok3, _ = phone_prereqs_ok(api_key="k", webhook_url="https://example.com/hook")
    assert ok3 is True


def test_run_talent_phone_stage_writes_t06_and_calls_request(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    t05 = stages / STAGE_T05_APOLLO_CONTACTABLE
    row = _talent_row()
    with t05.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)

    calls: list[Path] = []

    def fake_request(csv_path, **_kwargs):
        calls.append(Path(csv_path))
        log = Path(csv_path).with_name(f"{Path(csv_path).stem}.phone_requests.jsonl")
        log.write_text(
            '{"status":"requested","email":"ada@example.com","csv_row":"2"}\n',
            encoding="utf-8",
        )
        return log, 1

    out, n_rows, n_req = run_talent_phone_stage(
        tmp_path,
        request_fn=fake_request,
        api_key="test-key",
        webhook_url="https://example.com/hook",
    )
    assert n_rows == 1
    assert n_req == 1
    assert out.name == STAGE_T06_PHONE_ENRICHED
    assert calls and calls[0].name == STAGE_T06_PHONE_ENRICHED

    with out.open(encoding="utf-8", newline="") as f:
        got = list(csv.DictReader(f))
    assert len(got) == 1
    assert got[0]["phone"] == ""
    assert got[0]["mobile_phone"] == ""
    assert got[0]["phone_request_status"] == PHONE_STATUS_REQUESTED
    assert got[0]["email"] == "ada@example.com"
    assert got[0]["person_id"] == "p-1"
    assert got[0]["linkedin_url"]
    assert got[0]["company_name"] == "Analytical Engines"


def test_talent_phone_gate_skip_plan_off():
    action, err = talent_phone_gate(
        {"campaign_type": "talent_search", "modules": {"apollo_phone": False}}
    )
    assert action == "skip_plan_off"
    assert err is None


def test_talent_phone_gate_skip_flag():
    action, err = talent_phone_gate(
        {"campaign_type": "talent_search", "modules": {"apollo_phone": True}},
        skip_phone=True,
    )
    assert action == "skip_flag"
    assert err is None


def test_talent_phone_gate_error_when_on_but_missing_prereqs(monkeypatch):
    monkeypatch.setattr(
        "scripts.run_talent_campaign.phone_prereqs_ok",
        lambda: (False, "APOLLO_API_KEY is not configured"),
    )
    action, err = talent_phone_gate(
        {"campaign_type": "talent_search", "modules": {"apollo_phone": True}}
    )
    assert action == "error"
    assert err is not None
    assert "APOLLO_API_KEY" in err


def test_orchestrator_skips_phone_when_plan_off(monkeypatch, tmp_path, capsys):
    campaign = MagicMock()
    campaign.campaign_id = "talent_demo"
    campaign.campaign_dir = tmp_path
    campaign.stages_dir = tmp_path / "stages"
    campaign.stages_dir.mkdir()
    (campaign.stages_dir / "T01_raw_people.csv").write_text(
        "person_id,linkedin_url\np1,https://www.linkedin.com/in/x\n",
        encoding="utf-8",
    )

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
        lambda _d, _cid: {
            "campaign_type": "talent_search",
            "modules": {"apollo_phone": False},
        },
    )

    stage_calls: list[str] = []

    def fake_run(label, stage_main, argv):
        stage_calls.append(label)
        return 0

    monkeypatch.setattr("scripts.run_talent_campaign._run_stage", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_talent_campaign.py", "--campaign", "talent_demo"],
    )

    from scripts.run_talent_campaign import main

    rc = main()
    assert rc == 0
    assert "stage_talent_apollo.py" in stage_calls
    assert "stage_talent_phone.py" not in stage_calls
    out = capsys.readouterr().out
    assert "Skipping T06 phone" in out
    assert "apollo_phone off" in out


def test_orchestrator_errors_when_phone_on_missing_webhook(monkeypatch, tmp_path, capsys):
    campaign = MagicMock()
    campaign.campaign_id = "talent_demo"
    campaign.campaign_dir = tmp_path
    campaign.stages_dir = tmp_path / "stages"
    campaign.stages_dir.mkdir()
    (campaign.stages_dir / "T01_raw_people.csv").write_text(
        "person_id,linkedin_url\np1,https://www.linkedin.com/in/x\n",
        encoding="utf-8",
    )

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
        lambda _d, _cid: {
            "campaign_type": "talent_search",
            "modules": {"apollo_phone": True},
        },
    )
    monkeypatch.setattr(
        "scripts.run_talent_campaign.phone_prereqs_ok",
        lambda: (False, "webhook not configured"),
    )
    monkeypatch.setattr(
        "scripts.run_talent_campaign._run_stage",
        lambda *_a, **_k: 0,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_talent_campaign.py", "--campaign", "talent_demo"],
    )

    from scripts.run_talent_campaign import main

    rc = main()
    assert rc == 2
    err = capsys.readouterr().err
    assert "webhook" in err.lower()


def test_import_merge_writes_talent_phone_columns():
    from scripts.import_apollo_phone_webhooks import _match_record, _merge_phone_records

    rows = [
        {
            "email": "ada@example.com",
            "apollo_person_id": "apoll-ada",
            "full_name": "Ada Lovelace",
            "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
            "phone": "",
            "mobile_phone": "",
        }
    ]
    records = [
        {
            "apollo_person_id": "apoll-ada",
            "email": "ada@example.com",
            "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
            "name": "Ada Lovelace",
            "direct_phone": "+34111",
            "mobile_phone": "+34222",
        }
    ]
    assert _match_record(rows[0], records) is not None
    updated = _merge_phone_records(rows, records)
    assert updated[0]["phone"] == "+34111"
    assert updated[0]["mobile_phone"] == "+34222"
    assert updated[0]["decision_maker_direct_phone"] == "+34111"
