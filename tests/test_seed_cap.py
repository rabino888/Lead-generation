"""Company search size follows the builder target, not a fixed 80."""
from __future__ import annotations

import json

from agent.utils.linkedin_company_seeds import per_query_limits
from agent.utils.list_run import _ensure_seeds


def test_six_searches_add_up_to_the_builder_target():
    limits = per_query_limits(100, 6)
    assert limits == [17, 17, 17, 17, 16, 16]
    assert sum(limits) == 100


def test_next_linkedin_page_follows_the_largest_search():
    from agent.utils.linkedin_company_seeds import advance_start_page

    assert advance_start_page(1, 17) == 3
    assert advance_start_page(3, 16) == 5


def test_seed_search_uses_the_builder_target(tmp_path, monkeypatch):
    (tmp_path / "seed_sources.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "id": "linkedin_companies",
                        "enabled": True,
                        "config": {
                            "max_results": 80,
                            "queries": [{"searchQuery": "apparel"}],
                            "urls": [],
                            "actor": "harvestapi/linkedin-company-search",
                            "geo_segment": "United States",
                            "scraper_mode": "full",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class _Campaign:
        campaign_dir = tmp_path
        campaign_id = "demo"
        icp_path = tmp_path / "icp.json"
        seeds_csv = tmp_path / "seeds.csv"

    called: dict = {}

    def _run_source(campaign_id, source, **kwargs):
        called["campaign_id"] = campaign_id
        called["source"] = source
        called["max_results"] = kwargs.get("max_results")
        return tmp_path / "stages" / "01_raw_seeds.csv"

    monkeypatch.setattr("agent.utils.campaign_config.load_campaign", lambda _cid: _Campaign())
    monkeypatch.setattr(
        "agent.utils.icp_rules.stage_path",
        lambda _campaign, _name: tmp_path / "missing.csv",
    )
    monkeypatch.setattr("agent.utils.seed_sources.run_source", _run_source)

    _ensure_seeds("demo", seed_count=100)

    assert called["source"] == "linkedin_companies"
    assert called["max_results"] == 100
    saved = json.loads((tmp_path / "seed_sources.json").read_text(encoding="utf-8"))
    assert saved["sources"][0]["config"]["max_results"] == 100


def test_refill_searches_extra_companies_while_the_list_is_short():
    from agent.utils.contactable_fill import refill_batch_size, unseen_rows

    assert refill_batch_size(100, 20) == 100
    assert refill_batch_size(100, 95) == 25
    kept = unseen_rows(
        [
            {"company_name": "TULA", "website": "https://www.tula.com"},
            {"company_name": "Hero", "website": "https://herocosmetics.com"},
        ],
        {"d:tula.com"},
    )
    assert [row["company_name"] for row in kept] == ["Hero"]


def test_fill_stops_when_linkedin_returns_nothing_new(tmp_path, monkeypatch):
    from agent.utils.list_run import _run_until_contactable_target

    class _Campaign:
        campaign_dir = tmp_path
        campaign_id = "demo"

    monkeypatch.setattr("agent.utils.campaign_config.load_campaign", lambda _cid: _Campaign())
    monkeypatch.setattr(
        "agent.utils.stage_artifacts.read_stage_csv",
        lambda _campaign, _key: [{"company_name": "TULA", "website": "https://tula.com"}],
    )

    def _search(_campaign, **_kwargs):
        (tmp_path / "linkedin_page_stats.json").write_text(
            '{"scraped": 0, "added": 0}',
            encoding="utf-8",
        )
        return tmp_path / "stages" / "01_raw_seeds.csv"

    monkeypatch.setattr("agent.utils.seed_sources.run_linkedin_companies", _search)
    paid: dict = {}
    monkeypatch.setattr(
        "agent.utils.list_run._run_paid_slice",
        lambda *_args, **_kwargs: paid.setdefault("called", True),
    )

    result = _run_until_contactable_target(
        tmp_path, "demo", target_leads=100, smoke_leads=1
    )

    assert result["delivered"] == 0
    assert "no further companies" in result["reason"]
    assert "called" not in paid


def test_later_rounds_keep_leads_from_the_earlier_round(tmp_path):
    from agent.utils.list_run import _merge_csv_file

    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    dest = tmp_path / "out.csv"
    first.write_text(
        "decision_maker_email,company_name\nada@tula.com,TULA\n",
        encoding="utf-8",
    )
    second.write_text(
        "decision_maker_email,company_name\nhero@hero.com,Hero\n",
        encoding="utf-8",
    )
    _merge_csv_file(dest, first)
    _merge_csv_file(dest, second)
    text = dest.read_text(encoding="utf-8")
    assert "ada@tula.com" in text
    assert "hero@hero.com" in text


def test_later_slice_appends_onto_the_first_sheet(tmp_path, monkeypatch):
    from agent.utils.list_run import _publish_slice_onto_campaign_sheet

    slice_csv = tmp_path / "slice.csv"
    slice_csv.write_text(
        "decision_maker_email,company_name\nhero@hero.com,Hero\n",
        encoding="utf-8",
    )

    class _Run:
        sheet_url = "https://docs.google.com/spreadsheets/d/later"
        csv_path = str(slice_csv)

    monkeypatch.setattr("agent.utils.run_tracker.get_run", lambda _run_id: _Run())
    appended: dict = {}

    def _append(url, rows):
        appended["url"] = url
        appended["rows"] = rows
        return len(rows)

    monkeypatch.setattr("agent.integrations.sheets.append_csv_leads_to_sheet", _append)
    kept = _publish_slice_onto_campaign_sheet(
        "run_2",
        "https://docs.google.com/spreadsheets/d/first",
    )
    assert kept == "https://docs.google.com/spreadsheets/d/first"
    assert appended["url"].endswith("/first")
    assert appended["rows"][0]["company_name"] == "Hero"
