"""Talent T04: personal profile scrape (mocked Apify)."""
from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from agent.utils.talent_people import (
    STAGE_T03_ICP_MATCHED,
    STAGE_T04_PROFILE_FAILED,
    STAGE_T04_PROFILES,
    T01_FIELDS,
    write_people_csv,
)
from agent.utils.talent_profile import (
    PROFILE_FAILED,
    PROFILE_OK,
    enrich_people_rows,
    merge_profile_onto_row,
    profile_has_content,
    run_talent_profile_stage,
)


def _row(**kwargs) -> dict:
    base = {k: "" for k in T01_FIELDS}
    base.update(
        {
            "person_id": "p-1",
            "linkedin_url": "https://www.linkedin.com/in/ada",
            "full_name": "Ada Lovelace",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "headline": "Engineer",
            "title": "Engineer",
            "location": "London",
            "company_name": "Analytical",
        }
    )
    base.update(kwargs)
    return base


def test_profile_has_content():
    assert profile_has_content({"about": "hello"}) is True
    assert profile_has_content({"headline": "Eng"}) is True
    assert profile_has_content({}) is False
    assert profile_has_content(None) is False


def test_merge_ok_and_failed():
    ok = merge_profile_onto_row(
        _row(),
        {
            "about": "Mathematician",
            "headline": "Senior Engineer",
            "location": "Madrid, Spain",
            "experience_summary": "Engineer · Analytical",
            "education_summary": "Cambridge",
        },
        scraped_at_utc="2026-09-17T10:00:00+00:00",
    )
    assert ok["profile_status"] == PROFILE_OK
    assert ok["about"] == "Mathematician"
    assert ok["headline"] == "Senior Engineer"
    assert ok["location"] == "Madrid, Spain"
    assert ok["experience_summary"] == "Engineer · Analytical"
    assert ok["education_summary"] == "Cambridge"
    assert int(ok["profile_raw_chars"]) > 0
    assert ok["profile_scraped_at_utc"] == "2026-09-17T10:00:00+00:00"

    failed = merge_profile_onto_row(_row(), {}, scraped_at_utc="2026-09-17T10:00:00+00:00")
    assert failed["profile_status"] == PROFILE_FAILED
    assert failed["profile_raw_chars"] == "0"


def test_enrich_people_rows_mocks_fetcher():
    calls: list[str] = []

    def fake_fetch(url: str | None) -> dict:
        calls.append(url or "")
        if url and "ada" in url:
            return {"about": "bio", "headline": "Eng", "name": "Ada Lovelace"}
        return {}

    ok, failed = enrich_people_rows(
        [
            _row(person_id="p-1", linkedin_url="https://www.linkedin.com/in/ada"),
            _row(person_id="p-2", linkedin_url="https://www.linkedin.com/in/ghost"),
            _row(person_id="p-bad", linkedin_url=""),
        ],
        fetch_profile=fake_fetch,
    )
    assert len(ok) == 1
    assert ok[0]["person_id"] == "p-1"
    assert ok[0]["profile_status"] == PROFILE_OK
    assert len(failed) == 2
    assert {r["person_id"] for r in failed} == {"p-2", "p-bad"}
    # Missing LinkedIn never calls Apify
    assert all("ghost" in c or "ada" in c for c in calls)
    assert len(calls) == 2


def test_run_talent_profile_stage_writes_csvs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("APIFY_SKIP_LINKEDIN_POSTS", raising=False)
    stages = tmp_path / "stages"
    stages.mkdir()
    write_people_csv(
        stages / STAGE_T03_ICP_MATCHED,
        [
            _row(person_id="p-ok", linkedin_url="https://www.linkedin.com/in/ok-person"),
            _row(person_id="p-fail", linkedin_url="https://www.linkedin.com/in/fail-person"),
        ],
    )

    def fake_fetch(url: str | None) -> dict:
        # Prove skip-posts env is set during scrape
        assert os.environ.get("APIFY_SKIP_LINKEDIN_POSTS") == "1"
        if url and "ok-person" in url:
            return {
                "about": "About text",
                "experience_summary": "Backend · Acme",
                "headline": "Backend Eng",
            }
        return {}

    ok_path, fail_path, n_ok, n_fail = run_talent_profile_stage(
        tmp_path,
        fetch_profile=fake_fetch,
    )
    assert n_ok == 1
    assert n_fail == 1
    assert ok_path.name == STAGE_T04_PROFILES
    assert fail_path.name == STAGE_T04_PROFILE_FAILED

    with ok_path.open(encoding="utf-8", newline="") as f:
        ok_rows = list(csv.DictReader(f))
    with fail_path.open(encoding="utf-8", newline="") as f:
        fail_rows = list(csv.DictReader(f))

    assert len(ok_rows) == 1
    assert ok_rows[0]["person_id"] == "p-ok"
    assert ok_rows[0]["profile_status"] == PROFILE_OK
    assert ok_rows[0]["about"] == "About text"
    assert ok_rows[0]["experience_summary"] == "Backend · Acme"

    assert len(fail_rows) == 1
    assert fail_rows[0]["person_id"] == "p-fail"
    assert fail_rows[0]["profile_status"] == PROFILE_FAILED

    # Env restored after stage so tests do not leak
    assert os.environ.get("APIFY_SKIP_LINKEDIN_POSTS") is None


def test_run_talent_profile_stage_limit(tmp_path: Path):
    stages = tmp_path / "stages"
    stages.mkdir()
    write_people_csv(
        stages / STAGE_T03_ICP_MATCHED,
        [
            _row(person_id=f"p-{i}", linkedin_url=f"https://www.linkedin.com/in/p{i}")
            for i in range(5)
        ],
    )
    calls: list[str] = []

    def fake_fetch(url: str | None) -> dict:
        calls.append(url or "")
        return {"about": "x", "headline": "y"}

    _, _, n_ok, n_fail = run_talent_profile_stage(
        tmp_path,
        fetch_profile=fake_fetch,
        limit=2,
    )
    assert n_ok == 2
    assert n_fail == 0
    assert len(calls) == 2


def test_missing_input_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        run_talent_profile_stage(tmp_path, fetch_profile=lambda _u: {})
