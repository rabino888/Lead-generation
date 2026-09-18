"""Talent T02: person LinkedIn URL dedupe."""
from __future__ import annotations

import csv
from pathlib import Path

from agent.utils.talent_dedupe import (
    REJECT_DUP_LINKEDIN,
    REJECT_MISSING,
    REJECT_SHEETS,
    dedupe_talent_people,
    persist_t02,
    run_talent_dedupe,
    talent_contact_keys,
)
from agent.utils.talent_people import (
    STAGE_T01_RAW_PEOPLE,
    STAGE_T02_DEDUPED,
    STAGE_T02_DEDUPE_REJECTED,
    T01_FIELDS,
    read_people_csv,
)


def _write_t01_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "person_id": "p-1",
            "linkedin_url": "https://www.linkedin.com/in/Ada-Lovelace/?utm=1",
            "full_name": "Ada Lovelace",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "headline": "Engineer",
            "title": "Engineer",
            "location": "London",
            "company_name": "Analytical",
            "company_linkedin_url": "",
            "source_url": "",
            "seed_source_id": "people_csv_ingest",
            "notes": "first",
        },
        {
            "person_id": "p-1-dup",
            "linkedin_url": "linkedin.com/in/Ada-Lovelace/",
            "full_name": "Ada Dup",
            "first_name": "Ada",
            "last_name": "Dup",
            "headline": "",
            "title": "",
            "location": "",
            "company_name": "",
            "company_linkedin_url": "",
            "source_url": "",
            "seed_source_id": "people_csv_ingest",
            "notes": "dupe form",
        },
        {
            "person_id": "p-2",
            "linkedin_url": "https://www.linkedin.com/in/grace-hopper",
            "full_name": "Grace Hopper",
            "first_name": "Grace",
            "last_name": "Hopper",
            "headline": "Admiral",
            "title": "Admiral",
            "location": "US",
            "company_name": "Navy",
            "company_linkedin_url": "",
            "source_url": "",
            "seed_source_id": "people_csv_ingest",
            "notes": "",
        },
        {
            "person_id": "p-bad",
            "linkedin_url": "https://www.linkedin.com/company/acme",
            "full_name": "No Person",
            "first_name": "",
            "last_name": "",
            "headline": "",
            "title": "",
            "location": "",
            "company_name": "",
            "company_linkedin_url": "",
            "source_url": "",
            "seed_source_id": "people_csv_ingest",
            "notes": "company url",
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=T01_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_talent_contact_keys_linkedin_primary():
    keys = talent_contact_keys(
        {
            "linkedin_url": "https://www.linkedin.com/in/Foo/?x=1",
            "email": "a@b.com",
            "apollo_person_id": "abc",
        }
    )
    assert keys[0] == "linkedin:https://www.linkedin.com/in/foo"
    assert "apollo:abc" in keys
    assert "email:a@b.com" in keys


def test_dedupe_by_normalized_linkedin():
    rows = [
        {"linkedin_url": "https://www.linkedin.com/in/Jane-Doe/?utm=1", "full_name": "Jane"},
        {"linkedin_url": "linkedin.com/in/Jane-Doe/", "full_name": "Jane Dup"},
        {"linkedin_url": "https://www.linkedin.com/in/other", "full_name": "Other"},
        {"linkedin_url": "https://www.linkedin.com/company/x", "full_name": "Bad"},
    ]
    kept, rejected = dedupe_talent_people(rows)
    assert len(kept) == 2
    assert kept[0]["linkedin_url"] == "https://www.linkedin.com/in/Jane-Doe"
    assert kept[0]["full_name"] == "Jane"
    assert {r["reject_reason"] for r in rejected} == {REJECT_DUP_LINKEDIN, REJECT_MISSING}


def test_sheets_contact_dedup_optional():
    rows = [
        {"linkedin_url": "https://www.linkedin.com/in/seen-person", "full_name": "Seen"},
        {"linkedin_url": "https://www.linkedin.com/in/fresh", "full_name": "Fresh"},
    ]
    seen = {"linkedin:https://www.linkedin.com/in/seen-person"}
    kept, rejected = dedupe_talent_people(rows, seen_contact_keys=seen)
    assert len(kept) == 1
    assert kept[0]["full_name"] == "Fresh"
    assert len(rejected) == 1
    assert rejected[0]["reject_reason"].startswith(REJECT_SHEETS)


def test_run_talent_dedupe_fixture_csv(tmp_path: Path):
    camp = tmp_path / "talent_camp"
    stages = camp / "stages"
    _write_t01_fixture(stages / STAGE_T01_RAW_PEOPLE)

    kept_path, rejected_path, n_kept, n_rej = run_talent_dedupe(camp)
    assert kept_path.name == STAGE_T02_DEDUPED
    assert rejected_path.name == STAGE_T02_DEDUPE_REJECTED
    assert n_kept == 2
    assert n_rej == 2

    kept = read_people_csv(kept_path)
    assert {r["linkedin_url"] for r in kept} == {
        "https://www.linkedin.com/in/Ada-Lovelace",
        "https://www.linkedin.com/in/grace-hopper",
    }
    # first Ada wins
    ada = next(r for r in kept if "Ada" in r["linkedin_url"])
    assert ada["full_name"] == "Ada Lovelace"

    with rejected_path.open(encoding="utf-8-sig", newline="") as f:
        rejected = list(csv.DictReader(f))
    reasons = {r["reject_reason"] for r in rejected}
    assert REJECT_DUP_LINKEDIN in reasons
    assert REJECT_MISSING in reasons


def test_persist_t02_empty_rejected(tmp_path: Path):
    kept = [
        {
            "person_id": "p-1",
            "linkedin_url": "https://www.linkedin.com/in/only",
            "full_name": "Only",
            "first_name": "Only",
            "last_name": "",
            "headline": "",
            "title": "",
            "location": "",
            "company_name": "",
            "company_linkedin_url": "",
            "source_url": "",
            "seed_source_id": "",
            "notes": "",
        }
    ]
    k_path, r_path = persist_t02(tmp_path, kept, [])
    assert k_path.is_file()
    assert r_path.is_file()
    assert len(read_people_csv(k_path)) == 1
    with r_path.open(encoding="utf-8-sig", newline="") as f:
        assert list(csv.DictReader(f)) == []
