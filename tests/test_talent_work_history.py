"""Post-Apollo Apify work-history enrich on T05 contactables."""
from __future__ import annotations

import csv
from pathlib import Path

from agent.utils.talent_people import (
    STAGE_T04_PROFILES,
    STAGE_T05_APOLLO_CONTACTABLE,
    talent_stage_path,
)
from agent.utils.talent_profile import enrich_contactable_work_history


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_enrich_contactable_work_history_only_missing(tmp_path: Path):
    camp = tmp_path / "camp"
    stages = camp / "stages"
    stages.mkdir(parents=True)
    t05 = [
        {
            "person_id": "p1",
            "full_name": "Has Exp",
            "linkedin_url": "https://www.linkedin.com/in/has-exp",
            "experience_summary": "Already there",
            "current_position": "Advisor @ Co",
            "email": "a@x.com",
            "email_status": "verified",
        },
        {
            "person_id": "p2",
            "full_name": "Needs Exp",
            "linkedin_url": "https://www.linkedin.com/in/needs-exp",
            "experience_summary": "",
            "current_position": "Headline only",
            "email": "b@x.com",
            "email_status": "verified",
        },
    ]
    t04 = [
        {**t05[0], "email": "", "email_status": ""},
        {**t05[1], "email": "", "email_status": ""},
    ]
    _write_csv(talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE), t05)
    _write_csv(talent_stage_path(camp, STAGE_T04_PROFILES), t04)

    calls: list[str] = []

    def fake_apify(url: str) -> dict:
        calls.append(url)
        return {
            "experience_summary": "Role @ Firm (2020–present)",
            "current_position": "Role @ Firm",
            "about": "Hello",
            "headline": "Role @ Firm",
            "linkedin_url": url,
            "name": "Needs Exp",
        }

    enriched, missing = enrich_contactable_work_history(
        camp, fetch_apify_profile=fake_apify
    )
    assert enriched == 1
    assert missing == 0
    assert len(calls) == 1
    assert "needs-exp" in calls[0]

    with talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE).open(
        encoding="utf-8", newline=""
    ) as fh:
        rows = list(csv.DictReader(fh))
    by_id = {r["person_id"]: r for r in rows}
    assert by_id["p1"]["experience_summary"] == "Already there"
    assert "Role @ Firm" in by_id["p2"]["experience_summary"]
    assert by_id["p2"]["email"] == "b@x.com"
