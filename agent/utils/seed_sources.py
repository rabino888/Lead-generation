"""
Deterministic seed-source registry.

Operator step 2 (LLM) chooses a source id; step 4 runs it with no LLM.
Apollo mixed_companies/search is intentionally NOT registered.
"""
from __future__ import annotations

import csv
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


def run_linkedin_jobs(
    campaign: CampaignConfig,
    *,
    urls: list[str] | None = None,
    url_file: str | Path | None = None,
    count: int = 100,
    actor: str | None = None,
    geo_segment: str = "",
) -> Path:
    """Scrape LinkedIn jobs via Apify and write stages/01_raw_seeds.csv."""
    from agent.utils.linkedin_jobs_seeds import (
        DEFAULT_ACTOR,
        DEFAULT_GEO_SEGMENT,
        assign_seed_ids,
        jobs_to_seeds,
        load_linkedin_jobs_urls,
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
    seeds, dropped = jobs_to_seeds(jobs, geo_segment=segment, require_website=True)
    seeds = assign_seed_ids(seeds, prefix=campaign.seed_id_prefix)

    rows = [asdict(s) for s in seeds]
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
        why_chosen="LinkedIn jobs search → company seeds",
        source_type="linkedin_jobs",
        config_update={
            "urls": resolved_urls,
            "count": count,
            "actor": actor_id,
            "geo_segment": segment,
            "dropped_count": len(dropped),
        },
    )
    return out


def _blocked_apollo(**_kwargs: Any) -> Path:
    raise RuntimeError(
        "Apollo company search (mixed_companies/search) is not a registered seed source. "
        "Use csv_ingest or linkedin_jobs. See docs/OPERATOR-WORKFLOW.md."
    )


REGISTRY: dict[str, Runner] = {
    "csv_ingest": run_csv_ingest,
    "linkedin_jobs": run_linkedin_jobs,
}


def list_sources() -> list[str]:
    return sorted(REGISTRY)


def run_source(campaign_id: str, source_id: str, **kwargs: Any) -> Path:
    sid = source_id.strip().lower()
    if sid in BLOCKED_APOLLO_SOURCES:
        return _blocked_apollo()
    if sid not in REGISTRY:
        raise KeyError(
            f"Unknown seed source '{source_id}'. "
            f"Registered: {', '.join(list_sources())}. "
            f"Apollo company search is blocked."
        )
    campaign = load_campaign(campaign_id)
    return REGISTRY[sid](campaign, **kwargs)
