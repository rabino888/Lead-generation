"""
Deterministic campaign orchestrator.

Funnel: seed source → ICP match → dedup → Apollo → enrich → keyword score → stage CSVs.
Apollo company search is never used for seeds.

Usage:
  python scripts/run_deterministic_campaign.py --campaign {campaign_id}
  python scripts/run_deterministic_campaign.py --campaign {campaign_id} --source csv_ingest --csv seeds.csv
  python scripts/run_deterministic_campaign.py --campaign {campaign_id} --match-only
  python scripts/run_deterministic_campaign.py --campaign {campaign_id} --max-leads 10 --client-id {client_id}-smoketest
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

from agent.models import CompanySeed, ICPProfile, InputMode, RunRequest, SenderProfile
from agent.pipeline import run_pipeline
from agent.utils import run_tracker
from agent.utils.campaign_config import load_campaign, load_run_payload
from agent.utils.icp_rules import ensure_stages_dir, load_icp_rules, stage_path
from agent.utils.pre_apollo_gate import filter_pre_apollo
from agent.utils.seed_sources import run_source
from agent.utils.seeds import load_company_seeds_csv
from agent.utils.apollo_contactable import write_apollo_contactable_csv
from agent.utils.stage_artifacts import (
    dedupe_seed_rows,
    read_stage_csv,
    write_stage_csv,
)
from main import _client_from_request
from scripts.match_campaign_icp import main as match_main


def _rows_to_seeds(rows: list[dict]) -> list[CompanySeed]:
    seeds: list[CompanySeed] = []
    for row in rows:
        name = (row.get("company_name") or "").strip()
        if not name:
            continue
        seeds.append(
            CompanySeed(
                company_name=name,
                website=(row.get("website") or "").strip() or None,
                industry=(row.get("industry") or "").strip() or None,
                location=(row.get("location") or "").strip() or None,
                company_size=(row.get("company_size") or "").strip() or None,
                source_url=(row.get("source_url") or "").strip() or None,
                notes=(row.get("notes") or "").strip() or None,
            )
        )
    return seeds


def _run_match(campaign_id: str, input_csv: str = "") -> int:
    argv = ["--campaign", campaign_id]
    if input_csv:
        argv.extend(["--input", input_csv])
    old = sys.argv
    try:
        sys.argv = ["match_campaign_icp.py", *argv]
        return int(match_main() or 0)
    finally:
        sys.argv = old


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic campaign funnel")
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--source",
        default="",
        help="Optional seed source: csv_ingest | linkedin_jobs",
    )
    parser.add_argument("--csv", default="", help="CSV for csv_ingest (or skip scrape if stages/01 exists)")
    parser.add_argument("--url", action="append", default=[], help="LinkedIn jobs URL")
    parser.add_argument("--url-file", default="")
    parser.add_argument("--match-only", action="store_true", help="Stop after ICP match stage")
    parser.add_argument("--max-leads", type=int, default=None)
    parser.add_argument("--client-id", default=None, help="Override client id (e.g. *-smoketest)")
    parser.add_argument("--sheet-name", default=None)
    parser.add_argument(
        "--skip-enrichment",
        action="store_true",
        help="Only build stages through 03_deduped (no Apollo)",
    )
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    ensure_stages_dir(campaign)
    rules = load_icp_rules(campaign)

    # ── Stage 4: seed scrape / ingest ─────────────────────────────────────────
    if args.source:
        kwargs: dict = {}
        if args.source == "csv_ingest":
            csv_path = args.csv or str(campaign.seeds_csv)
            kwargs["csv_path"] = csv_path
        elif args.source == "linkedin_jobs":
            kwargs["urls"] = args.url or None
            kwargs["url_file"] = args.url_file or None
        out = run_source(args.campaign, args.source, **kwargs)
        print(f"Raw seeds -> {out}")
    elif not stage_path(campaign, "raw_seeds").exists():
        if campaign.seeds_csv.exists():
            out = run_source(args.campaign, "csv_ingest", csv_path=campaign.seeds_csv)
            print(f"Ingested seeds.csv -> {out}")
        else:
            print("ERROR: no stages/01_raw_seeds.csv and no seeds.csv", file=sys.stderr)
            return 2

    # ── Stage 5: ICP match ────────────────────────────────────────────────────
    rc = _run_match(args.campaign)
    if rc != 0:
        return rc
    if args.match_only:
        return 0

    matched = read_stage_csv(campaign, "icp_matched")
    if not matched:
        print("ERROR: no ICP-matched seeds", file=sys.stderr)
        return 2

    # ── Stage 6: in-batch dedupe → 03_deduped ──────────────────────────────────
    kept, dropped = dedupe_seed_rows(matched)
    fields = list(matched[0].keys()) if matched else []
    if "dedupe_reason" not in fields:
        fields = fields + ["dedupe_reason"]
    write_stage_csv(campaign, "deduped", kept, fields)
    print(f"Deduped: {len(kept)} kept, {len(dropped)} in-batch duplicates dropped")

    # ── Stage 6b: pre-Apollo hard gate ─────────────────────────────────────────
    pre_kept, pre_rejected = filter_pre_apollo(kept, rules, campaign.campaign_dir)
    if pre_rejected:
        rej_fields = list(fields)
        if "pre_apollo_reason" not in rej_fields:
            rej_fields.append("pre_apollo_reason")
        write_stage_csv(campaign, "pre_apollo_rejected", pre_rejected, rej_fields)
        print(f"Pre-Apollo gate: {len(pre_kept)} kept, {len(pre_rejected)} rejected")
    kept = pre_kept
    write_stage_csv(campaign, "deduped", kept, fields)

    if args.skip_enrichment:
        print("Skipping Apollo/enrichment (--skip-enrichment)")
        return 0

    seeds = _rows_to_seeds(kept)
    payload = load_run_payload(campaign)
    sender = SenderProfile(**payload["sender"])
    if args.client_id:
        sender.client_id = args.client_id

    # Prefer machine icp.json titles/locations when present
    icp_data = dict(payload.get("icp") or {})
    if rules.job_titles:
        icp_data["job_titles"] = rules.job_titles
    if rules.contact_locations:
        icp_data["contact_locations"] = rules.contact_locations
    icp = ICPProfile(**icp_data)

    max_leads = args.max_leads or len(seeds)
    request = RunRequest(
        mode=InputMode.CURATED_SEEDS,
        max_leads=max_leads,
        sender=sender,
        icp=icp,
        company_seeds=seeds,
        output_sheet_name=args.sheet_name
        or f"{sender.name} — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}",
    )
    client = _client_from_request(request)
    run_state = run_tracker.create_run(
        client_id=client.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=max_leads,
        keyword=f"deterministic:{campaign.campaign_id}",
    )
    print(
        f"Starting deterministic run {run_state.run_id} — {len(seeds)} seeds, "
        f"max_leads={max_leads}, client={client.client_id}"
    )

    asyncio.run(run_pipeline(run_state.run_id, request, client))

    final = run_tracker.get_run(run_state.run_id)
    # Reconstruct leads from run CSV if available for stage artifacts
    csv_path = final.csv_path if final else None
    leads_rows: list[dict] = []
    if csv_path and Path(csv_path).exists():
        with Path(csv_path).open(encoding="utf-8", newline="") as f:
            leads_rows = list(csv.DictReader(f))

    contactable = [r for r in leads_rows if (r.get("decision_maker_email") or "").strip()]
    rejected_apollo = []
    # Companies that were in deduped but not in contactable (approx)
    contactable_names = {(r.get("company_name") or "").lower() for r in contactable}
    for row in kept:
        if (row.get("company_name") or "").lower() not in contactable_names:
            out = dict(row)
            out["apollo_reject_reason"] = "no_contactable_decision_maker"
            rejected_apollo.append(out)

    write_apollo_contactable_csv(campaign, contactable)
    write_stage_csv(
        campaign,
        "apollo_rejected",
        rejected_apollo,
        list(rejected_apollo[0].keys()) if rejected_apollo else ["company_name", "apollo_reject_reason"],
    )
    write_stage_csv(campaign, "enriched", contactable)
    write_stage_csv(campaign, "scored", contactable)

    # QA flags from scored CSV rows (lightweight)
    qa_rows = []
    for r in contactable:
        reasons = []
        try:
            score = int(r.get("lead_score") or -1)
        except ValueError:
            score = -1
        if 0 <= score <= 35:
            reasons.append(f"borderline_score:{score}")
        loc = (r.get("decision_maker_location") or "").lower()
        for need in rules.contact_locations:
            if need and loc and need.lower() not in loc:
                reasons.append("location_mismatch")
                break
        if not (r.get("matched_terms") or r.get("pain_points")):
            reasons.append("no_matched_terms")
        if reasons:
            out = dict(r)
            out["qa_flags"] = ";".join(reasons)
            qa_rows.append(out)
    write_stage_csv(campaign, "qa_flags", qa_rows)

    print(f"Status: {final.status if final else 'unknown'}")
    if final and final.sheet_url:
        print(f"Sheet: {final.sheet_url}")
    if csv_path:
        print(f"CSV:   {csv_path}")
    print(f"Stages: {campaign.stages_dir}")
    if final is None:
        return 1
    status = getattr(final.status, "value", str(final.status)).lower()
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
