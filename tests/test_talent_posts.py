"""Talent post-Apollo LinkedIn posts enrich."""
from __future__ import annotations

import csv
from pathlib import Path

from agent.utils.talent_people import (
    STAGE_T04_PROFILES,
    STAGE_T05_APOLLO_CONTACTABLE,
    talent_stage_path,
)
from agent.utils.talent_profile import enrich_contactable_posts


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_enrich_contactable_posts_only_missing(tmp_path: Path):
    camp = tmp_path / "camp"
    stages = camp / "stages"
    stages.mkdir(parents=True)
    t05 = [
        {
            "person_id": "p1",
            "full_name": "Has Posts",
            "linkedin_url": "https://www.linkedin.com/in/has-posts",
            "linkedin_post_summary": "Already there",
            "linkedin_recent_posts": "old",
            "email": "a@x.com",
        },
        {
            "person_id": "p2",
            "full_name": "Needs Posts",
            "linkedin_url": "https://www.linkedin.com/in/needs-posts",
            "linkedin_post_summary": "",
            "linkedin_recent_posts": "",
            "email": "b@x.com",
        },
    ]
    t04 = [{**r, "email": ""} for r in t05]
    _write_csv(talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE), t05)
    _write_csv(talent_stage_path(camp, STAGE_T04_PROFILES), t04)

    calls: list[str] = []

    def fake_posts(url: str) -> dict:
        calls.append(url)
        return {
            "posts": [{"text": "Looking at Marbella listings this week"}],
            "interests": ["real estate", "Marbella"],
            "summary": "Recent LinkedIn themes: real estate, Marbella.",
        }

    enriched, empty = enrich_contactable_posts(camp, fetch_posts=fake_posts)
    assert enriched == 1
    assert empty == 0
    assert len(calls) == 1
    assert "needs-posts" in calls[0]

    with talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE).open(
        encoding="utf-8", newline=""
    ) as fh:
        rows = {r["person_id"]: r for r in csv.DictReader(fh)}
    assert rows["p1"]["linkedin_post_summary"] == "Already there"
    assert "Marbella" in rows["p2"]["linkedin_post_summary"]
    assert "real estate" in rows["p2"]["linkedin_interests"]
    assert "Marbella listings" in rows["p2"]["linkedin_recent_posts"]
