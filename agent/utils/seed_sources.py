"""
Deterministic seed-source registry.

Operator step 2 (LLM) chooses a source id; step 4 runs it with no LLM.
Apollo mixed_companies/search is intentionally NOT registered.

Company discovery: linkedin_companies (company search), google_search (web SERP
queries, one seed per root domain) or google_maps (local businesses).
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


def _record_seed_apify_cost(
    campaign: CampaignConfig,
    usd: float,
    *,
    source_id: str = "linkedin_companies",
    label: str = "LinkedIn company search",
    actor: str = "harvestapi/linkedin-company-search",
) -> None:
    """Append seed-source Apify spend to this campaign's cost log."""
    from datetime import datetime, timezone

    from agent.utils.cost_manifest import append_manifest_run

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    amount = round(float(usd), 4)
    append_manifest_run(
        campaign.campaign_dir,
        {
            "run_key": f"seed_{source_id}_{stamp}",
            "label": label,
            "stage": "seed",
            "run_id": f"seed-{stamp}",
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "ended_at_utc": datetime.now(timezone.utc).isoformat(),
            "cost_tracker": {
                "total_usd": amount,
                "apify_usd": amount,
                "source": "apify usageTotalUsd",
            },
            "apify": {
                "cost_tracker_usd": amount,
                "api_truth_usd": amount,
                "drift_usd": 0,
                "source": actor,
            },
        },
        campaign_id=campaign.campaign_id,
    )


def run_linkedin_companies(
    campaign: CampaignConfig,
    *,
    queries: list[dict[str, Any]] | None = None,
    urls: list[str] | None = None,
    max_results: int | None = None,
    actor: str | None = None,
    geo_segment: str = "",
    scraper_mode: str = "full",
    firmographics: bool = False,
    start_page: int | None = None,
    append: bool = False,
) -> Path:
    """
    LinkedIn company search (not jobs) → stages/01_raw_seeds.csv.

    Deterministic: ICP → company-search URLs/queries → harvestapi scrape.
    Full mode returns the company About website (correct local TLD).
    """
    from agent.utils.linkedin_company_seeds import (
        DEFAULT_ACTOR,
        advance_start_page,
        companies_to_seed_rows,
        per_query_limits,
        scrape_linkedin_companies,
    )
    from agent.utils.contactable_fill import company_key, unseen_rows

    cfg = load_seed_sources(campaign)
    entry = cfg.source_by_id("linkedin_companies")
    resolved_queries: list[dict[str, Any]] = list(queries or [])
    conf = (entry.config or {}) if entry else {}
    if not resolved_queries and entry:
        resolved_queries = list(conf.get("queries") or [])
        geo_segment = conf.get("geo_segment") or geo_segment
        actor = conf.get("actor") or actor
        scraper_mode = conf.get("scraper_mode") or scraper_mode
        if conf.get("firmographics") is True:
            firmographics = True
    if max_results is None:
        max_results = int(conf.get("max_results") or 80)
    if not resolved_queries:
        raise ValueError(
            "linkedin_companies requires queries from ICP / seed_sources.json "
            "(searchQuery + locations). Jobs search is not used."
        )

    segment = geo_segment or (
        campaign.segment_order[0] if campaign.segment_order else "default"
    )
    actor_id = actor or DEFAULT_ACTOR
    usable = [
        q for q in resolved_queries
        if (q.get("searchQuery") or q.get("keyword") or "").strip()
    ]
    page = 1
    if append and entry is not None:
        stored = (entry.config or {}).get("next_start_page")
        if stored:
            page = int(stored)
        else:
            already = 0
            raw_existing = stage_path(campaign, "raw_seeds")
            if raw_existing.is_file():
                try:
                    with raw_existing.open(encoding="utf-8-sig", newline="") as f:
                        already = sum(1 for _ in csv.DictReader(f))
                except OSError:
                    already = 0
            query_count = max(1, len(usable))
            per_query = max(1, (already + query_count - 1) // query_count) if already else 1
            page = advance_start_page(1, per_query)
    if start_page is not None:
        page = max(1, int(start_page))
    limits = per_query_limits(max_results, len(usable) or 1)
    largest = max((n for n in limits if n > 0), default=0)
    companies, apify_usd = scrape_linkedin_companies(
        resolved_queries,
        max_results=max_results,
        actor_id=actor_id,
        scraper_mode=scraper_mode,
        start_page=page,
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

    existing: list[dict[str, Any]] = []
    raw_out = stage_path(campaign, "raw_seeds")
    if append and raw_out.is_file():
        try:
            with raw_out.open(encoding="utf-8-sig", newline="") as f:
                existing = list(csv.DictReader(f))
        except OSError:
            existing = []
    seen = {company_key(row) for row in existing}
    new_rows = unseen_rows(rows, seen)
    offset = len(existing)
    for i, row in enumerate(new_rows, start=offset + 1):
        row["seed_id"] = f"{campaign.seed_id_prefix}-{i:04d}"
    combined = existing + new_rows if append else rows

    out = _write_raw_seeds_csv(campaign, combined)
    with campaign.seeds_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SEED_HEADERS, extrasaction="ignore")
        w.writeheader()
        for row in combined:
            w.writerow(row)

    next_page = advance_start_page(page, largest)
    stats_path = campaign.campaign_dir / "linkedin_page_stats.json"
    try:
        stats_path.write_text(
            json.dumps(
                {"scraped": len(companies), "added": len(new_rows) if append else len(rows), "start_page": page}
            ),
            encoding="utf-8",
        )
    except OSError:
        pass

    touch_seed_source_run(
        campaign,
        "linkedin_companies",
        ref=str(out),
        seeds_added=len(new_rows) if append else len(rows),
        why_chosen="LinkedIn company search URLs from ICP → company seeds",
        source_type="linkedin_companies",
        config_update={
            "queries": resolved_queries,
            "urls": list(urls or []) or [q.get("url") for q in resolved_queries if q.get("url")],
            "max_results": max_results,
            "actor": actor_id,
            "geo_segment": segment,
            "scraper_mode": scraper_mode,
            "next_start_page": next_page,
        },
    )
    if apify_usd > 0:
        _record_seed_apify_cost(campaign, apify_usd)
    return out


def _read_raw_seed_rows(campaign: CampaignConfig) -> list[dict[str, Any]]:
    raw = stage_path(campaign, "raw_seeds")
    if not raw.is_file():
        return []
    try:
        with raw.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def run_google_search(
    campaign: CampaignConfig,
    *,
    queries: list[Any] | None = None,
    max_results: int | None = None,
    pages_per_query: int | None = None,
    country_code: str = "",
    language_code: str = "",
    location: str = "",
    geo_segment: str = "",
    exclude_domains: list[str] | None = None,
    actor: str | None = None,
    firmographics: bool = True,
    append: bool = False,
    reuse_raw: bool = False,
) -> Path:
    """
    Google Search (Apify) → stages/01_raw_seeds.csv.

    Queries, country/language, location label and campaign-specific domain excludes
    come from seed_sources.json `google_search.config`. One root domain = one seed.
    `max_results` caps NEW seeds kept (default 150). `append` keeps existing raw
    seeds (e.g. a curated csv_ingest) and adds only unseen domains after them.
    `reuse_raw` re-parses the saved google_search_raw.json (no Apify spend), e.g.
    after editing exclude_domains; prior Google rows are dropped before re-adding.
    Firmographics trust domain matches only: the SERP host is ground truth.
    """
    from agent.utils.contactable_fill import company_key
    from agent.utils.google_search_seeds import (
        DEFAULT_ACTOR,
        DEFAULT_PAGES_PER_QUERY,
        DEFAULT_RESULTS_PER_PAGE,
        DEFAULT_TARGET,
        host_of,
        normalize_queries,
        registrable_domain,
        scrape_google_search,
        serp_items_to_seed_rows,
    )

    cfg = load_seed_sources(campaign)
    entry = cfg.source_by_id("google_search") or cfg.source_by_type("google_search")
    conf = (entry.config or {}) if entry else {}
    resolved = normalize_queries(queries or conf.get("queries") or [])
    if not resolved:
        raise ValueError(
            "google_search requires config.queries in seed_sources.json "
            "(list of search strings)."
        )
    target = int(max_results or conf.get("max_results") or DEFAULT_TARGET)
    pages = int(pages_per_query or conf.get("pages_per_query") or DEFAULT_PAGES_PER_QUERY)
    per_page = int(conf.get("results_per_page") or DEFAULT_RESULTS_PER_PAGE)
    country = country_code or str(conf.get("country_code") or "")
    language = language_code or str(conf.get("language_code") or "")
    loc = location or str(conf.get("location") or "")
    segment = geo_segment or str(conf.get("geo_segment") or "") or (
        campaign.segment_order[0] if campaign.segment_order else "default"
    )
    excludes = list(exclude_domains or conf.get("exclude_domains") or [])
    actor_id = actor or conf.get("actor") or DEFAULT_ACTOR
    if conf.get("firmographics") is False:
        firmographics = False

    existing = _read_raw_seed_rows(campaign) if append else []
    if reuse_raw:
        existing = [r for r in existing if "Google Search" not in (r.get("notes") or "")]
    seen_hosts = {
        registrable_domain(host_of(r.get("website") or "")) for r in existing
    } - {""}
    seen_keys = {company_key(r) for r in existing} - {""}

    raw_path = campaign.campaign_dir / "google_search_raw.json"
    if reuse_raw:
        if not raw_path.is_file():
            raise FileNotFoundError(f"reuse_raw: {raw_path} not found; run without it first")
        items, apify_usd = json.loads(raw_path.read_text(encoding="utf-8")), 0.0
    else:
        items, apify_usd = scrape_google_search(
            resolved,
            pages_per_query=pages,
            results_per_page=per_page,
            country_code=country,
            language_code=language,
            actor_id=actor_id,
        )
        try:
            raw_path.write_text(
                json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        except OSError:
            pass

    rows, dropped = serp_items_to_seed_rows(
        items,
        geo_segment=segment,
        location=loc,
        exclude_domains=excludes,
        seen_hosts=seen_hosts,
    )
    rows = [r for r in rows if company_key(r) not in seen_keys][:target]
    if firmographics and rows:
        from agent.utils.firmographics import enrich_seed_rows_firmographics

        rows = enrich_seed_rows_firmographics(
            rows, prefer_enriched_website=False, match_by_name=False, fill_name=True
        )
    offset = len(existing)
    for i, row in enumerate(rows, start=offset + 1):
        row["seed_id"] = f"{campaign.seed_id_prefix}-{i:04d}"
    combined = existing + rows

    out = _write_raw_seeds_csv(campaign, combined)
    with campaign.seeds_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SEED_HEADERS, extrasaction="ignore")
        w.writeheader()
        for row in combined:
            w.writerow(row)

    touch_seed_source_run(
        campaign,
        entry.id if entry else "google_search",
        ref=str(out),
        seeds_added=len(rows),
        why_chosen="Google Search queries (Apify) → one seed per root domain",
        source_type="google_search",
        config_update={
            # One-off CLI overrides (queries / excludes) are not persisted over config.
            **({} if queries else {"queries": resolved}),
            **({} if exclude_domains else {"exclude_domains": excludes}),
            "max_results": target,
            "pages_per_query": pages,
            "results_per_page": per_page,
            "country_code": country,
            "language_code": language,
            "location": loc,
            "geo_segment": segment,
            "actor": actor_id,
            "firmographics": firmographics,
            "excluded_hits": len(dropped),
        },
    )
    if apify_usd > 0:
        _record_seed_apify_cost(
            campaign,
            apify_usd,
            source_id="google_search",
            label="Google search seeds",
            actor=actor_id,
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
    if not resolved_query:
        kws = conf.get("keywords") or conf.get("keyword") or ""
        if isinstance(kws, (list, tuple)):
            resolved_query = " ".join(str(k).strip() for k in kws if str(k).strip())
        elif isinstance(kws, str):
            resolved_query = kws.strip()
    resolved_title = title if title else (
        conf.get("title") or conf.get("titles") or conf.get("currentJobTitles") or ""
    )
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
            f"Apify people actor returned {len(items)} item(s) but no parseable /in/ URLs. "
            "Check seed locations match LinkedIn autocomplete (e.g. "
            "'Marbella, Andalusia, Spain' — not neighborhood aliases like 'puerto banus'). "
            f"Actor={actor_id} query={resolved_query!r} titles={resolved_title!r} "
            f"locations={resolved_location!r}."
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
        "Use csv_ingest, google_search, google_maps or linkedin_companies."
    )


REGISTRY: dict[str, Runner] = {
    "csv_ingest": run_csv_ingest,
    "google_maps": run_google_maps,
    "google_search": run_google_search,
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
