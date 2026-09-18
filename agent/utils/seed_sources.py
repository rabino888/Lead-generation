"""
Deterministic seed-source registry.

Operator step 2 (LLM) chooses a source id; step 4 runs it with no LLM.
Apollo mixed_companies/search is intentionally NOT registered.

Company discovery: prefer google_maps (businesses by keyword+location).
linkedin_jobs remains for hiring_first / talent-adjacent lists only.
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from agent.utils.campaign_config import SEED_HEADERS, CampaignConfig, load_campaign
from agent.utils.icp_rules import (
    ensure_stages_dir,
    load_seed_sources,
    stage_path,
    touch_seed_source_run,
)
from agent.utils.seeds import load_company_seeds_csv

Runner = Callable[..., Path]

BLOCKED_APOLLO_SOURCES = frozenset(
    {
        "apollo",
        "apollo_company_search",
        "apollo_mixed_companies",
        "mixed_companies",
        "build_seed_list",
    }
)


def _write_raw_seeds_csv(campaign: CampaignConfig, rows: list[dict[str, Any]]) -> Path:
    ensure_stages_dir(campaign)
    out = stage_path(campaign, "raw_seeds")
    fieldnames = list(SEED_HEADERS)
    for row in rows:
        for k in row:
            if k not in fieldnames:
                fieldnames.append(k)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    return out


def _maybe_firmographics(rows: list[dict[str, Any]], *, enabled: bool) -> list[dict[str, Any]]:
    if not enabled or not rows:
        return rows
    from agent.utils.firmographics import enrich_seed_rows_firmographics

    return enrich_seed_rows_firmographics(rows, prefer_enriched_website=True)


def run_csv_ingest(
    campaign: CampaignConfig,
    *,
    csv_path: str | Path,
    copy_to_seeds: bool = True,
) -> Path:
    """Normalize any seed CSV into stages/01_raw_seeds.csv."""
    path = Path(csv_path)
    seeds = load_company_seeds_csv(path)
    rows: list[dict[str, Any]] = []
    for i, seed in enumerate(seeds, start=1):
        data = seed.model_dump()
        rows.append(
            {
                "seed_id": f"{campaign.seed_id_prefix}-{i:04d}",
                "company_name": data.get("company_name") or "",
                "website": data.get("website") or "",
                "location": data.get("location") or "",
                "geo_segment": data.get("location") or "",
                "source_url": data.get("source_url") or "",
                "status": "pending",
                "notes": data.get("notes") or "",
                "industry": data.get("industry") or "",
                "company_size": data.get("company_size") or "",
            }
        )
    out = _write_raw_seeds_csv(campaign, rows)
    if copy_to_seeds:
        with campaign.seeds_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=SEED_HEADERS, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow(row)
    touch_seed_source_run(
        campaign,
        "csv_ingest",
        ref=str(out),
        seeds_added=len(rows),
        why_chosen="Hand/web-researched or prior scrape CSV",
        source_type="csv_ingest",
        config_update={"csv_path": str(path)},
    )
    return out


def run_google_maps(
    campaign: CampaignConfig,
    *,
    queries: list[dict[str, str]] | None = None,
    keyword: str = "",
    location: str = "",
    max_results: int = 50,
    geo_segment: str = "",
    firmographics: bool = True,
) -> Path:
    """Discover companies via Google Maps (Apify) → stages/01_raw_seeds.csv."""
    from agent.integrations.apify import google_maps_search

    cfg = load_seed_sources(campaign)
    entry = cfg.source_by_id("google_maps")
    resolved_queries: list[dict[str, str]] = list(queries or [])
    if not resolved_queries and entry:
        conf = entry.config or {}
        resolved_queries = list(conf.get("queries") or [])
        max_results = int(conf.get("max_results") or max_results)
        geo_segment = conf.get("geo_segment") or geo_segment
        if conf.get("firmographics") is False:
            firmographics = False
        if not resolved_queries and conf.get("keyword"):
            resolved_queries = [{
                "keyword": str(conf.get("keyword")),
                "location": str(conf.get("location") or geo_segment or ""),
            }]
    if not resolved_queries and keyword:
        resolved_queries = [{"keyword": keyword, "location": location or geo_segment}]
    if not resolved_queries:
        raise ValueError(
            "google_maps requires queries=[{keyword, location}] or seed_sources.json config"
        )

    segment = geo_segment or (
        campaign.segment_order[0] if campaign.segment_order else "default"
    )
    per_query = max(5, int(max_results) // max(1, len(resolved_queries)))
    seen_domains: set[str] = set()
    rows: list[dict[str, Any]] = []
    for q in resolved_queries:
        kw = (q.get("keyword") or "").strip()
        loc = (q.get("location") or location or "").strip()
        if not kw:
            continue
        companies = google_maps_search(kw, loc, max_results=per_query)
        for c in companies:
            website = (c.website or "").strip()
            if not website:
                continue
            from agent.utils.google_maps_seeds import root_domain

            domain = root_domain(website)
            if domain and domain in seen_domains:
                continue
            if domain:
                seen_domains.add(domain)
            rows.append(
                {
                    "seed_id": "",
                    "company_name": c.company_name or "",
                    "website": website if website.startswith("http") else f"https://{website}",
                    "location": c.location or loc,
                    "geo_segment": segment,
                    "source_url": f"google_maps:{kw}",
                    "status": "pending",
                    "notes": f"Google Maps | query: {kw}",
                    "industry": c.industry or "",
                    "company_size": c.company_size or "",
                }
            )

    rows = _maybe_firmographics(rows, enabled=firmographics)
    for i, row in enumerate(rows, start=1):
        row["seed_id"] = f"{campaign.seed_id_prefix}-{i:04d}"

    out = _write_raw_seeds_csv(campaign, rows)
    with campaign.seeds_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SEED_HEADERS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)

    touch_seed_source_run(
        campaign,
        "google_maps",
        ref=str(out),
        seeds_added=len(rows),
        why_chosen="Google Maps keyword+location → company seeds",
        source_type="google_maps",
        config_update={
            "queries": resolved_queries,
            "max_results": max_results,
            "geo_segment": segment,
            "firmographics": firmographics,
        },
    )
    return out


def run_linkedin_jobs(
    campaign: CampaignConfig,
    *,
    urls: list[str] | None = None,
    url_file: str | Path | None = None,
    count: int = 100,
    actor: str | None = None,
    geo_segment: str = "",
    firmographics: bool = True,
    resolve_li_websites: bool | None = None,
) -> Path:
    """Scrape LinkedIn jobs via Apify and write stages/01_raw_seeds.csv."""
    from agent.utils.linkedin_jobs_seeds import (
        DEFAULT_ACTOR,
        DEFAULT_GEO_SEGMENT,
        assign_seed_ids,
        jobs_to_seeds,
        load_linkedin_jobs_urls,
        prefer_linkedin_company_websites,
        scrape_linkedin_jobs,
    )

    cfg = load_seed_sources(campaign)
    entry = cfg.source_by_id("linkedin_jobs")
    resolved_urls = list(urls or [])
    resolved_url_file = Path(url_file) if url_file else None
    if not resolved_urls and resolved_url_file is None and entry:
        resolved_urls = list(entry.config.get("urls") or [])
        if entry.config.get("url_file"):
            resolved_url_file = Path(entry.config["url_file"])
        count = int(entry.config.get("count") or count)
        actor = entry.config.get("actor") or actor
        geo_segment = entry.config.get("geo_segment") or geo_segment
        if entry.config.get("firmographics") is False:
            firmographics = False

    try:
        resolved_urls = load_linkedin_jobs_urls(
            urls=resolved_urls or None, url_file=resolved_url_file
        )
    except ValueError as exc:
        raise ValueError(
            "linkedin_jobs requires --url / --url-file or seed_sources.json config.urls"
        ) from exc

    actor_id = actor or DEFAULT_ACTOR
    segment = geo_segment or (
        campaign.segment_order[0] if campaign.segment_order else DEFAULT_GEO_SEGMENT
    )
    jobs = scrape_linkedin_jobs(resolved_urls, count=count, actor_id=actor_id)

    # Persist raw jobs for forensics / employee index (pre-Apollo gate)
    raw_path = campaign.campaign_dir / "linkedin_jobs_raw.json"
    try:
        raw_path.write_text(json.dumps(jobs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass

    seeds, dropped = jobs_to_seeds(jobs, geo_segment=segment, require_website=True)

    # Prefer LinkedIn company About website over jobs-card companyWebsite when
    # explicitly enabled or for tiny lists (smoke-scale). Default bulk fix = foxlabs.
    if resolve_li_websites is None:
        resolve_li_websites = os.environ.get(
            "LINKEDIN_RESOLVE_SEED_WEBSITES", ""
        ).lower() in ("1", "true", "yes") or len(seeds) <= 5
    if resolve_li_websites and seeds:
        seeds = prefer_linkedin_company_websites(seeds)

    seeds = assign_seed_ids(seeds, prefix=campaign.seed_id_prefix)
    rows = [asdict(s) for s in seeds]
    rows = _maybe_firmographics(rows, enabled=firmographics)

    out = _write_raw_seeds_csv(campaign, rows)

    with campaign.seeds_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SEED_HEADERS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)

    touch_seed_source_run(
        campaign,
        "linkedin_jobs",
        ref=str(out),
        seeds_added=len(rows),
        why_chosen="LinkedIn jobs search → company seeds (hiring signal; not primary discovery)",
        source_type="linkedin_jobs",
        config_update={
            "urls": resolved_urls,
            "count": count,
            "actor": actor_id,
            "geo_segment": segment,
            "dropped_count": len(dropped),
            "firmographics": firmographics,
            "resolve_li_websites": bool(resolve_li_websites),
        },
    )
    return out


def run_linkedin_companies(
    campaign: CampaignConfig,
    *,
    queries: list[dict[str, Any]] | None = None,
    urls: list[str] | None = None,
    max_results: int = 80,
    actor: str | None = None,
    geo_segment: str = "",
    scraper_mode: str = "full",
    firmographics: bool = False,
) -> Path:
    """
    LinkedIn company search (not jobs) → stages/01_raw_seeds.csv.

    Deterministic: ICP → company-search URLs/queries → harvestapi scrape.
    Full mode returns the company About website (correct local TLD).
    """
    from agent.utils.linkedin_company_seeds import (
        DEFAULT_ACTOR,
        companies_to_seed_rows,
        scrape_linkedin_companies,
    )

    cfg = load_seed_sources(campaign)
    entry = cfg.source_by_id("linkedin_companies")
    resolved_queries: list[dict[str, Any]] = list(queries or [])
    if not resolved_queries and entry:
        conf = entry.config or {}
        resolved_queries = list(conf.get("queries") or [])
        max_results = int(conf.get("max_results") or max_results)
        geo_segment = conf.get("geo_segment") or geo_segment
        actor = conf.get("actor") or actor
        scraper_mode = conf.get("scraper_mode") or scraper_mode
        if conf.get("firmographics") is True:
            firmographics = True
    if not resolved_queries:
        raise ValueError(
            "linkedin_companies requires queries from ICP / seed_sources.json "
            "(searchQuery + locations). Jobs search is not used."
        )

    segment = geo_segment or (
        campaign.segment_order[0] if campaign.segment_order else "default"
    )
    actor_id = actor or DEFAULT_ACTOR
    companies = scrape_linkedin_companies(
        resolved_queries,
        max_results=max_results,
        actor_id=actor_id,
        scraper_mode=scraper_mode,
    )

    raw_path = campaign.campaign_dir / "linkedin_companies_raw.json"
    try:
        raw_path.write_text(
            json.dumps(companies, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError:
        pass

    rows = companies_to_seed_rows(
        companies, geo_segment=segment, prefix=campaign.seed_id_prefix
    )
    rows = _maybe_firmographics(rows, enabled=firmographics)

    out = _write_raw_seeds_csv(campaign, rows)
    with campaign.seeds_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SEED_HEADERS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)

    touch_seed_source_run(
        campaign,
        "linkedin_companies",
        ref=str(out),
        seeds_added=len(rows),
        why_chosen="LinkedIn company search URLs from ICP → company seeds",
        source_type="linkedin_companies",
        config_update={
            "queries": resolved_queries,
            "urls": list(urls or []) or [q.get("url") for q in resolved_queries if q.get("url")],
            "max_results": max_results,
            "actor": actor_id,
            "geo_segment": segment,
            "scraper_mode": scraper_mode,
        },
    )
    return out


def run_people_csv_ingest(
    campaign: CampaignConfig,
    *,
    csv_path: str = "",
) -> Path:
    """Talent T01: person CSV with linkedin_url → stages/T01_raw_people.csv."""
    from agent.utils.linkedin_people_seeds import load_people_from_csv, persist_t01

    cfg = load_seed_sources(campaign)
    entry = cfg.source_by_type("people_csv_ingest")
    path = Path(csv_path) if csv_path else None
    if path is None and entry:
        path = Path((entry.config or {}).get("csv_path") or "")
    if path is None or not str(path):
        # Convention: people_seeds.csv in campaign folder
        cand = campaign.campaign_dir / "people_seeds.csv"
        path = cand if cand.is_file() else None
    if path is None or not path.is_file():
        raise ValueError(
            "people_csv_ingest requires --csv path/to/people.csv "
            "(columns: linkedin_url, optional full_name / title / location)"
        )
    seed_id = entry.id if entry else "people_csv_ingest"
    rows = load_people_from_csv(
        path,
        seed_source_id=seed_id,
        campaign_id=campaign.campaign_id,
    )
    if not rows:
        raise RuntimeError(f"No parseable LinkedIn /in/ URLs in {path}")
    out = persist_t01(campaign.campaign_dir, rows)
    touch_seed_source_run(
        campaign,
        seed_id,
        ref=str(out),
        seeds_added=len(rows),
        why_chosen="Operator-curated person CSV for talent_search T01",
        source_type="people_csv_ingest",
        config_update={"csv_path": str(path)},
    )
    return out


def run_linkedin_people(
    campaign: CampaignConfig,
    *,
    urls: list[str] | None = None,
    url_file: str = "",
    max_results: int = 100,
    actor: str = "",
    query: str = "",
    title: str | list[str] = "",
    location: str | list[str] = "",
    profile_scraper_mode: str = "",
) -> Path:
    """Talent T01: Apify LinkedIn people search → stages/T01_raw_people.csv."""
    from agent.utils.linkedin_people_seeds import (
        items_from_apify_people,
        persist_t01,
        resolve_people_actor,
        scrape_linkedin_people,
    )

    cfg = load_seed_sources(campaign)
    entry = cfg.source_by_type("linkedin_people")
    conf = (entry.config if entry else None) or {}
    seed_id = entry.id if entry else "linkedin_people"
    resolved_urls = list(urls or [])
    if not resolved_urls and url_file:
        p = Path(url_file)
        if p.is_file():
            resolved_urls = [
                ln.strip()
                for ln in p.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
    if not resolved_urls:
        resolved_urls = [str(u).strip() for u in (conf.get("urls") or []) if str(u).strip()]

    resolved_query = (query or conf.get("query") or conf.get("searchQuery") or "").strip()
    resolved_title = title if title else (conf.get("title") or conf.get("titles") or "")
    resolved_location = (
        location
        if location
        else (conf.get("location") or conf.get("locations") or "")
    )
    max_results = int(
        conf.get("max_results") or conf.get("target_count") or conf.get("count") or max_results
    )
    mode = (
        profile_scraper_mode
        or conf.get("profile_scraper_mode")
        or conf.get("profileScraperMode")
        or conf.get("scraper_mode")
        or ""
    )
    actor_id = resolve_people_actor(
        actor or conf.get("actor") or conf.get("actor_id") or ""
    )

    has_search = bool(
        resolved_query
        or (isinstance(resolved_title, str) and resolved_title.strip())
        or (
            isinstance(resolved_title, (list, tuple))
            and any(str(t).strip() for t in resolved_title)
        )
        or (isinstance(resolved_location, str) and resolved_location.strip())
        or (
            isinstance(resolved_location, (list, tuple))
            and any(str(loc).strip() for loc in resolved_location)
        )
    )
    if not has_search and not resolved_urls:
        raise ValueError(
            "linkedin_people requires config.query / title / location "
            "(HarvestAPI searchQuery / currentJobTitles / locations), "
            "or LinkedIn people-search URL(s)."
        )

    items = scrape_linkedin_people(
        max_results=max_results,
        actor_id=actor_id,
        query=resolved_query,
        title=resolved_title,
        location=resolved_location,
        profile_scraper_mode=mode or "Short",
        urls=resolved_urls or None,
    )
    raw_path = campaign.campaign_dir / "linkedin_people_raw.json"
    raw_path.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rows = items_from_apify_people(
        items,
        seed_source_id=seed_id,
        campaign_id=campaign.campaign_id,
    )
    if not rows:
        raise RuntimeError(
            f"Apify people actor returned {len(items)} item(s) but no parseable /in/ URLs"
        )
    out = persist_t01(campaign.campaign_dir, rows)
    touch_seed_source_run(
        campaign,
        seed_id,
        ref=str(out),
        seeds_added=len(rows),
        why_chosen="LinkedIn people search → talent T01 raw people",
        source_type="linkedin_people",
        config_update={
            "query": resolved_query,
            "title": resolved_title if not isinstance(resolved_title, list) else resolved_title,
            "location": resolved_location
            if not isinstance(resolved_location, list)
            else resolved_location,
            "urls": resolved_urls,
            "max_results": max_results,
            "actor": actor_id,
            "profile_scraper_mode": mode or "Short",
        },
    )
    return out


def _blocked_apollo(**_kwargs: Any) -> Path:
    raise RuntimeError(
        "Apollo company search (mixed_companies/search) is not a registered seed source. "
        "Use csv_ingest, google_maps, or linkedin_jobs. See docs/OPERATOR-WORKFLOW.md."
    )


REGISTRY: dict[str, Runner] = {
    "csv_ingest": run_csv_ingest,
    "google_maps": run_google_maps,
    "linkedin_companies": run_linkedin_companies,
    # linkedin_jobs kept for legacy CLI only — gated auto-seed never calls it
    "linkedin_jobs": run_linkedin_jobs,
    # Talent search (SPEC-talent-search.md T01)
    "people_csv_ingest": run_people_csv_ingest,
    "linkedin_people": run_linkedin_people,
}


def list_sources() -> list[str]:
    return sorted(REGISTRY)


def run_source(campaign_id: str, source_id: str, **kwargs: Any) -> Path:
    sid = source_id.strip().lower()
    if sid in BLOCKED_APOLLO_SOURCES:
        return _blocked_apollo()
    if sid == "linkedin_jobs" and kwargs.pop("_allow_jobs", None) is not True:
        # Hard block unless explicit legacy opt-in (CLI scripts pass _allow_jobs=True)
        if os.environ.get("ALLOW_LINKEDIN_JOBS_SEED", "").lower() not in ("1", "true", "yes"):
            raise RuntimeError(
                "linkedin_jobs seeding is disabled. Use linkedin_companies (company search) "
                "or csv_ingest / google_maps. Set ALLOW_LINKEDIN_JOBS_SEED=1 only for legacy runs."
            )
    if sid not in REGISTRY:
        raise KeyError(
            f"Unknown seed source '{source_id}'. "
            f"Registered: {', '.join(list_sources())}. "
            f"Apollo company search is blocked."
        )
    campaign = load_campaign(campaign_id)
    return REGISTRY[sid](campaign, **kwargs)
