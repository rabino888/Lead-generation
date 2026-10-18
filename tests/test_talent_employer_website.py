"""Brief-driven employer website enrich for talent lists."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from agent.utils.talent_employer_website import (
    brief_url_terms,
    enrich_contactable_employer_website,
    load_client_brief_text,
)
from agent.utils.talent_people import STAGE_T05_APOLLO_CONTACTABLE, talent_stage_path
from agent.utils.website_outreach import prefer_brief_relevant_urls


def test_load_client_brief_from_client_md(tmp_path: Path):
    data = tmp_path / "data"
    client = data / "clients" / "decoracel"
    client.mkdir(parents=True)
    (client / "CLIENT.md").write_text(
        "Decoracel sells celosías. Buyers: property managers with rental portfolios.\n",
        encoding="utf-8",
    )
    (client / "client.json").write_text(
        json.dumps({"client_id": "decoracel", "brief_path": "CLIENT.md"}),
        encoding="utf-8",
    )
    camp = data / "clients" / "decoracel" / "campaigns" / "in_progress" / "c1"
    camp.mkdir(parents=True)
    (camp / "campaign.json").write_text(
        json.dumps({"client_id": "decoracel", "campaign_type": "talent_search"}),
        encoding="utf-8",
    )
    (camp / "icp.json").write_text(
        json.dumps({"include_keywords": ["property management", "holiday rental"]}),
        encoding="utf-8",
    )
    brief = load_client_brief_text(camp, data_root=data)
    assert "celosías" in brief.lower() or "celosias" in brief.lower() or "property managers" in brief.lower()
    assert "property management" in brief.lower()
    terms = brief_url_terms(brief, {"include_keywords": ["property management", "alquiler"]})
    assert any("property" in t.lower() or "alquiler" in t.lower() for t in terms)


def test_prefer_brief_relevant_urls_adds_listing_pages():
    mapped = [
        "https://example.com/",
        "https://example.com/about",
        "https://example.com/properties/villa-marbella",
        "https://example.com/alquiler/apartamento-1",
        "https://example.com/blog/hello",
    ]
    urls = prefer_brief_relevant_urls(
        mapped,
        "https://example.com",
        ["property", "alquiler", "villa"],
        max_pages=6,
        max_brief_slots=3,
    )
    assert "https://example.com/" in urls or any(u.rstrip("/").endswith("example.com") for u in urls)
    joined = " ".join(urls).lower()
    assert "propert" in joined or "alquiler" in joined


def test_enrich_contactable_employer_website(tmp_path: Path):
    data = tmp_path / "data"
    client = data / "clients" / "decoracel"
    client.mkdir(parents=True)
    (client / "CLIENT.md").write_text(
        "We sell decoration to property managers. Find properties they advertise.\n",
        encoding="utf-8",
    )
    (client / "client.json").write_text(
        json.dumps({"client_id": "decoracel", "brief_path": "CLIENT.md"}),
        encoding="utf-8",
    )
    camp = data / "clients" / "decoracel" / "campaigns" / "in_progress" / "c1"
    stages = camp / "stages"
    stages.mkdir(parents=True)
    (camp / "campaign.json").write_text(
        json.dumps({"client_id": "decoracel", "campaign_type": "talent_search"}),
        encoding="utf-8",
    )
    (camp / "icp.json").write_text(
        json.dumps({"include_keywords": ["property", "rental"]}),
        encoding="utf-8",
    )
    t05 = talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE)
    rows = [
        {
            "person_id": "p1",
            "full_name": "Ana",
            "email": "a@x.com",
            "company_name": "Costa Homes",
            "website": "https://costahomes.example",
        }
    ]
    with t05.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    def fake_crawl(url: str) -> str:
        return (
            f"URL: {url}\n\n---\n\n"
            "# Properties\n"
            "Villa Luna — 3 bed in Nueva Andalucía\n"
            "Apartment Sol — Puerto Banús rental\n"
        )

    def fake_analyse(markdown, *, company_name, brief, website=""):
        assert "property" in brief.lower() or "decoration" in brief.lower()
        assert "Villa Luna" in markdown
        return {
            "website_summary": f"{company_name} manages holiday rentals.",
            "services_offered": ["Holiday rental management"],
            "website_brief_findings": [
                "Villa Luna — 3 bed Nueva Andalucía",
                "Apartment Sol — Puerto Banús rental",
            ],
            "website_brief_findings_summary": "Two properties advertised on site.",
            "website_listing_urls": [f"{website}/properties"],
        }

    enriched, skipped = enrich_contactable_employer_website(
        camp,
        crawl_fn=fake_crawl,
        analyse_fn=fake_analyse,
        only_missing=True,
        data_root=data,
    )
    assert enriched == 1
    assert skipped == 0
    with t05.open(encoding="utf-8", newline="") as fh:
        out = list(csv.DictReader(fh))[0]
    assert out["website"] == "https://costahomes.example"
    assert "Villa Luna" in out["website_brief_findings"]
    assert "Two properties" in out["website_brief_findings_summary"]
