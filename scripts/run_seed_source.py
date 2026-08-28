"""
Run a registered deterministic seed source into stages/01_raw_seeds.csv.

Usage:
  python scripts/run_seed_source.py --campaign {campaign_id} --source csv_ingest --csv path/to/seeds.csv
  python scripts/run_seed_source.py --campaign {campaign_id} --source linkedin_jobs --url "https://..."
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.utils.seed_sources import BLOCKED_APOLLO_SOURCES, list_sources, run_source


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic campaign seed source")
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--source",
        required=True,
        help=f"One of: {', '.join(list_sources())}",
    )
    parser.add_argument("--csv", default="", help="Input CSV for csv_ingest")
    parser.add_argument("--url", action="append", default=[], help="LinkedIn jobs search URL")
    parser.add_argument("--url-file", default="", help="File with LinkedIn jobs URLs")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--actor", default="")
    parser.add_argument("--geo-segment", default="")
    args = parser.parse_args()

    sid = args.source.strip().lower()
    if sid in BLOCKED_APOLLO_SOURCES:
        print(
            "ERROR: Apollo company search is not a registered seed source.",
            file=sys.stderr,
        )
        return 2

    kwargs: dict = {}
    if sid == "csv_ingest":
        if not args.csv:
            print("ERROR: --csv is required for csv_ingest", file=sys.stderr)
            return 2
        kwargs["csv_path"] = args.csv
    elif sid == "linkedin_jobs":
        kwargs["urls"] = args.url or None
        kwargs["url_file"] = args.url_file or None
        kwargs["count"] = args.count
        if args.actor:
            kwargs["actor"] = args.actor
        if args.geo_segment:
            kwargs["geo_segment"] = args.geo_segment

    try:
        out = run_source(args.campaign, sid, **kwargs)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote raw seeds -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
