"""Talent T01: LinkedIn person URL normalize + people CSV ingest."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from agent.utils.linkedin_people_seeds import (
    DEFAULT_PEOPLE_ACTOR,
    DEFAULT_PROFILE_SCRAPER_MODE,
    build_people_search_input,
    items_from_apify_people,
    load_people_from_csv,
    persist_t01,
    resolve_people_actor,
)
from agent.utils.talent_people import (
    STAGE_T01_RAW_PEOPLE,
    normalize_linkedin_person_url,
    person_id_from_linkedin,
    person_row_from_mapping,
    read_people_csv,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_normalize_linkedin_person_url():
    assert (
        normalize_linkedin_person_url("https://www.linkedin.com/in/Jane-Doe/?utm=1")
        == "https://www.linkedin.com/in/Jane-Doe"
    )
    assert (
        normalize_linkedin_person_url("linkedin.com/in/jane-doe/")
        == "https://www.linkedin.com/in/jane-doe"
    )
    assert normalize_linkedin_person_url("https://www.linkedin.com/company/acme") == ""
    assert normalize_linkedin_person_url("") == ""


def test_person_id_stable():
    a = person_id_from_linkedin("https://www.linkedin.com/in/foo/", prefix="demo")
    b = person_id_from_linkedin("https://linkedin.com/in/foo?trk=x", prefix="demo")
    assert a == b
    assert a.startswith("demo-")


def test_person_row_requires_in_url():
    assert person_row_from_mapping({"full_name": "No URL"}) is None
    row = person_row_from_mapping(
        {
            "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
            "full_name": "Ada Lovelace",
            "title": "Engineer",
        },
        seed_source_id="people_csv_ingest",
        campaign_prefix="t",
    )
    assert row is not None
    assert row["linkedin_url"] == "https://www.linkedin.com/in/ada-lovelace"
    assert row["first_name"] == "Ada"
    assert row["last_name"] == "Lovelace"
    assert row["seed_source_id"] == "people_csv_ingest"


def test_load_people_csv_dedupes(tmp_path: Path):
    csv_path = tmp_path / "people.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["linkedin_url", "full_name", "title"])
        w.writeheader()
        w.writerow(
            {
                "linkedin_url": "https://www.linkedin.com/in/a/",
                "full_name": "A One",
                "title": "Dev",
            }
        )
        w.writerow(
            {
                "linkedin_url": "https://www.linkedin.com/in/a?trk=1",
                "full_name": "A Dup",
                "title": "Dev",
            }
        )
        w.writerow(
            {
                "linkedin_url": "https://www.linkedin.com/company/x",
                "full_name": "Skip",
                "title": "",
            }
        )
        w.writerow(
            {
                "linkedin_url": "https://www.linkedin.com/in/b",
                "full_name": "B Two",
                "title": "PM",
            }
        )
    rows = load_people_from_csv(
        csv_path, seed_source_id="people_csv_ingest", campaign_id="talent_demo"
    )
    assert len(rows) == 2
    assert {r["linkedin_url"] for r in rows} == {
        "https://www.linkedin.com/in/a",
        "https://www.linkedin.com/in/b",
    }


def test_persist_t01_and_apify_mapping(tmp_path: Path):
    items = [
        {
            "linkedinUrl": "https://www.linkedin.com/in/c-three",
            "fullName": "C Three",
            "headline": "Backend",
            "location": "Spain",
        },
        {"url": "not-a-linkedin", "name": "Bad"},
    ]
    rows = items_from_apify_people(
        items, seed_source_id="linkedin_people", campaign_id="camp1"
    )
    assert len(rows) == 1
    assert rows[0]["full_name"] == "C Three"
    out = persist_t01(tmp_path, rows)
    assert out.name == STAGE_T01_RAW_PEOPLE
    loaded = read_people_csv(out)
    assert len(loaded) == 1
    assert loaded[0]["location"] == "Spain"


def test_default_people_actor_locked():
    assert DEFAULT_PEOPLE_ACTOR == "harvestapi/linkedin-profile-search"
    assert resolve_people_actor("") == DEFAULT_PEOPLE_ACTOR
    assert resolve_people_actor("custom/actor") == "custom/actor"


def test_resolve_people_actor_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APIFY_LINKEDIN_PEOPLE_SEARCH_ACTOR", "other/people-search")
    assert resolve_people_actor("") == "other/people-search"
    # Explicit config/CLI still wins over env
    assert resolve_people_actor("harvestapi/linkedin-profile-search") == (
        "harvestapi/linkedin-profile-search"
    )


def test_build_people_search_input_query_title_location():
    run_input = build_people_search_input(
        query="Python",
        title="Senior Backend Engineer",
        location=["Spain", "Portugal"],
        max_items=50,
    )
    assert run_input["profileScraperMode"] == DEFAULT_PROFILE_SCRAPER_MODE == "Short"
    assert run_input["searchQuery"] == "Python"
    assert run_input["currentJobTitles"] == ["Senior Backend Engineer"]
    assert run_input["locations"] == ["Spain", "Portugal"]
    assert run_input["maxItems"] == 50
    assert "startUrls" not in run_input


def test_build_people_search_input_requires_filters():
    with pytest.raises(ValueError, match="query, title, or location"):
        build_people_search_input(max_items=10)


def test_build_people_search_input_from_people_search_url():
    url = (
        "https://www.linkedin.com/search/results/people/"
        "?keywords=Senior%20Backend%20Engineer&origin=FACETED_SEARCH"
    )
    run_input = build_people_search_input(urls=[url], max_items=25)
    assert run_input["searchQuery"] == "Senior Backend Engineer"
    assert run_input["maxItems"] == 25
    assert run_input["urls"] == [url]


def test_harvestapi_fixture_maps_to_t01(tmp_path: Path):
    fixture = FIXTURES / "harvestapi_people_search_short.json"
    items = json.loads(fixture.read_text(encoding="utf-8"))
    rows = items_from_apify_people(
        items, seed_source_id="linkedin_people", campaign_id="talent_es"
    )
    assert len(rows) == 2
    by_url = {r["linkedin_url"]: r for r in rows}
    ada = by_url["https://www.linkedin.com/in/towhid-rahman"]
    assert ada["first_name"] == "Towhid"
    assert ada["last_name"] == "Rahman"
    assert "Los Angeles" in ada["location"]
    assert ada["company_name"] == "CVS Health"
    assert "cvshealth" in ada["company_linkedin_url"]
    hamid = by_url["https://www.linkedin.com/in/hamidsharif"]
    assert hamid["headline"] == "Backend Engineer"
    assert hamid["company_name"] == "Example SA"
    assert hamid["location"] == "Madrid, Community of Madrid, Spain"
    out = persist_t01(tmp_path, rows)
    assert out.name == STAGE_T01_RAW_PEOPLE
    assert len(read_people_csv(out)) == 2


def test_run_people_csv_ingest_via_registry(tmp_path: Path):
    from agent.utils.campaign_config import CampaignConfig
    from agent.utils.seed_sources import run_people_csv_ingest

    camp_dir = tmp_path / "talent_camp"
    camp_dir.mkdir()
    (camp_dir / "campaign.json").write_text(
        '{"campaign_id":"talent_camp","display_name":"T"}', encoding="utf-8"
    )
    people = camp_dir / "people_seeds.csv"
    people.write_text(
        "linkedin_url,full_name\n"
        "https://www.linkedin.com/in/x-y,X Y\n",
        encoding="utf-8",
    )
    (camp_dir / "seed_sources.json").write_text(
        '{"sources":[{"id":"people_csv_ingest","type":"people_csv_ingest",'
        '"name":"People CSV","enabled":true,"config":{}}]}',
        encoding="utf-8",
    )

    class _Cfg(CampaignConfig):
        @property
        def campaign_dir(self) -> Path:
            return camp_dir

    out = run_people_csv_ingest(_Cfg(campaign_id="talent_camp"), csv_path=str(people))
    assert out.is_file()
    assert out.name == STAGE_T01_RAW_PEOPLE
    rows = read_people_csv(out)
    assert len(rows) == 1
    assert rows[0]["full_name"] == "X Y"


def test_source_by_type_matches_custom_id():
    from agent.utils.campaign_config import SeedSourceConfig, SeedSourcesConfig

    cfg = SeedSourcesConfig(
        campaign_id="x",
        sources=[
            SeedSourceConfig(
                id="linkedin_people_backend_es",
                type="linkedin_people",
                name="People",
                enabled=True,
                config={"query": "Backend Spain"},
            )
        ],
    )
    entry = cfg.source_by_type("linkedin_people")
    assert entry is not None
    assert entry.id == "linkedin_people_backend_es"
    assert entry.config["query"] == "Backend Spain"
    assert cfg.source_by_id("linkedin_people") is None
