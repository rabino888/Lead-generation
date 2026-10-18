"""
Talent T01 — Person seed scrape / CSV ingest → stages/T01_raw_people.csv.

Usage:
  python scripts/stage_talent_seed.py --campaign {id} --source people_csv_ingest --csv people.csv
  python scripts/stage_talent_seed.py --campaign {id} --source linkedin_people --url "…"
  python scripts/stage_talent_seed.py --campaign {id}   # uses enabled talent source in seed_sources.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.models import InputMode
from agent.utils import run_tracker
from agent.utils.campaign_config import load_campaign
from agent.utils.cost_manifest import finalize_stage_cost
from agent.utils.cost_tracker import get_cost_tracker, init_cost_tracker
from agent.utils.enrichment_plan import load_plan
from agent.utils.icp_rules import load_seed_sources
from agent.utils.seed_sources import run_source
from agent.utils.stage_runner import get_logger, load_client_profile

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
    parser.add_argument("--client-id", default=None)
    parser.add_argument(
        "--rebuild-cost-dashboard",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Rebuild cost ledger after logging (default: on)",
    )
    args = parser.parse_args()

    log = get_logger("stage_talent_seed")
    campaign = load_campaign(args.campaign)
    client = load_client_profile(campaign, client_id_override=args.client_id)
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

    started = datetime.now(timezone.utc)
    run_state = run_tracker.create_run(
        client_id=client.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=int(args.max_results or 0),
        keyword=f"stage_talent_seed:{campaign.campaign_id}",
    )
    run_id = run_state.run_id
    init_cost_tracker(run_id, client.client_id)
    log.info("Talent T01 seed run_id=%s campaign=%s source=%s", run_id, campaign.campaign_id, sid)

    try:
        out = run_source(args.campaign, sid, **kwargs)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    tracker = get_cost_tracker()
    summary = tracker.run_summary() if tracker else None
    n_rows = 0
    try:
        from agent.utils.talent_people import read_people_csv

        n_rows = len(read_people_csv(Path(out)))
    except Exception:
        pass

    report_path, manifest_path = finalize_stage_cost(
        campaign_dir=campaign.campaign_dir,
        campaign_id=campaign.campaign_id,
        client_id=client.client_id,
        run_id=run_id,
        phase="talent_people_seed",
        started_at=started,
        tracker_summary=summary,
        label=f"Talent people seed ({sid}, {n_rows} people)",
        outcome={"people_seeded": n_rows, "source": sid},
        extra={
            "source": sid,
            "output": str(out),
            "people": n_rows,
        },
        rebuild_dashboard=args.rebuild_cost_dashboard,
    )
    totals = (summary or {}).get("run_totals") or {}
    report = json.loads(report_path.read_text(encoding="utf-8"))

    print(f"T01 raw people -> {out}")
    print(f"run_id:   {run_id}")
    print(
        f"CostTracker: ${totals.get('total_usd', 0):.4f} "
        f"(apify=${totals.get('apify_usd', 0):.4f})"
    )
    print(f"Apify API truth (window): ${report.get('apify_truth_usd', 0):.4f}")
    if report.get("warning"):
        print(f"WARNING: {report['warning']}")
    print(f"Cost report: {report_path}")
    print(f"Cost manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
