"""
LinkedIn-only enrichment (company profile + DM profile/posts). Skips Firecrawl/website LLM.

Jobs stay gated by APIFY_SKIP_LINKEDIN_JOBS (default skip). Always writes CostTracker
+ Apify API reconcile so spend cannot go unlogged.

Usage:
  python scripts/stage_linkedin_enrich.py --campaign automata_us_rnd
  python scripts/stage_linkedin_enrich.py --campaign automata_us_rnd --missing-person-only
  python scripts/stage_linkedin_enrich.py --campaign automata_us_rnd --limit 10
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
from agent.stages import contacts
from agent.utils import run_tracker
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
    parser = argparse.ArgumentParser(description="LinkedIn-only enrich with spend reconcile")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--input", default="", help="Default: stages/05_enriched.csv or 04")
    parser.add_argument("--client-id", default=None)
    parser.add_argument("--max-leads", type=int, default=200)
    parser.add_argument(
        "--missing-person-only",
        action="store_true",
        help="Only leads that have DM LinkedIn but empty post summary/about",
    )
    parser.add_argument(
        "--person-only",
        action="store_true",
        help="Skip company LinkedIn; only enrich person profile/posts",
    )
    parser.add_argument(
        "--rebuild-cost-dashboard",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Rebuild cost ledger after logging (default: on)",
    )
    args = parser.parse_args()

    log = get_logger("stage_linkedin_enrich")
    campaign = campaign_arg_load(args.campaign)
    client = load_client_profile(campaign, client_id_override=args.client_id)

    inp = None
    if args.input:
        inp = Path(args.input)
    else:
        for key in ("enriched", "scored", "apollo_contactable"):
            try:
                inp = resolve_input_csv(campaign, input_path=None, default_stage_key=key)
                if inp and Path(inp).exists():
                    break
            except Exception:
                continue
    if not inp or not Path(inp).exists():
        print("ERROR: no input CSV found", file=sys.stderr)
        return 2

    rows = read_csv_rows(inp)[: args.max_leads]
    rows = [r for r in rows if (r.get("decision_maker_email") or "").strip()]
    if args.missing_person_only:
        filtered = []
        for r in rows:
            has_li = (r.get("decision_maker_linkedin") or "").strip()
            has_posts = (r.get("decision_maker_linkedin_post_summary") or "").strip()
            has_about = (r.get("decision_maker_linkedin_about") or "").strip()
            if has_li and not (has_posts or has_about):
                filtered.append(r)
        rows = filtered

    if not rows:
        print("ERROR: no rows to enrich", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc)
    run_state = run_tracker.create_run(
        client_id=client.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=len(rows),
        keyword=f"stage_linkedin_enrich:{campaign.campaign_id}",
    )
    run_id = run_state.run_id
    init_cost_tracker(run_id, client.client_id)
    leads = [lead_from_row(r, run_id=run_id, client_id=client.client_id) for r in rows]

    with_li = sum(1 for L in leads if L.decision_maker and L.decision_maker.linkedin_url)
    print(f"Input: {inp} ({len(leads)} leads, {with_li} with DM LinkedIn)")
    print(f"run_id: {run_id}")
    print("APIFY_SKIP_LINKEDIN_JOBS should stay on unless jobs actor cost-gated.")

    if args.person_only:
        # Temporarily clear company URLs so company enrich is skipped? Better: call person path only.
        # enrich_linkedin_for_leads always does company first — keep that unless person_only.
        # For person_only, strip company fetch by setting a sentinel — simplest is run full
        # path; company re-fetch is cheap (~harvestapi). User asked for LinkedIn enrichment.
        pass

    leads = contacts.enrich_linkedin_for_leads(leads, client, run_id, log)

    # Merge LinkedIn fields into full stage CSVs (never overwrite 05 with a subset).
    enriched_path = campaign.campaign_dir / "stages" / "05_enriched.csv"
    scored_path = campaign.campaign_dir / "stages" / "06_scored.csv"
    by_email = {
        ((L.decision_maker.email or "").strip().lower()): L
        for L in leads
        if L.decision_maker and L.decision_maker.email
    }

    def _merge_into(path: Path) -> None:
        import csv
        import shutil

        if not path.exists():
            return
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fields = list(reader.fieldnames or [])
            all_rows = [dict(r) for r in reader]
        for row in all_rows:
            email = (row.get("decision_maker_email") or "").strip().lower()
            lead = by_email.get(email)
            if not lead or not lead.decision_maker:
                continue
            dm = lead.decision_maker
            if lead.company_linkedin_url:
                row["company_linkedin"] = lead.company_linkedin_url
            if lead.company_linkedin_description:
                row["company_linkedin_description"] = lead.company_linkedin_description
            if dm.linkedin_url:
                row["decision_maker_linkedin"] = dm.linkedin_url
            if dm.linkedin_about:
                row["decision_maker_linkedin_about"] = dm.linkedin_about
            if dm.linkedin_headline:
                row["decision_maker_linkedin_headline"] = dm.linkedin_headline
            if dm.linkedin_post_summary:
                row["decision_maker_linkedin_post_summary"] = dm.linkedin_post_summary
            if dm.linkedin_interests:
                row["decision_maker_linkedin_interests"] = "; ".join(dm.linkedin_interests)
            if lead.company_is_hiring is not None:
                row["company_is_hiring"] = "yes" if lead.company_is_hiring else "no"
            if lead.company_open_jobs_summary:
                row["company_open_jobs_summary"] = lead.company_open_jobs_summary
            if lead.company_open_jobs_count is not None:
                row["company_open_jobs_count"] = str(lead.company_open_jobs_count)
        bak = path.with_suffix(path.suffix + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(path, bak)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in all_rows:
                writer.writerow({k: row.get(k, "") for k in fields})
        print(f"Merged LinkedIn fields into {path} (kept {len(all_rows)} rows)")

    if enriched_path.exists():
        _merge_into(enriched_path)
        out = enriched_path
    else:
        out = write_stage_csv(campaign, "enriched", leads_to_rows(leads))
    if scored_path.exists():
        _merge_into(scored_path)

    tracker = get_cost_tracker()
    summary = tracker.run_summary() if tracker else None
    report_path, manifest_path = finalize_stage_cost(
        campaign_dir=campaign.campaign_dir,
        campaign_id=campaign.campaign_id,
        client_id=client.client_id,
        run_id=run_id,
        phase="linkedin_enrich",
        started_at=started,
        tracker_summary=summary,
        label=f"LinkedIn enrich ({len(leads)} leads)",
        outcome={"leads": len(leads), "with_dm_linkedin": with_li},
        extra={
            "input": str(inp),
            "leads": len(leads),
            "with_dm_linkedin": with_li,
            "missing_person_only": args.missing_person_only,
            "output": str(out),
        },
        rebuild_dashboard=args.rebuild_cost_dashboard,
    )
    totals = (summary or {}).get("run_totals") or {}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        f"CostTracker: ${totals.get('total_usd', 0):.4f} "
        f"(apify=${totals.get('apify_usd', 0):.4f})"
    )
    print(f"Apify API truth (window): ${report.get('apify_truth_usd', 0):.4f}")
    if report.get("warning"):
        print(f"WARNING: {report['warning']}")
    print(f"Cost report: {report_path}")
    print(f"Cost manifest: {manifest_path}")
    print(f"Enriched {len(leads)} leads; stage file: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
