"""
Build a company seed list from LinkedIn jobs search results via Apify.

Uses curious_coder/linkedin-jobs-scraper by default (search URL -> jobs -> companies).
Output matches build_seed_list.py: seeds.csv, seeds.json, seeds_by_segment.json.

For the deterministic funnel stage output (stages/01_raw_seeds.csv), prefer:
  python scripts/run_seed_source.py --campaign {id} --source linkedin_jobs ...

Usage:
  python scripts/build_linkedin_jobs_seeds.py --campaign automata \\
    --url "https://www.linkedin.com/jobs/search/?keywords=Financial%20company&geoId=101165590"

  python scripts/build_linkedin_jobs_seeds.py --campaign automata \\
    --url-file data/campaigns/automata/job_search_urls.txt --count 300
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.utils.linkedin_jobs_seeds import (
    DEFAULT_ACTOR,
    DEFAULT_GEO_SEGMENT,
    LinkedInJobSeed,
    assign_seed_ids,
    jobs_to_seeds,
    load_linkedin_jobs_urls,
    scrape_linkedin_jobs,
)


def _write_outputs(
    campaign_dir: Path,
    seeds: list[LinkedInJobSeed],
    jobs: list[dict],
    dropped: list[dict],
    meta: dict,
) -> None:
    campaign_dir.mkdir(parents=True, exist_ok=True)
    fields = [
        "seed_id", "company_name", "website", "location", "geo_segment",
        "source_url", "status", "notes",
    ]

    csv_path = campaign_dir / "seeds.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for seed in seeds:
            w.writerow(asdict(seed))

    seed_dicts = [asdict(s) for s in seeds]
    (campaign_dir / "seeds.json").write_text(json.dumps(seed_dicts, indent=2), encoding="utf-8")

    by_seg: dict[str, list] = {}
    for seed in seed_dicts:
        by_seg.setdefault(seed["geo_segment"], []).append(seed)
    (campaign_dir / "seeds_by_segment.json").write_text(json.dumps(by_seg, indent=2), encoding="utf-8")

    (campaign_dir / "linkedin_jobs_raw.json").write_text(
        json.dumps(jobs, indent=2), encoding="utf-8"
    )
    (campaign_dir / "linkedin_jobs_dropped.json").write_text(
        json.dumps(dropped, indent=2), encoding="utf-8"
    )
    (campaign_dir / "linkedin_jobs_scrape_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )

    print(f"\nCampaign folder: {campaign_dir}")
    print(f"Seeds written: {len(seeds)} -> {csv_path}")
    for seg, rows in by_seg.items():
        print(f"  {seg}: {len(rows)}")
    print(f"Dropped: {len(dropped)} (see linkedin_jobs_dropped.json)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape LinkedIn jobs search into company seeds")
    parser.add_argument("--campaign", required=True, help="Campaign id (folder under data/campaigns/)")
    parser.add_argument("--url", action="append", default=[], help="LinkedIn jobs search URL (repeatable)")
    parser.add_argument("--url-file", help="Text file with one search URL per line")
    parser.add_argument("--count", type=int, default=200, help="Max jobs to scrape per run")
    parser.add_argument("--geo-segment", default=DEFAULT_GEO_SEGMENT, help="geo_segment value for all seeds")
    parser.add_argument("--seed-prefix", help="seed_id prefix (default: campaign id)")
    parser.add_argument("--actor", default=os.environ.get("APIFY_LINKEDIN_JOBS_SEARCH_ACTOR", DEFAULT_ACTOR))
    parser.add_argument(
        "--allow-no-website",
        action="store_true",
        help="Keep companies without a website (default: drop them)",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=True)
    urls = load_linkedin_jobs_urls(urls=args.url, url_file=args.url_file)
    campaign_dir = ROOT / "data" / "campaigns" / args.campaign
    prefix = args.seed_prefix or args.campaign

    jobs = scrape_linkedin_jobs(urls, count=args.count, actor_id=args.actor)
    seeds, dropped = jobs_to_seeds(
        jobs,
        geo_segment=args.geo_segment,
        require_website=not args.allow_no_website,
    )
    seeds = assign_seed_ids(seeds, prefix)

    meta = {
        "campaign_id": args.campaign,
        "actor": args.actor,
        "search_urls": urls,
        "count_requested": args.count,
        "jobs_returned": len(jobs),
        "seeds_written": len(seeds),
        "dropped_count": len(dropped),
        "geo_segment": args.geo_segment,
    }
    _write_outputs(campaign_dir, seeds, jobs, dropped, meta)


if __name__ == "__main__":
    main()
