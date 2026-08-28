"""
Re-crawl thin / failed website enrichment rows (Firecrawl + careers + hiring LLM).

Targets leads where website_summary is empty, failed, or marked thin — does not
re-run LinkedIn Apify enrichment.

Usage:
  python scripts/stage_website_repair.py --campaign automata_us_rnd
  python scripts/stage_website_repair.py --campaign automata_us_rnd --input stages/05_enriched.csv
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.stages import website
from agent.utils import run_tracker
from agent.models import InputMode
from agent.utils.cost_manifest import finalize_stage_cost
from agent.utils.cost_tracker import get_cost_tracker, init_cost_tracker
from agent.utils.lead_from_csv import lead_from_row
from agent.utils.stage_artifacts import lead_to_stage_row
from agent.utils.stage_runner import (
    campaign_arg_load,
    get_logger,
    load_client_profile,
    read_csv_rows,
    resolve_input_csv,
)


def _needs_repair(row: dict) -> bool:
    summary = (row.get("website_summary") or "").strip().lower()
    if not summary:
        return bool((row.get("website") or "").strip())
    thin_markers = (
        "thin site",
        "analysis failed",
        "pitch analysis failed",
        "careers page only",
    )
    return any(m in summary for m in thin_markers)


def _merge_rows(path: Path, updates: dict[str, dict]) -> None:
    if not path.exists():
        return
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    for row in rows:
        email = (row.get("decision_maker_email") or "").strip().lower()
        upd = updates.get(email)
        if not upd:
            continue
        for k, v in upd.items():
            if v:
                row[k] = v
    bak = path.with_suffix(path.suffix + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(path, bak)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-crawl thin website enrichment rows")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--input", default="", help="Default: stages/05_enriched.csv")
    parser.add_argument("--client-id", default=None)
    parser.add_argument("--max-leads", type=int, default=200)
    parser.add_argument("--rebuild-cost-dashboard", action="store_true")
    args = parser.parse_args()

    log = get_logger("stage_website_repair")
    campaign = campaign_arg_load(args.campaign)
    client = load_client_profile(campaign, client_id_override=args.client_id)

    inp = None
    if args.input:
        inp = Path(args.input)
    else:
        inp = resolve_input_csv(campaign, input_path=None, default_stage_key="enriched")

    rows = read_csv_rows(inp)[: args.max_leads]
    repair_rows = [r for r in rows if _needs_repair(r)]
    if not repair_rows:
        print("No thin/failed website rows to repair.")
        return 0

    started = datetime.now(timezone.utc)
    run_state = run_tracker.create_run(
        client_id=client.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=len(repair_rows),
        keyword=f"stage_website_repair:{campaign.campaign_id}",
    )
    run_id = run_state.run_id
    init_cost_tracker(run_id, client.client_id)

    leads = [
        lead_from_row(r, run_id=run_id, client_id=client.client_id) for r in repair_rows
    ]
    log.info("Repairing website enrichment for %d/%d rows from %s", len(leads), len(rows), inp)

    analyses = website.run(leads, client, run_id, log)
    updates: dict[str, dict] = {}
    for lead in leads:
        wa = analyses.get(lead.company_name)
        if not wa:
            continue
        lead.website_analysis = wa
        email = (lead.decision_maker.email or "").strip().lower() if lead.decision_maker else ""
        if email:
            updates[email] = lead_to_stage_row(lead)

    enriched_path = campaign.campaign_dir / "stages" / "05_enriched.csv"
    scored_path = campaign.campaign_dir / "stages" / "06_scored.csv"
    if Path(inp) == enriched_path or not inp:
        _merge_rows(enriched_path, updates)
    else:
        _merge_rows(Path(inp), updates)
    if scored_path.exists():
        _merge_rows(scored_path, updates)

    tracker = get_cost_tracker()
    summary = tracker.run_summary() if tracker else None
    report_path, manifest_path = finalize_stage_cost(
        campaign_dir=campaign.campaign_dir,
        campaign_id=campaign.campaign_id,
        client_id=client.client_id,
        run_id=run_id,
        phase="website_repair",
        started_at=started,
        tracker_summary=summary,
        label=f"Website repair ({len(leads)} leads)",
        outcome={"repaired": len(updates), "attempted": len(leads)},
        extra={"input": str(inp), "updates": len(updates)},
        rebuild_dashboard=args.rebuild_cost_dashboard,
    )
    print(f"Updated {len(updates)} rows with fresh website analysis")
    print(f"Cost report: {report_path}")
    print(f"Cost manifest: {manifest_path}")
    print(f"run_id: {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
