"""
Re-crawl thin / failed website enrichment rows (Firecrawl + careers + hiring LLM).

Targets leads where website_summary is empty, failed, or marked thin — does not
re-run LinkedIn Apify enrichment.

Guardrails (override with --force):
  - Exclusive per-campaign lock (.website_repair.lock)
  - Max WEBSITE_REPAIR_MAX_RUNS_PER_24H repair runs per 24h (default 2)
  - Auto LLM-only when markdown cache exists (no Firecrawl re-crawl)

Usage:
  python scripts/stage_website_repair.py --campaign automata_us_rnd
  python scripts/stage_website_repair.py --campaign automata_us_rnd --all --force
  python scripts/stage_website_repair.py --campaign automata_us_rnd --llm-only
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
from agent.utils.website_repair_guard import (
    RepairLock,
    assert_repair_run_budget,
    classify_repair_rows,
    row_needs_repair,
)


_OBSOLETE_WEBSITE_COLUMNS = {
    "website_hiring_signals",
    "tech_stack",
    "content_quality_score",
    "seo_health",
    "has_chatbot",
    "last_blog_post",
    "social_proof",
}


def _merge_rows(path: Path, updates: dict[str, dict]) -> None:
    if not path.exists():
        return
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = [f for f in (reader.fieldnames or []) if f not in _OBSOLETE_WEBSITE_COLUMNS]
        rows = [dict(r) for r in reader]
    for row in rows:
        email = (row.get("decision_maker_email") or "").strip().lower()
        upd = updates.get(email)
        if not upd:
            continue
        for k, v in upd.items():
            if k in _OBSOLETE_WEBSITE_COLUMNS:
                continue
            if k not in fields:
                fields.append(k)
            row[k] = "" if v is None else v
    bak = path.with_suffix(path.suffix + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(path, bak)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def _collect_updates(
    leads,
    analyses: dict,
) -> dict[str, dict]:
    updates: dict[str, dict] = {}
    for lead in leads:
        wa = analyses.get(lead.company_name)
        if not wa:
            continue
        lead.website_analysis = wa
        email = (lead.decision_maker.email or "").strip().lower() if lead.decision_maker else ""
        if email:
            updates[email] = lead_to_stage_row(lead)
    return updates


def _run_batch(
    repair_rows: list[dict],
    *,
    client,
    campaign,
    run_id: str,
    log,
    firecrawl: bool,
    label: str,
) -> dict[str, dict]:
    if not repair_rows:
        return {}
    leads = [
        lead_from_row(r, run_id=run_id, client_id=client.client_id) for r in repair_rows
    ]
    log.info("%s: %d rows (firecrawl=%s)", label, len(leads), firecrawl)
    analyses = website.run(
        leads,
        client,
        run_id,
        log,
        campaign_dir=campaign.campaign_dir,
        firecrawl=firecrawl,
    )
    return _collect_updates(leads, analyses)


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-crawl thin website enrichment rows")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--input", default="", help="Default: stages/05_enriched.csv")
    parser.add_argument("--client-id", default=None)
    parser.add_argument("--max-leads", type=int, default=200)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Re-enrich every row with a website (not only thin/failed summaries)",
    )
    parser.add_argument(
        "--llm-only",
        action="store_true",
        help="Only re-run LLM on cached markdown (no Firecrawl)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass lock and 24h run cap (operator override)",
    )
    parser.add_argument("--rebuild-cost-dashboard", action="store_true")
    args = parser.parse_args()

    log = get_logger("stage_website_repair")
    campaign = campaign_arg_load(args.campaign)
    client = load_client_profile(campaign, client_id_override=args.client_id)

    lock = RepairLock(campaign.campaign_dir, force=args.force)
    try:
        lock.acquire()
        assert_repair_run_budget(campaign.campaign_dir, force=args.force)
    except RuntimeError as e:
        print(f"Blocked: {e}", file=sys.stderr)
        return 1

    inp = None
    if args.input:
        inp = Path(args.input)
    else:
        inp = resolve_input_csv(campaign, input_path=None, default_stage_key="enriched")

    rows = read_csv_rows(inp)[: args.max_leads]
    if args.all:
        repair_rows = [r for r in rows if (r.get("website") or "").strip()]
    else:
        repair_rows = [r for r in rows if row_needs_repair(r)]
    if not repair_rows:
        print("No thin/failed website rows to repair.")
        lock.release()
        return 0

    if args.all and not args.llm_only:
        llm_rows, crawl_rows, skipped = [], repair_rows, []
    else:
        llm_rows, crawl_rows, skipped = classify_repair_rows(
            repair_rows,
            campaign.campaign_dir,
            llm_only_mode=args.llm_only,
        )
    if skipped:
        print(
            f"Skipping {len(skipped)} row(s): --llm-only set but no markdown cache "
            "(run full repair once or enrich with cache enabled)."
        )
        for row in skipped[:5]:
            print(f"  - {row.get('company_name')} ({row.get('website')})")
        if len(skipped) > 5:
            print(f"  ... and {len(skipped) - 5} more")

    if not llm_rows and not crawl_rows:
        print("No repairable rows after guardrail classification.")
        lock.release()
        return 1

    started = datetime.now(timezone.utc)
    run_state = run_tracker.create_run(
        client_id=client.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=len(llm_rows) + len(crawl_rows),
        keyword=f"stage_website_repair:{campaign.campaign_id}",
    )
    run_id = run_state.run_id
    init_cost_tracker(run_id, client.client_id)

    log.info(
        "Repair plan from %s: %d LLM-only, %d full crawl, %d skipped",
        inp,
        len(llm_rows),
        len(crawl_rows),
        len(skipped),
    )

    updates: dict[str, dict] = {}
    try:
        updates.update(
            _run_batch(
                llm_rows,
                client=client,
                campaign=campaign,
                run_id=run_id,
                log=log,
                firecrawl=False,
                label="LLM-only repair",
            )
        )
        updates.update(
            _run_batch(
                crawl_rows,
                client=client,
                campaign=campaign,
                run_id=run_id,
                log=log,
                firecrawl=True,
                label="Full crawl repair",
            )
        )
    finally:
        lock.release()

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
        label=f"Website repair ({len(llm_rows)} llm / {len(crawl_rows)} crawl)",
        outcome={
            "repaired": len(updates),
            "attempted": len(llm_rows) + len(crawl_rows),
            "llm_only": len(llm_rows),
            "full_crawl": len(crawl_rows),
            "skipped": len(skipped),
        },
        extra={
            "input": str(inp),
            "updates": len(updates),
            "llm_only": len(llm_rows),
            "full_crawl": len(crawl_rows),
        },
        rebuild_dashboard=args.rebuild_cost_dashboard,
    )
    print(f"Updated {len(updates)} rows ({len(llm_rows)} LLM-only, {len(crawl_rows)} full crawl)")
    print(f"Cost report: {report_path}")
    print(f"Cost manifest: {manifest_path}")
    print(f"run_id: {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
