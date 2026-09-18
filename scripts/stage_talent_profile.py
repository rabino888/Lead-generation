"""
Talent T04 — Personal LinkedIn profile scrape on ICP survivors.

Reads stages/T03_icp_matched.csv by default. Writes T04_profiles.csv (ok) and
T04_profile_failed.csv. Always sets APIFY_SKIP_LINKEDIN_POSTS=1 for the run.
Appends cost_runs.json via finalize_stage_cost (same pattern as stage_linkedin_enrich).

Usage:
  python scripts/stage_talent_profile.py --campaign {campaign_id}
  python scripts/stage_talent_profile.py --campaign {id} --input path/to/T03.csv
  python scripts/stage_talent_profile.py --campaign {id} --limit 10
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.models import InputMode
from agent.utils import run_tracker
from agent.utils.cost_manifest import finalize_stage_cost
from agent.utils.cost_tracker import get_cost_tracker, init_cost_tracker
from agent.utils.stage_runner import campaign_arg_load, get_logger, load_client_profile
from agent.utils.talent_people import STAGE_T03_ICP_MATCHED
from agent.utils.talent_profile import run_talent_profile_stage


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Talent T04: LinkedIn personal profile → T04_profiles.csv / T04_profile_failed.csv"
    )
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--input",
        default="",
        help=f"CSV (default: stages/{STAGE_T03_ICP_MATCHED})",
    )
    parser.add_argument("--client-id", default=None)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max ICP survivor rows to scrape (default: all)",
    )
    parser.add_argument(
        "--rebuild-cost-dashboard",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Rebuild cost ledger after logging (default: on)",
    )
    args = parser.parse_args()

    log = get_logger("stage_talent_profile")
    # Talent T04 never scrapes posts — keep spend on profile actor only.
    os.environ["APIFY_SKIP_LINKEDIN_POSTS"] = "1"

    campaign = campaign_arg_load(args.campaign)
    client = load_client_profile(campaign, client_id_override=args.client_id)
    input_path = Path(args.input) if args.input else None

    started = datetime.now(timezone.utc)
    run_state = run_tracker.create_run(
        client_id=client.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=args.limit or 0,
        keyword=f"stage_talent_profile:{campaign.campaign_id}",
    )
    run_id = run_state.run_id
    init_cost_tracker(run_id, client.client_id)
    log.info(
        "Talent T04 profile run_id=%s campaign=%s skip_posts=%s",
        run_id,
        campaign.campaign_id,
        os.environ.get("APIFY_SKIP_LINKEDIN_POSTS"),
    )

    try:
        ok_path, failed_path, n_ok, n_fail = run_talent_profile_stage(
            campaign.campaign_dir,
            input_path=input_path,
            limit=args.limit,
            skip_posts=True,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Re-assert for cost window / any nested helpers.
    os.environ["APIFY_SKIP_LINKEDIN_POSTS"] = "1"

    tracker = get_cost_tracker()
    summary = tracker.run_summary() if tracker else None
    report_path, manifest_path = finalize_stage_cost(
        campaign_dir=campaign.campaign_dir,
        campaign_id=campaign.campaign_id,
        client_id=client.client_id,
        run_id=run_id,
        phase="talent_linkedin_profile",
        started_at=started,
        tracker_summary=summary,
        label=f"Talent profile ({n_ok} ok / {n_fail} failed)",
        outcome={"profiles_ok": n_ok, "profiles_failed": n_fail},
        extra={
            "input": str(input_path or (campaign.campaign_dir / "stages" / STAGE_T03_ICP_MATCHED)),
            "ok": n_ok,
            "failed": n_fail,
            "output_ok": str(ok_path),
            "output_failed": str(failed_path),
            "apify_skip_linkedin_posts": "1",
        },
        rebuild_dashboard=args.rebuild_cost_dashboard,
    )
    totals = (summary or {}).get("run_totals") or {}
    report = json.loads(report_path.read_text(encoding="utf-8"))

    print(f"Campaign: {campaign.campaign_id}")
    print(f"run_id:   {run_id}")
    print(f"Profiles ok:     {n_ok} -> {ok_path}")
    print(f"Profiles failed: {n_fail} -> {failed_path}")
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
