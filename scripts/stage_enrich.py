"""
Stage 05 — Firecrawl website + Apify LinkedIn enrichment.

Usage:
  python scripts/stage_enrich.py --campaign {campaign_id}
  python scripts/stage_enrich.py --campaign {campaign_id} --input path/to/04_apollo_contactable.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

import json

from agent.stages import contacts, website
from agent.utils import run_tracker
from agent.models import InputMode
from datetime import datetime, timezone

from agent.utils.cost_manifest import finalize_stage_cost
from agent.utils.cost_tracker import get_cost_tracker, init_cost_tracker
from agent.utils.lead_from_csv import lead_from_row
from agent.utils.stage_runner import (
    campaign_arg_load,
    get_logger,
    leads_to_rows,
    load_client_profile,
    read_csv_rows,
    resolve_input_csv,
    write_stage_csv,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 05: Firecrawl + Apify enrich")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--input", default="", help="CSV (default: stages/04_apollo_contactable.csv)")
    parser.add_argument("--client-id", default=None)
    parser.add_argument("--max-leads", type=int, default=50)
    parser.add_argument(
        "--rebuild-cost-dashboard",
        action="store_true",
        help="Rebuild cost_dashboard.html after logging (queries Apify API)",
    )
    args = parser.parse_args()

    log = get_logger("stage_enrich")
    campaign = campaign_arg_load(args.campaign)
    client = load_client_profile(campaign, client_id_override=args.client_id)
    inp = resolve_input_csv(
        campaign, input_path=args.input or None, default_stage_key="apollo_contactable"
    )
    rows = read_csv_rows(inp)[: args.max_leads]
    # Prefer rows with email
    rows = [r for r in rows if (r.get("decision_maker_email") or "").strip()]
    if not rows:
        print("ERROR: no contactable rows with email in input", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc)
    run_state = run_tracker.create_run(
        client_id=client.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=len(rows),
        keyword=f"stage_enrich:{campaign.campaign_id}",
    )
    run_id = run_state.run_id
    init_cost_tracker(run_id, client.client_id)
    leads = [lead_from_row(r, run_id=run_id, client_id=client.client_id) for r in rows]

    leads = contacts.enrich_linkedin_for_leads(leads, client, run_id, log)
    analyses = website.run(
        leads, client, run_id, log, campaign_dir=campaign.campaign_dir
    )
    for lead in leads:
        lead.website_analysis = analyses.get(lead.company_name) or lead.website_analysis

    out = write_stage_csv(campaign, "enriched", leads_to_rows(leads))
    tracker = get_cost_tracker()
    summary = tracker.run_summary() if tracker else None
    report_path, manifest_path = finalize_stage_cost(
        campaign_dir=campaign.campaign_dir,
        campaign_id=campaign.campaign_id,
        client_id=client.client_id,
        run_id=run_id,
        phase="stage_enrich",
        started_at=started,
        tracker_summary=summary,
        label=f"Firecrawl + Apify enrich ({len(leads)} leads)",
        outcome={"leads": len(leads)},
        extra={"input": str(inp), "leads": len(leads), "output": str(out)},
        rebuild_dashboard=args.rebuild_cost_dashboard,
    )
    totals = (summary or {}).get("run_totals") or {}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        f"CostTracker: ${totals.get('total_usd', 0):.4f} "
        f"(apify=${totals.get('apify_usd', 0):.4f}, "
        f"firecrawl=${totals.get('firecrawl_usd', 0):.4f}, "
        f"llm=${totals.get('llm_usd', 0):.4f}, "
        f"apollo=${totals.get('apollo_usd', 0):.4f})"
    )
    print(f"Apify API truth (window): ${report.get('apify_truth_usd', 0):.4f}")
    if report.get("warning"):
        print(f"WARNING: {report['warning']}")
    print(f"Cost report: {report_path}")
    print(f"Cost manifest: {manifest_path}")
    print(f"Enriched {len(leads)} -> {out}")
    print(f"run_id: {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
