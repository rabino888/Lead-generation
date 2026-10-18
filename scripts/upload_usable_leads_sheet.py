"""
Add a Usable leads tab to the existing Apollo-vs-treg spreadsheet.

Duplicate emails are dropped (Apollo keeps the row when both sources match).
Rows with a LinkedIn URL are written to stages/04_enrich_ready.csv for stage_enrich.

Usage:
  python scripts/upload_usable_leads_sheet.py --campaign {id}
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

from agent.integrations.sheets import _get_gspread_client
from agent.utils.campaign_config import load_campaign, load_icp
from scripts.upload_source_compare_sheet import _score_row

FIELDS = [
    "company_name",
    "website",
    "domain",
    "company_size",
    "industry",
    "source",
    "full_name",
    "title",
    "email",
    "linkedin_url",
    "email_action",
    "same_email_note",
    "enrich_ready",
]


def _leads(rows: list[dict[str, str]], job_titles: list[str]) -> list[dict[str, str]]:
    leads: list[dict[str, str]] = []
    for row in rows:
        scored = _score_row(row, job_titles)
        base = {
            "company_name": row.get("company_name") or "",
            "website": row.get("website") or "",
            "domain": row.get("domain") or "",
            "company_size": row.get("company_size") or "",
            "industry": row.get("industry") or "",
        }
        if scored["apollo_usable"] == "yes":
            leads.append(
                {
                    **base,
                    "source": "apollo",
                    "full_name": row.get("apollo_name") or "",
                    "title": row.get("apollo_title") or "",
                    "email": (row.get("apollo_email") or "").strip(),
                    "linkedin_url": (row.get("apollo_linkedin") or "").strip(),
                }
            )
        if scored["treg_usable"] == "yes":
            leads.append(
                {
                    **base,
                    "source": "treg",
                    "full_name": row.get("treg_name") or "",
                    "title": row.get("treg_title") or "",
                    "email": (row.get("treg_email") or "").strip(),
                    "linkedin_url": (row.get("treg_linkedin") or "").strip(),
                }
            )
    seen: dict[str, dict[str, str]] = {}
    for lead in leads:
        key = lead["email"].lower()
        prior = seen.get(key)
        if prior is None:
            lead["email_action"] = "send"
            lead["same_email_note"] = ""
            seen[key] = lead
        else:
            lead["email_action"] = "duplicate — do not send"
            lead["same_email_note"] = (
                f"Same email as {prior['source']} {prior['full_name']} at {prior['company_name']}"
            )
        lead["enrich_ready"] = (
            "yes" if lead["email_action"] == "send" and lead["linkedin_url"] else "no"
        )
    return leads


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload usable leads to a new Google Sheet")
    parser.add_argument("--campaign", required=True)
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    icp = load_icp(campaign)
    csv_path = campaign.campaign_dir / "stages" / "04_source_compare.csv"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    leads = _leads(rows, list(icp.job_titles))
    unique = [lead for lead in leads if lead["email_action"] == "send"]
    ready_leads = [lead for lead in unique if lead["enrich_ready"] == "yes"]
    held = len(leads) - len(unique)
    missing_li = len(unique) - len(ready_leads)
    out_csv = campaign.campaign_dir / "stages" / "04_usable_leads.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(unique)

    meta_path = campaign.campaign_dir / "campaign_sheet.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    file_id = str(meta.get("spreadsheet_id") or "").strip()
    if not file_id:
        print("Missing compare spreadsheet id in campaign_sheet.json", file=sys.stderr)
        return 2
    extra_id = str(meta.get("usable_spreadsheet_id") or "").strip()

    gc = _get_gspread_client()
    spreadsheet = gc.open_by_key(file_id)
    try:
        ws = spreadsheet.worksheet("Usable leads")
        ws.clear()
    except Exception:
        ws = spreadsheet.add_worksheet(
            title="Usable leads",
            rows=max(len(unique) + 5, 20),
            cols=len(FIELDS),
        )
    ws.update(
        [FIELDS] + [[lead.get(field, "") for field in FIELDS] for lead in unique],
        value_input_option="USER_ENTERED",
    )
    try:
        ws.format("1:1", {"textFormat": {"bold": True}})
    except Exception:
        pass

    seeds = {
        (row.get("domain") or "").strip().lower(): row
        for row in csv.DictReader(
            (campaign.campaign_dir / "stages" / "03_deduped.csv").open(encoding="utf-8")
        )
    }
    enrich_fields = [
        "lead_id",
        "company_name",
        "website",
        "domain",
        "industry",
        "location",
        "company_size",
        "company_linkedin",
        "decision_maker_name",
        "decision_maker_title",
        "decision_maker_email",
        "decision_maker_linkedin",
        "contact_source",
    ]
    enrich_rows = []
    for index, lead in enumerate(ready_leads, start=1):
        seed = seeds.get(lead["domain"].lower(), {})
        enrich_rows.append(
            {
                "lead_id": f"usable-{index:03d}",
                "company_name": lead["company_name"],
                "website": lead["website"],
                "domain": lead["domain"],
                "industry": lead["industry"],
                "location": seed.get("location") or "",
                "company_size": lead["company_size"],
                "company_linkedin": seed.get("company_linkedin_url") or "",
                "decision_maker_name": lead["full_name"],
                "decision_maker_title": lead["title"],
                "decision_maker_email": lead["email"],
                "decision_maker_linkedin": lead["linkedin_url"],
                "contact_source": lead["source"],
            }
        )
    enrich_path = campaign.campaign_dir / "stages" / "04_enrich_ready.csv"
    with enrich_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=enrich_fields)
        writer.writeheader()
        writer.writerows(enrich_rows)

    sheet_url = f"https://docs.google.com/spreadsheets/d/{file_id}#gid={ws.id}"
    if extra_id and extra_id != file_id:
        try:
            from agent.integrations.drive import _get_service

            _get_service().files().update(
                fileId=extra_id,
                body={"trashed": True},
                supportsAllDrives=True,
            ).execute()
        except Exception as exc:
            print(f"Could not trash extra workbook {extra_id}: {exc}", file=sys.stderr)
    meta.pop("usable_spreadsheet_id", None)
    meta.update(
        {
            "usable_sheet_url": sheet_url,
            "usable_leads": len(unique),
            "usable_enrich_ready": len(ready_leads),
            "usable_updated_at": datetime.now(UTC).isoformat(),
        }
    )
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(sheet_url)
    print(
        json.dumps(
            {
                "unique_leads": len(unique),
                "duplicates_removed": held,
                "enrich_ready": len(ready_leads),
                "held_no_linkedin": missing_li,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
