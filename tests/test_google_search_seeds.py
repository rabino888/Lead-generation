"""google_search seed source: SERP → one seed per root domain, no client data in code."""
from __future__ import annotations

from agent.utils.google_search_seeds import (
    company_name_from_result,
    is_excluded_host,
    normalize_queries,
    serp_items_to_seed_rows,
)


def _item(term, results, page=1):
    return {"searchQuery": {"term": term, "page": page}, "organicResults": results}


def test_brand_picked_from_title_segment():
    assert company_name_from_result("Home Insurance | Get a Quote | Acme", "acme.co.uk") == "Acme"
    assert company_name_from_result("Acme Underwriting - Specialty MGA", "acmeuw.com") == "Acme Underwriting"


def test_brand_falls_back_to_domain_label():
    assert company_name_from_result("Cheap cover today", "blue-fox.co.uk") == "Blue Fox"


def test_builtin_and_config_excludes():
    assert is_excluded_host("uk.linkedin.com")
    assert is_excluded_host("fca.org.uk", ["fca.org.uk"])
    assert is_excluded_host("service.hmrc.gov.uk")
    assert not is_excluded_host("acme.co.uk")
    assert is_excluded_host("")


def test_normalize_queries_accepts_strings_and_dicts():
    assert normalize_queries(["a", {"query": "b"}, {"keyword": "c"}, "A", " "]) == ["a", "b", "c"]


def test_rows_dedupe_by_host_and_respect_seen():
    items = [
        _item("q1", [
            {"url": "https://www.acme.co.uk/quote", "title": "Quote | Acme", "position": 1},
            {"url": "https://acme.co.uk/claims", "title": "Claims | Acme", "position": 2},
            {"url": "https://news.example-media.com/x", "title": "News", "position": 3},
            {"url": "https://www.linkedin.com/company/acme", "title": "Acme | LinkedIn"},
            {"url": "https://old.co.uk/", "title": "Old Co", "position": 4},
        ]),
    ]
    seen = {"old.co.uk"}
    rows, dropped = serp_items_to_seed_rows(
        items,
        geo_segment="Segment",
        location="Somewhere",
        exclude_domains=["example-media.com"],
        seen_hosts=seen,
    )
    assert [r["website"] for r in rows] == ["https://www.acme.co.uk"]
    assert rows[0]["company_name"] == "Acme"
    assert rows[0]["source_url"] == "https://www.acme.co.uk/quote"
    assert rows[0]["geo_segment"] == "Segment"
    assert "query: q1" in rows[0]["notes"]
    assert {d["host"] for d in dropped} == {"news.example-media.com", "linkedin.com"}
    assert "acme.co.uk" in seen


def test_run_google_search_appends_unseen(tmp_path, monkeypatch):
    import csv
    import json

    from agent.utils import seed_sources
    from agent.utils.campaign_config import CampaignConfig

    camp_dir = tmp_path / "camp"
    (camp_dir / "stages").mkdir(parents=True)
    (camp_dir / "seed_sources.json").write_text(json.dumps({
        "sources": [{
            "id": "google_search", "type": "google_search", "name": "Google", "enabled": True,
            "config": {"queries": ["widget makers"], "max_results": 2,
                       "exclude_domains": ["media.com"], "geo_segment": "Seg"},
        }]
    }), encoding="utf-8")
    raw = camp_dir / "stages" / "01_raw_seeds.csv"
    with raw.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["seed_id", "company_name", "website"])
        w.writeheader()
        w.writerow({"seed_id": "X-0001", "company_name": "Existing", "website": "https://existing.com"})

    monkeypatch.setattr("agent.utils.campaign_config.CAMPAIGNS_ROOT", tmp_path)
    campaign = CampaignConfig(campaign_id="camp", seed_id_prefix="X")

    items = [_item("widget makers", [
        {"url": "https://existing.com/a", "title": "Existing"},
        {"url": "https://media.com/b", "title": "Media"},
        {"url": "https://one.com/", "title": "One"},
        {"url": "https://two.com/", "title": "Two"},
        {"url": "https://three.com/", "title": "Three"},
    ])]
    monkeypatch.setattr(
        "agent.utils.google_search_seeds.scrape_google_search",
        lambda *a, **k: (items, 0.0),
    )
    out = seed_sources.run_google_search(campaign, append=True, firmographics=False)
    with out.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["website"] for r in rows] == ["https://existing.com", "https://one.com", "https://two.com"]
    assert [r["seed_id"] for r in rows] == ["X-0001", "X-0002", "X-0003"]



def test_subdomains_collapse_to_one_company():
    from agent.utils.google_search_seeds import registrable_domain, site_root

    assert registrable_domain("quote.acme.co.uk") == "acme.co.uk"
    assert registrable_domain("careers.acme.com") == "acme.com"
    assert site_root("https://quote.acme.co.uk/x") == "https://www.acme.co.uk"
    assert site_root("https://www.acme.co.uk/x") == "https://www.acme.co.uk"
    items = [_item("q", [
        {"url": "https://www.acme.co.uk/", "title": "Acme"},
        {"url": "https://careers.acme.co.uk/jobs", "title": "Careers | Acme"},
    ])]
    rows, _ = serp_items_to_seed_rows(items, geo_segment="S")
    assert len(rows) == 1


def test_bare_government_root_excluded():
    assert is_excluded_host("gov.uk")


def test_firmographics_domain_only_keeps_serp_website():
    from agent.utils.firmographics import merge_firmographics_into_rows

    rows = [
        {"company_name": "Acme", "website": "https://acme.co.uk"},
        {"company_name": "Elephant", "website": "https://elephant.co.uk"},
    ]
    enr = [
        {"domain": "acme.co.uk", "name": "Acme Insurance Ltd", "website": "https://acme.com", "employees": 120},
        {"domain": "elephantvc.com", "name": "Elephant", "website": "https://elephantvc.com", "employees": 9},
    ]
    out = merge_firmographics_into_rows(
        rows, enr, prefer_enriched_website=False, match_by_name=False, fill_name=True
    )
    assert out[0]["website"] == "https://acme.co.uk"
    assert out[0]["company_name"] == "Acme Insurance Ltd"
    assert out[0]["company_size"] == "120"
    assert out[1] == rows[1]
