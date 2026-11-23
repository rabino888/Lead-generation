"""
Publish a campaign's stage CSVs to one Google Sheet for auditing — one tab per stage.

Every kept and rejected stage file under stages/ that exists becomes a tab, plus a
Summary tab with row counts, so an operator can trace each lead from raw seed to
Apollo contact (and see why every rejected company was dropped).

The sheet lives in the client's Drive folder. Its id is saved to
{campaign_dir}/audit_sheet.json so re-running refreshes the same file.

Usage:
  python scripts/publish_audit_sheet.py --campaign {campaign_id}
  python scripts/publish_audit_sheet.py --campaign {campaign_id} --new   # force a new file
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.utils.campaign_config import CampaignConfig, load_campaign
from agent.utils.icp_rules import stage_path

# (stage key, tab title) in funnel order. Missing files are skipped.
AUDIT_TABS: tuple[tuple[str, str], ...] = (
    ("raw_seeds", "01 Raw seeds"),
    ("icp_matched", "02 ICP matched"),
    ("icp_rejected", "02 ICP rejected"),
    ("deduped", "03 Deduped"),
    ("pre_apollo_rejected", "03 Pre-Apollo rejected"),
    ("apollo_contactable", "04 Apollo contactable"),
    ("apollo_rejected", "04 Apollo rejected"),
    ("enriched", "05 Enriched"),
    ("scored", "06 Scored"),
    ("qa_flags", "07 QA flags"),
    ("outreach_segments", "08 Outreach segments"),
)


def _drop_empty_columns(header: list[str], rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """Stage CSVs carry many always-empty columns; hide them to keep tabs readable."""
    keep = [i for i in range(len(header)) if any((r[i] if i < len(r) else "").strip() for r in rows)]
    if not keep:
        return header, rows
    return [header[i] for i in keep], [[(r[i] if i < len(r) else "") for i in keep] for r in rows]


def read_stage_table(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        rows = [r for r in reader if any(c.strip() for c in r)]
    return _drop_empty_columns(header, rows)


def collect_tabs(campaign: CampaignConfig) -> list[tuple[str, list[str], list[list[str]]]]:
    tabs = []
    for key, title in AUDIT_TABS:
        path = stage_path(campaign, key)
        if path.is_file() and path.stat().st_size:
            header, rows = read_stage_table(path)
            tabs.append((title, header, rows))
    # Campaign-local lists (role splits, exclusions) survive a republish.
    lists_dir = campaign.campaign_dir / "lists"
    if lists_dir.is_dir():
        for path in sorted(lists_dir.glob("*.csv")):
            if path.is_file() and path.stat().st_size:
                header, rows = read_stage_table(path)
                tabs.append((path.stem, header, rows))
    return tabs


def _with_retry(fn, *args, **kwargs):
    """Sheets allows ~60 writes/min per user; back off on 429 instead of failing."""
    import time

    from gspread.exceptions import APIError

    for attempt in range(5):
        try:
            return fn(*args, **kwargs)
        except APIError as e:
            if "429" not in str(e) or attempt == 4:
                raise
            time.sleep(20 * (attempt + 1))


def _write_tab(spreadsheet, title: str, header: list[str], rows: list[list[str]], *, first: bool):
    values = [header] + rows
    n_rows, n_cols = max(len(values), 2), max(len(header), 1)
    if first:
        ws = spreadsheet.sheet1
        _with_retry(ws.update_title, title)
    else:
        ws = _with_retry(spreadsheet.add_worksheet, title=title, rows=n_rows, cols=n_cols)
    # RAW: never let Sheets evaluate scraped text as formulas.
    _with_retry(ws.update, values, value_input_option="RAW")
    return ws


def _format_headers(spreadsheet, sheets) -> None:
    """One batched write: bold + frozen header row on every tab."""
    requests = []
    for ws in sheets:
        requests.append(
            {
                "repeatCell": {
                    "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            }
        )
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount",
                }
            }
        )
    _with_retry(spreadsheet.batch_update, {"requests": requests})


def publish(campaign: CampaignConfig, *, force_new: bool = False) -> str:
    from agent.integrations.drive import create_sheet_in_folder, get_or_create_client_folder
    from agent.integrations.sheets import _get_gspread_client

    tabs = collect_tabs(campaign)
    if not tabs:
        raise SystemExit(f"No stage CSVs found under {campaign.campaign_dir / 'stages'}")

    meta_path = campaign.campaign_dir / "audit_sheet.json"
    gc = _get_gspread_client()
    meta: dict = {}
    if meta_path.is_file() and not force_new:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    if meta.get("spreadsheet_id"):
        spreadsheet = gc.open_by_key(meta["spreadsheet_id"])
        # Keep one sheet so the file is never empty, then rebuild every tab.
        keep, *extra = spreadsheet.worksheets()
        if extra:
            _with_retry(
                spreadsheet.batch_update,
                {"requests": [{"deleteSheet": {"sheetId": ws.id}} for ws in extra]},
            )
        _with_retry(keep.clear)
    else:
        title = f"{campaign.display_name} — Funnel audit — {datetime.now(UTC):%Y-%m-%d}"
        folder_id, folder_url = get_or_create_client_folder(campaign.client_name)
        file_id = create_sheet_in_folder(title, folder_id)
        spreadsheet = gc.open_by_key(file_id)
        meta = {
            "campaign_id": campaign.campaign_id,
            "spreadsheet_id": file_id,
            "sheet_url": f"https://docs.google.com/spreadsheets/d/{file_id}",
            "folder_url": folder_url,
            "created_at_utc": datetime.now(UTC).isoformat(),
        }

    summary_rows = [[t, str(len(rows))] for t, _, rows in tabs]
    written = [_write_tab(spreadsheet, "Summary", ["Stage", "Rows"], summary_rows, first=True)]
    for title, header, rows in tabs:
        written.append(_write_tab(spreadsheet, title, header, rows, first=False))
        print(f"  {title}: {len(rows)} rows")
    _format_headers(spreadsheet, written)

    meta["updated_at_utc"] = datetime.now(UTC).isoformat()
    meta["tabs"] = {t: len(rows) for t, _, rows in tabs}
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"Audit sheet: {meta['sheet_url']}")
    return meta["sheet_url"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish stage CSVs to an audit Google Sheet")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--new", action="store_true", help="Create a new file even if one exists")
    args = parser.parse_args()
    publish(load_campaign(args.campaign), force_new=args.new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
