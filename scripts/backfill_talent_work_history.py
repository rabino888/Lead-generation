"""Re-enrich talent T05 contactable work history via Apify + republish Sheet.

Usage:
  python scripts/backfill_talent_work_history.py --campaign decoracel_real_estate_advisors_in_marbella_new_lis_2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.utils.campaign_config import load_campaign
from agent.utils.cost_tracker import get_cost_tracker, init_cost_tracker
from agent.utils.talent_people import STAGE_T05_APOLLO_CONTACTABLE, talent_stage_path
from agent.utils.talent_profile import enrich_contactable_work_history
from agent.utils.talent_sheets import publish_talent_sheet


def main() -> int:
    parser = argparse.ArgumentParser(description="Apify work-history backfill for T05")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--client-id", default=None)
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    camp = Path(campaign.campaign_dir)
    cid = args.client_id or getattr(campaign, "client_id", None) or "local"

    from agent.utils import run_tracker
    from agent.models import InputMode

    run_state = run_tracker.create_run(
        client_id=cid,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=0,
        keyword=f"backfill_work_history:{campaign.campaign_id}",
    )
    init_cost_tracker(run_state.run_id, cid)

    t05 = talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE)
    if not t05.is_file():
        print(f"ERROR: missing {t05}", file=sys.stderr)
        return 2

    print(f"Backfilling work history for {campaign.campaign_id}…")
    enriched, missing = enrich_contactable_work_history(camp, only_missing=True)
    print(f"Enriched={enriched} still_missing={missing}")

    tracker = get_cost_tracker()
    if tracker:
        print(f"CostTracker: {tracker.run_summary()}")

    try:
        meta = publish_talent_sheet(camp, campaign.campaign_id, update=True)
        print("Sheet:", meta.get("sheet_url"))
    except Exception as exc:
        print(f"Sheet publish skipped: {exc}")

    try:
        from agent.utils.cost_dashboard import build_dashboard_index, persist_dashboard_index

        idx = build_dashboard_index(ROOT / "data")
        persist_dashboard_index(ROOT / "data", idx)
        print("Dashboard index rebuilt")
    except Exception as exc:
        print(f"Dashboard rebuild skipped: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
