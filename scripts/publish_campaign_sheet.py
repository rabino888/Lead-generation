"""
Create or update a campaign master Google Sheet with a Seeds tab.

Reads campaign config and seeds from data/campaigns/{campaign_id}/.

Usage:
  python scripts/publish_campaign_sheet.py --campaign {campaign_id}
  python scripts/publish_campaign_sheet.py --campaign {campaign_id} --update
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

from agent.integrations.drive import create_sheet_in_folder, get_or_create_client_folder
from agent.integrations.sheets import _get_gspread_client
from agent.utils.campaign_config import (
    BATCH_LOG_HEADERS,
    ENRICHED_HEADERS,
    SEED_HEADERS,
    CampaignConfig,
    load_campaign,
)


def _read_seeds(campaign: CampaignConfig) -> list[list[str]]:
    seeds_csv = campaign.seeds_csv
    if not seeds_csv.exists():
        raise FileNotFoundError(
            f"Seed file not found: {seeds_csv}\n"
            f"Run: python scripts/build_seed_list.py --campaign {campaign.campaign_id}"
        )
    with seeds_csv.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [[row.get(h, "") for h in SEED_HEADERS] for row in rows]


def publish(campaign: CampaignConfig, update: bool = False) -> str:
    seeds = _read_seeds(campaign)
    meta_path = campaign.campaign_sheet_meta_path
    gc = _get_gspread_client()

    if update and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        file_id = meta["spreadsheet_id"]
        spreadsheet = gc.open_by_key(file_id)
        seeds_ws = spreadsheet.worksheet("Seeds")
        seeds_ws.clear()
        seeds_ws.update([SEED_HEADERS] + seeds, value_input_option="USER_ENTERED")
        seeds_ws.format("1:1", {"textFormat": {"bold": True}})
        sheet_url = meta["sheet_url"]
        meta["seed_count"] = len(seeds)
        meta["updated_at"] = datetime.now(UTC).isoformat()
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"Updated Seeds tab: {sheet_url} ({len(seeds)} rows)")
        return sheet_url

    sheet_title = (
        f"{campaign.display_name} — Seeds + Enrichment — "
        f"{datetime.utcnow().strftime('%Y-%m-%d')}"
    )

    folder_id, folder_url = get_or_create_client_folder(campaign.client_name)
    file_id = create_sheet_in_folder(sheet_title, folder_id)

    spreadsheet = gc.open_by_key(file_id)

    seeds_ws = spreadsheet.sheet1
    seeds_ws.update_title("Seeds")
    seeds_ws.update([SEED_HEADERS] + seeds, value_input_option="USER_ENTERED")
    seeds_ws.format("1:1", {"textFormat": {"bold": True}})

    enriched_ws = spreadsheet.add_worksheet(title="Enriched", rows=2000, cols=len(ENRICHED_HEADERS))
    enriched_ws.update([ENRICHED_HEADERS], value_input_option="USER_ENTERED")
    enriched_ws.format("1:1", {"textFormat": {"bold": True}})

    batch_ws = spreadsheet.add_worksheet(title="Batch Log", rows=200, cols=len(BATCH_LOG_HEADERS))
    batch_ws.update([BATCH_LOG_HEADERS], value_input_option="USER_ENTERED")
    batch_ws.format("1:1", {"textFormat": {"bold": True}})

    sheet_url = f"https://docs.google.com/spreadsheets/d/{file_id}"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(
            {
                "campaign_id": campaign.campaign_id,
                "client_id": campaign.client_id,
                "spreadsheet_id": file_id,
                "sheet_url": sheet_url,
                "folder_url": folder_url,
                "created_at": datetime.utcnow().isoformat(),
                "seed_count": len(seeds),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Campaign: {campaign.campaign_id}")
    print(f"Campaign sheet: {sheet_url}")
    print(f"Folder: {folder_url}")
    print(f"Seeds tab: {len(seeds)} rows")
    return sheet_url


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish campaign master Google Sheet")
    parser.add_argument("--campaign", required=True, help="Campaign id under data/campaigns/")
    parser.add_argument("--update", action="store_true", help="Update existing campaign sheet Seeds tab")
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    publish(campaign, update=args.update)


if __name__ == "__main__":
    main()
