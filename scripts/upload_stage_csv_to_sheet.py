"""
Replace leads on an existing campaign Google Sheet (do not create a new file).

Usage:
  python scripts/upload_stage_csv_to_sheet.py --campaign automata_us_rnd \\
      --csv data/campaigns/automata_us_rnd/stages/06_scored.csv \\
      --sheet-id 1c4x5dgTtaZq0oyUBfspdEw2jgBQhqklm-6zswYk_YkA

  # Or create a new sheet (legacy):
  python scripts/upload_stage_csv_to_sheet.py --campaign automata_us_rnd \\
      --csv ... --sheet-name "New upload"
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.integrations.sheets import replace_leads_on_sheet
from agent.models import (
    ClientProfile,
    ICPProfile,
    InputMode,
    RunState,
    RunStatus,
    SenderProfile,
)
from agent.stages import report as report_stage
from agent.utils.campaign_config import load_campaign, load_run_payload
from agent.utils.lead_from_csv import lead_from_row

# Same sheet as first 53-lead Automata US R&D upload (2026-08-26).
DEFAULT_AUTOMATA_SHEET_ID = "1c4x5dgTtaZq0oyUBfspdEw2jgBQhqklm-6zswYk_YkA"


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload stage CSV to Google Sheet")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--sheet-name", default=None, help="Create a NEW sheet with this name")
    parser.add_argument(
        "--sheet-id",
        default=None,
        help="Replace Leads tab on this existing spreadsheet ID (preferred for updates)",
    )
    parser.add_argument(
        "--same-sheet",
        action="store_true",
        help=f"Shorthand for --sheet-id {DEFAULT_AUTOMATA_SHEET_ID} (automata_us_rnd)",
    )
    parser.add_argument("--client-id", default=None)
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    payload = load_run_payload(campaign)
    sender = SenderProfile(**payload["sender"])
    if args.client_id:
        sender.client_id = args.client_id
    client = ClientProfile(
        client_id=sender.client_id,
        name=campaign.client_name,
        sender=sender,
        icp=ICPProfile(**payload["icp"]),
    )

    csv_path = Path(args.csv)
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    run_id = f"upload_{campaign.campaign_id}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    leads = [
        lead_from_row(row, run_id=run_id, client_id=client.client_id)
        for row in rows
        if (row.get("company_name") or "").strip()
        and (row.get("decision_maker_email") or "").strip()
    ]
    if not leads:
        print("ERROR: no contactable leads (need decision_maker_email)", file=sys.stderr)
        return 2

    sheet_id = args.sheet_id
    if args.same_sheet:
        sheet_id = DEFAULT_AUTOMATA_SHEET_ID

    if sheet_id:
        title = args.sheet_name or (
            f"{campaign.display_name} — {len(leads)} scored "
            f"{datetime.now(UTC).strftime('%Y-%m-%d')}"
        )
        url = replace_leads_on_sheet(sheet_id, leads, sheet_title=title)
        print(f"Leads: {len(leads)}")
        print(f"Updated existing sheet: {url}")
        return 0

    sheet_name = args.sheet_name or (
        f"{campaign.display_name} batch — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}"
    )
    run_state = RunState(
        run_id=run_id,
        client_id=client.client_id,
        status=RunStatus.COMPLETE,
        input_mode=InputMode.CURATED_SEEDS,
        keyword=sheet_name,
        max_leads=len(leads),
    )
    sheet_url, csv_out, _ = report_stage.run(
        qualified_leads=leads,
        partial_leads=[],
        client_profile=client,
        run_state=run_state,
        sheet_name=sheet_name,
    )
    print(f"Leads: {len(leads)}")
    print(f"Sheet: {sheet_url}")
    print(f"Local CSV copy: {csv_out}")
    return 0 if sheet_url else 1


if __name__ == "__main__":
    raise SystemExit(main())
