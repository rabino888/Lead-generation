"""
Talent T01 — Person seed scrape / CSV ingest → stages/T01_raw_people.csv.

Usage:
  python scripts/stage_talent_seed.py --campaign {id} --source people_csv_ingest --csv people.csv
  python scripts/stage_talent_seed.py --campaign {id} --source linkedin_people --url "…"
  python scripts/stage_talent_seed.py --campaign {id}   # uses enabled talent source in seed_sources.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.utils.campaign_config import load_campaign
from agent.utils.enrichment_plan import load_plan
from agent.utils.icp_rules import load_seed_sources
from agent.utils.seed_sources import run_source

TALENT_SOURCES = ("people_csv_ingest", "linkedin_people")


def _pick_source(campaign_id: str, explicit: str) -> str:
    if explicit:
        sid = explicit.strip().lower()
        if sid not in TALENT_SOURCES:
            raise SystemExit(
                f"ERROR: --source must be one of {', '.join(TALENT_SOURCES)} (got {explicit!r})"
            )
        return sid
    campaign = load_campaign(campaign_id)
    cfg = load_seed_sources(campaign)
    for sid in TALENT_SOURCES:
        entry = cfg.source_by_id(sid)
        if entry and getattr(entry, "enabled", True):
            return sid
    # type-based lookup
    for src in cfg.sources:
        st = (getattr(src, "type", None) or src.id or "").strip().lower()
        if st in TALENT_SOURCES and getattr(src, "enabled", True):
            return st
    raise SystemExit(
        "ERROR: no enabled people_csv_ingest / linkedin_people in seed_sources.json; "
        "pass --source explicitly."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Talent T01: person seed → T01_raw_people.csv")
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--source",
        default="",
        help="people_csv_ingest | linkedin_people (default: first enabled talent source)",
    )
    parser.add_argument("--csv", default="", help="Person CSV for people_csv_ingest")
    parser.add_argument("--url", action="append", default=[], help="LinkedIn people-search URL")
    parser.add_argument("--url-file", default="")
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--actor", default="")
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    plan = load_plan(campaign.campaign_dir, campaign.campaign_id)
    ctype = (plan.get("campaign_type") or "").strip()
    # Soft warn only — CSV ingest may run before plan is saved as talent_search
    if ctype and ctype != "talent_search":
        print(
            f"WARNING: enrichment_plan campaign_type={ctype!r} "
            "(expected talent_search). Continuing T01 anyway.",
            file=sys.stderr,
        )

    sid = _pick_source(args.campaign, args.source)
    kwargs: dict = {}
    if sid == "people_csv_ingest":
        if args.csv:
            kwargs["csv_path"] = args.csv
    else:
        kwargs["urls"] = args.url or None
        kwargs["url_file"] = args.url_file or ""
        kwargs["max_results"] = args.max_results
        if args.actor:
            kwargs["actor"] = args.actor

    try:
        out = run_source(args.campaign, sid, **kwargs)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"T01 raw people -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
