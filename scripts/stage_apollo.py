"""
Stage 04 — Apollo decision-maker search + email reveal (no enrichment).

Usage:
  python scripts/stage_apollo.py --campaign {campaign_id} --max-leads 20
  python scripts/stage_apollo.py --campaign {campaign_id} --input path/to/03_deduped.csv
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
from agent.stages import contacts
from agent.utils import run_tracker
from agent.utils.cost_manifest import finalize_stage_cost
from agent.utils.cost_tracker import get_cost_tracker, init_cost_tracker
from agent.utils.deduplication import filter_seen_contacts
from agent.utils.pre_apollo_gate import record_apollo_attempted_domains
from agent.utils.apollo_contactable import write_apollo_contactable_csv
from agent.utils.stage_runner import (
    campaign_arg_load,
    get_logger,
    leads_to_rows,
    load_client_profile,
    read_csv_rows,
    resolve_input_csv,
    rows_to_raw_companies,
    write_stage_csv,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 04: Apollo DM + email reveal")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--input", default="", help="CSV (default: stages/03_deduped.csv)")
    parser.add_argument("--max-leads", type=int, default=50)
    parser.add_argument("--client-id", default=None)
    parser.add_argument(
        "--rebuild-cost-dashboard",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Rebuild cost ledger after logging (default: on)",
    )
    args = parser.parse_args()

    log = get_logger("stage_apollo")
    campaign = campaign_arg_load(args.campaign)
    client = load_client_profile(campaign, client_id_override=args.client_id)
    inp = resolve_input_csv(campaign, input_path=args.input or None, default_stage_key="deduped")
    rows = read_csv_rows(inp)[: args.max_leads]
    record_apollo_attempted_domains(campaign.campaign_dir, rows)
    companies = rows_to_raw_companies(rows, client.icp)
    if not companies:
        print("ERROR: no companies in input", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc)
    reset_credits_used()
    run_state = run_tracker.create_run(
        client_id=client.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=args.max_leads,
        keyword=f"stage_apollo:{campaign.campaign_id}",
    )
    run_id = run_state.run_id
    init_cost_tracker(run_id, client.client_id)
    log.info("Apollo stage run_id=%s companies=%d", run_id, len(companies))

    qualified, rejected = contacts.run(
        companies=companies,
        client_profile=client,
        input_mode=InputMode.CURATED_SEEDS,
        run_id=run_id,
        logger=log,
    )
    qualified = filter_seen_contacts(client.client_id, qualified, log)

    q_rows = leads_to_rows(qualified)
    r_rows = leads_to_rows(rejected)
    for r in r_rows:
        r.setdefault("apollo_reject_reason", "no_contactable_decision_maker")

    out_q = write_apollo_contactable_csv(campaign, q_rows)
    out_r = write_stage_csv(
        campaign,
        "apollo_rejected",
        r_rows,
        list(r_rows[0].keys()) if r_rows else ["company_name", "apollo_reject_reason"],
    )

    credits = get_credits_used()
    tracker = get_cost_tracker()
    summary = tracker.run_summary() if tracker else None
    totals = (summary or {}).get("run_totals") or {}
    report_path, manifest_path = finalize_stage_cost(
        campaign_dir=campaign.campaign_dir,
        campaign_id=campaign.campaign_id,
        client_id=client.client_id,
        run_id=run_id,
        phase="stage_apollo",
        started_at=started,
        tracker_summary=summary,
        label=f"Apollo contact ({len(qualified)} contactable)",
        outcome={"contactable": len(qualified), "rejected": len(rejected)},
        extra={"input": str(inp), "companies": len(companies), "output": str(out_q)},
        apollo_extra={
            "companies_attempted": len(companies),
            "email_reveals": len(qualified),
            "contactable": len(qualified),
            "rejected": len(rejected),
            "credits_consumed": credits,
        },
        rebuild_dashboard=args.rebuild_cost_dashboard,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    print(f"Contactable: {len(qualified)} -> {out_q}")
    print(f"Rejected:    {len(rejected)} -> {out_r}")
    print(f"Apollo credits consumed: {credits} (CostTracker apollo_usd=${totals.get('apollo_usd', 0):.4f})")
    print(f"CostTracker total_usd=${totals.get('total_usd', 0):.4f}")
    if report.get("apify_truth_usd"):
        print(f"Apify API truth (window): ${report.get('apify_truth_usd', 0):.4f}")
    print(f"Cost report: {report_path}")
    print(f"Cost manifest: {manifest_path}")
    print(f"run_id: {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
