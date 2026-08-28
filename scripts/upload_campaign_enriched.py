"""
Upload canonical campaign CSVs to the master campaign Google Sheet.

Also creates per-segment Google Sheets in Drive when --create-run-sheets is set.

Usage:
  python scripts/upload_campaign_enriched.py --campaign {campaign_id}
  python scripts/upload_campaign_enriched.py --campaign {campaign_id} --create-run-sheets
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

from agent.integrations.sheets import _get_gspread_client
from agent.models import (
    ApolloSignals,
    ClientProfile,
    Contact,
    EnrichmentStatus,
    ICPProfile,
    InputMode,
    Lead,
    RunState,
    RunStatus,
    SenderProfile,
    WebsiteAnalysis,
)
from agent.stages import report as report_stage
from agent.utils.campaign_config import CampaignConfig, load_campaign, load_run_payload

ENRICHED_HEADERS = [
    "batch_id", "run_id", "lead_id", "seed_id", "company_name", "website",
    "location", "geo_segment", "industry", "company_size", "company_linkedin",
    "decision_maker_apollo_person_id", "decision_maker_name", "decision_maker_title",
    "decision_maker_email", "decision_maker_email_status", "decision_maker_linkedin",
    "decision_maker_linkedin_interests", "decision_maker_linkedin_post_summary",
    "decision_maker_linkedin_about", "decision_maker_linkedin_headline", "decision_maker_is_hiring",
    "company_linkedin_description", "company_is_hiring", "company_open_jobs_count",
    "company_open_jobs_summary",
    "decision_maker_direct_phone", "decision_maker_mobile_phone",
    "icp_match_score", "lead_score", "pain_points", "qualification_notes",
    "website_summary", "enriched_at",
]


def _load_state(campaign: CampaignConfig) -> dict:
    path = campaign.batch_state_path
    if not path.exists():
        raise FileNotFoundError(f"Missing batch state: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_seeds_by_domain(campaign: CampaignConfig) -> dict[str, str]:
    out: dict[str, str] = {}
    if not campaign.seeds_csv.exists():
        return out
    with campaign.seeds_csv.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            website = (row.get("website") or "").lower().replace("https://", "").replace("http://", "")
            domain = website.split("/")[0].removeprefix("www.")
            if domain:
                out[domain] = row.get("seed_id", "")
    return out


def _read_csv_rows(csv_path: Path) -> list[dict]:
    with csv_path.open(encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("lead_id") and not str(r.get("lead_id", "")).startswith("──")]


def _csv_to_enriched_rows(
    campaign: CampaignConfig,
    segment: str,
    run_id: str,
    csv_path: Path,
    seed_ids: dict[str, str],
) -> list[list]:
    now = datetime.now(UTC).isoformat()
    batch_id = f"{campaign.campaign_id}-{segment.lower().replace(' ', '-')}-upload"
    rows: list[list] = []
    for lead in _read_csv_rows(csv_path):
        website = (lead.get("website") or "").lower()
        domain = website.replace("https://", "").replace("http://", "").split("/")[0].removeprefix("www.")
        rows.append([
            batch_id,
            run_id,
            lead.get("lead_id", ""),
            seed_ids.get(domain, ""),
            lead.get("company_name", ""),
            lead.get("website", ""),
            lead.get("location", ""),
            segment,
            lead.get("industry", ""),
            lead.get("company_size", ""),
            lead.get("company_linkedin", ""),
            lead.get("decision_maker_apollo_person_id", ""),
            lead.get("decision_maker_name", ""),
            lead.get("decision_maker_title", ""),
            lead.get("decision_maker_email", ""),
            lead.get("decision_maker_email_status", ""),
            lead.get("decision_maker_linkedin", ""),
            lead.get("decision_maker_linkedin_interests", ""),
            lead.get("decision_maker_linkedin_post_summary", ""),
            lead.get("decision_maker_linkedin_about", ""),
            lead.get("decision_maker_linkedin_headline", ""),
            lead.get("decision_maker_is_hiring", ""),
            lead.get("company_linkedin_description", ""),
            lead.get("company_is_hiring", ""),
            lead.get("company_open_jobs_count", ""),
            lead.get("company_open_jobs_summary", ""),
            lead.get("decision_maker_direct_phone", ""),
            lead.get("decision_maker_mobile_phone", ""),
            lead.get("icp_match_score", ""),
            lead.get("lead_score", ""),
            lead.get("pain_points", ""),
            lead.get("qualification_notes", ""),
            lead.get("website_summary", ""),
            "new",
            now,
        ])
    return rows


def upload_master_enriched(
    campaign: CampaignConfig,
    create_run_sheets: bool = False,
    sheets_only: bool = False,
    replace_enriched: bool = False,
    force_run_sheets: bool = False,
) -> None:
    state = _load_state(campaign)
    meta_path = campaign.campaign_sheet_meta_path
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    seed_ids = _load_seeds_by_domain(campaign)

    gc = _get_gspread_client()
    spreadsheet = gc.open_by_key(meta["spreadsheet_id"])
    enriched_ws = spreadsheet.worksheet("Enriched")

    existing = enriched_ws.get_all_values()
    if len(existing) <= 1:
        enriched_ws.update([ENRICHED_HEADERS], value_input_option="USER_ENTERED")
        start_row = 2
    else:
        start_row = len(existing) + 1

    all_rows: list[list] = []
    uploaded_segments: list[str] = []

    for segment, info in state.get("segments", {}).items():
        if info.get("status") != "complete":
            print(f"SKIP {segment}: status={info.get('status')}")
            continue
        csv_path = ROOT / info["csv_path"]
        if not csv_path.exists():
            print(f"SKIP {segment}: missing CSV {csv_path}")
            continue
        run_id = info["canonical_run_id"]
        segment_rows = _csv_to_enriched_rows(campaign, segment, run_id, csv_path, seed_ids)
        all_rows.extend(segment_rows)
        uploaded_segments.append(f"{segment} ({len(segment_rows)})")
        print(f"Prepared {segment}: {len(segment_rows)} leads from {csv_path.name}")

        if create_run_sheets and (force_run_sheets or not info.get("sheet_url")):
            _create_run_sheet(campaign, segment, run_id, csv_path)

    if not all_rows:
        print("Nothing to upload.")
        return

    if not sheets_only:
        if replace_enriched:
            enriched_ws.clear()
            enriched_ws.update(
                [ENRICHED_HEADERS] + all_rows,
                value_input_option="USER_ENTERED",
            )
            meta["enriched_row_count"] = len(all_rows)
        else:
            enriched_ws.append_rows(all_rows, value_input_option="USER_ENTERED")
            meta["enriched_row_count"] = start_row - 1 + len(all_rows)
        meta["enriched_uploaded_at"] = datetime.now(UTC).isoformat()
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        print(f"\nUploaded {len(all_rows)} rows to Enriched tab.")
        print(f"Master sheet: {meta['sheet_url']}")
        print("Segments:", ", ".join(uploaded_segments))
    else:
        print(f"\nSheets-only mode — skipped master Enriched append ({len(all_rows)} rows prepared).")


def _create_run_sheet(
    campaign: CampaignConfig,
    segment: str,
    run_id: str,
    csv_path: Path,
) -> str | None:
    try:
        rows = _read_csv_rows(csv_path)
        leads: list[Lead] = []
        for row in rows:
            dm = Contact(
                apollo_person_id=row.get("decision_maker_apollo_person_id") or None,
                name=row.get("decision_maker_name") or None,
                title=row.get("decision_maker_title") or None,
                email=row.get("decision_maker_email") or None,
                email_status=row.get("decision_maker_email_status") or None,
                linkedin_url=row.get("decision_maker_linkedin") or None,
                direct_phone=row.get("decision_maker_direct_phone") or None,
                mobile_phone=row.get("decision_maker_mobile_phone") or None,
                linkedin_interests=[
                    p.strip() for p in (row.get("decision_maker_linkedin_interests") or "").split(";") if p.strip()
                ],
                linkedin_post_summary=row.get("decision_maker_linkedin_post_summary") or None,
                linkedin_about=row.get("decision_maker_linkedin_about") or None,
                linkedin_headline=row.get("decision_maker_linkedin_headline") or None,
                is_hiring=row.get("decision_maker_is_hiring") or None,
            )
            wa = WebsiteAnalysis(
                website_summary=row.get("website_summary") or None,
            ) if row.get("website_summary") else None
            leads.append(Lead(
                lead_id=row["lead_id"],
                company_name=row.get("company_name", ""),
                website=row.get("website") or None,
                industry=row.get("industry") or None,
                location=row.get("location") or None,
                company_size=row.get("company_size") or None,
                company_linkedin_url=row.get("company_linkedin") or None,
                company_linkedin_description=row.get("company_linkedin_description") or None,
                company_is_hiring=row.get("company_is_hiring") or None,
                company_open_jobs_count=int(row["company_open_jobs_count"]) if row.get("company_open_jobs_count") else None,
                company_open_jobs_summary=row.get("company_open_jobs_summary") or None,
                decision_maker=dm,
                website_analysis=wa,
                apollo_signals=ApolloSignals(),
                icp_match_score=int(row["icp_match_score"]) if row.get("icp_match_score") else None,
                lead_score=int(row["lead_score"]) if row.get("lead_score") else None,
                pain_points=[p.strip() for p in (row.get("pain_points") or "").split(";") if p.strip()],
                qualification_notes=row.get("qualification_notes") or None,
                enrichment_status=EnrichmentStatus.COMPLETE,
                run_id=run_id,
                client_id=campaign.client_id,
                input_mode=InputMode.CURATED_SEEDS,
                scraped_at=datetime.now(UTC),
            ))

        payload = load_run_payload(campaign)
        client = ClientProfile(
            client_id=campaign.client_id,
            name=campaign.client_name,
            sender=SenderProfile(**payload["sender"]),
            icp=ICPProfile(**payload["icp"]),
        )
        run_state = RunState(
            run_id=run_id,
            client_id=campaign.client_id,
            status=RunStatus.COMPLETE,
            input_mode=InputMode.CURATED_SEEDS,
            keyword=f"{campaign.display_name} {segment}",
            max_leads=len(leads),
        )
        sheet_url, _, _ = report_stage.run(
            qualified_leads=leads,
            partial_leads=[],
            client_profile=client,
            run_state=run_state,
            sheet_name=f"{campaign.display_name} {segment} — {datetime.now(UTC).strftime('%Y-%m-%d')}",
        )
        if sheet_url:
            state = _load_state(campaign)
            state["segments"][segment]["sheet_url"] = sheet_url
            campaign.batch_state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            print(f"  Created run sheet for {segment}: {sheet_url}")
        return sheet_url
    except Exception as exc:
        print(f"  WARN: could not create run sheet for {segment}: {exc}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload campaign enrichment CSVs to master sheet")
    parser.add_argument("--campaign", required=True, help="Campaign id under data/campaigns/")
    parser.add_argument(
        "--create-run-sheets",
        action="store_true",
        help="Also create per-segment Google Sheets in Drive for rows missing sheet_url",
    )
    parser.add_argument(
        "--sheets-only",
        action="store_true",
        help="Only create missing per-segment run sheets; do not append to master Enriched tab",
    )
    parser.add_argument(
        "--replace-enriched",
        action="store_true",
        help="Replace the master Enriched tab (clear and rewrite all rows)",
    )
    parser.add_argument(
        "--force-run-sheets",
        action="store_true",
        help="Recreate per-segment run sheets even if sheet_url exists",
    )
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    upload_master_enriched(
        campaign,
        create_run_sheets=args.create_run_sheets,
        sheets_only=args.sheets_only,
        replace_enriched=args.replace_enriched,
        force_run_sheets=args.force_run_sheets,
    )


if __name__ == "__main__":
    main()
