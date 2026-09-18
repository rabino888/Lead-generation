"""
Talent T05 — Apollo email reveal by LinkedIn URL → T05_apollo_contactable / rejected.

D3 gate: contactable rows require a revealed personal email. Phone is T06
(``stage_talent_phone.py`` / TS-8), not this stage.

Usage:
  python scripts/stage_talent_apollo.py --campaign {campaign_id}
  python scripts/stage_talent_apollo.py --campaign {id} --input path/to/profiles.csv
  python scripts/stage_talent_apollo.py --campaign {id} --include-failed-profiles
  python scripts/stage_talent_apollo.py --campaign {id} --max-leads 20
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

from agent.integrations.apollo import get_credits_used, reset_credits_used
from agent.models import InputMode
from agent.utils import run_tracker
from agent.utils.cost_manifest import finalize_stage_cost
from agent.utils.cost_tracker import get_cost_tracker, init_cost_tracker
from agent.utils.stage_runner import campaign_arg_load, get_logger, load_client_profile
from agent.utils.talent_apollo import run_talent_apollo_stage
from agent.utils.talent_people import STAGE_T04_PROFILES


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Talent T05: Apollo people/match by LinkedIn → personal email (D3)"
    )
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--input",
        default="",
        help=f"CSV (default: stages/{STAGE_T04_PROFILES})",
    )
    parser.add_argument("--max-leads", type=int, default=None, help="Cap Apollo attempts")
    parser.add_argument("--client-id", default=None)
    parser.add_argument(
        "--include-failed-profiles",
        action="store_true",
        help="Also attempt Apollo for profile_status failed/skipped (default: only ok)",
    )
    parser.add_argument(
        "--rebuild-cost-dashboard",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Rebuild cost ledger after logging (default: on)",
    )
    args = parser.parse_args()

    log = get_logger("stage_talent_apollo")
    campaign = campaign_arg_load(args.campaign)
    client = load_client_profile(campaign, client_id_override=args.client_id)
    input_path = Path(args.input) if args.input else None

    started = datetime.now(timezone.utc)
    reset_credits_used()
    run_state = run_tracker.create_run(
        client_id=client.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=args.max_leads or 0,
        keyword=f"stage_talent_apollo:{campaign.campaign_id}",
    )
    run_id = run_state.run_id
    init_cost_tracker(run_id, client.client_id)
    log.info("Talent Apollo stage run_id=%s", run_id)

    try:
        ok_path, rej_path, n_ok, n_rej = run_talent_apollo_stage(
            campaign.campaign_dir,
            input_path=input_path,
            include_failed_profiles=args.include_failed_profiles,
            max_leads=args.max_leads,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    credits = get_credits_used()
    tracker = get_cost_tracker()
    summary = tracker.run_summary() if tracker else None
    totals = (summary or {}).get("run_totals") or {}
    report_path, manifest_path = finalize_stage_cost(
        campaign_dir=campaign.campaign_dir,
        campaign_id=campaign.campaign_id,
        client_id=client.client_id,
        run_id=run_id,
        phase="stage_talent_apollo",
        started_at=started,
        tracker_summary=summary,
        label=f"Talent Apollo email ({n_ok} contactable)",
        outcome={"contactable": n_ok, "rejected": n_rej},
        extra={
            "input": str(input_path or (campaign.campaign_dir / "stages" / STAGE_T04_PROFILES)),
            "output": str(ok_path),
        },
        apollo_extra={
            "email_reveals": n_ok,
            "contactable": n_ok,
            "rejected": n_rej,
            "credits_consumed": credits,
        },
        rebuild_dashboard=args.rebuild_cost_dashboard,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    print(f"Contactable: {n_ok} -> {ok_path}")
    print(f"Rejected:    {n_rej} -> {rej_path}")
    print(
        f"Apollo credits consumed: {credits} "
        f"(CostTracker apollo_usd=${totals.get('apollo_usd', 0):.4f})"
    )
    print(f"CostTracker total_usd=${totals.get('total_usd', 0):.4f}")
    if report.get("apify_truth_usd"):
        print(f"Apify API truth (window): ${report.get('apify_truth_usd', 0):.4f}")
    print(f"Cost report: {report_path}")
    print(f"Cost manifest: {manifest_path}")
    print(f"run_id: {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
