"""
Publish stages/04_source_compare.csv to a Google Sheet and score usable contacts.

A contact is usable when the email is on the company domain and the title
matches the campaign ICP job titles.

Usage:
  python scripts/upload_source_compare_sheet.py --campaign {id}
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

from agent.integrations.drive import create_sheet_in_folder, get_or_create_client_folder
from agent.integrations.sheets import _get_gspread_client
from agent.utils.campaign_config import load_campaign, load_icp
from agent.utils.job_titles import title_matches_icp
from agent.utils.list_run import update_list_run

FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "icloud.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
}

SCORE_FIELDS = [
    "apollo_email_on_domain",
    "apollo_icp_title",
    "apollo_usable",
    "treg_email_on_domain",
    "treg_icp_title",
    "treg_usable",
    "emails_match",
    "value_note",
]


def _email_host(email: str) -> str:
    text = (email or "").strip().lower()
    if "@" not in text:
        return ""
    return text.split("@", 1)[1].strip()


def _on_company_domain(email: str, company_domain: str) -> bool:
    host = _email_host(email)
    domain = (company_domain or "").strip().lower().removeprefix("www.")
    if not host or not domain or host in FREE_EMAIL_DOMAINS:
        return False
    return host == domain or host.endswith("." + domain)


def _score_row(row: dict[str, str], job_titles: list[str]) -> dict[str, str]:
    domain = row.get("domain") or ""
    apollo_email = (row.get("apollo_email") or "").strip()
    treg_email = (row.get("treg_email") or "").strip()
    apollo_on = _on_company_domain(apollo_email, domain)
    treg_on = _on_company_domain(treg_email, domain)
    apollo_title = title_matches_icp(row.get("apollo_title") or "", job_titles)
    treg_title = title_matches_icp(row.get("treg_title") or "", job_titles)
    apollo_usable = bool(apollo_email) and apollo_on and apollo_title
    treg_usable = bool(treg_email) and treg_on and treg_title
    if apollo_email and treg_email:
        match = "yes" if apollo_email.lower() == treg_email.lower() else "no"
    else:
        match = ""
    if apollo_usable and treg_usable:
        note = "tie" if match == "yes" else "both_usable_different_person"
    elif apollo_usable:
        note = "apollo"
    elif treg_usable:
        note = "treg"
    else:
        note = "neither"
    return {
        "apollo_email_on_domain": "yes" if apollo_on else "no",
        "apollo_icp_title": "yes" if apollo_title else "no",
        "apollo_usable": "yes" if apollo_usable else "no",
        "treg_email_on_domain": "yes" if treg_on else "no",
        "treg_icp_title": "yes" if treg_title else "no",
        "treg_usable": "yes" if treg_usable else "no",
        "emails_match": match,
        "value_note": note,
    }


def _matrix(rows: list[dict[str, str]], fields: list[str]) -> list[list[str]]:
    return [[row.get(field, "") for field in fields] for row in rows]


def _upsert(spreadsheet, title: str, fields: list[str], rows: list[dict[str, str]]) -> None:
    try:
        ws = spreadsheet.worksheet(title)
        ws.clear()
    except Exception:
        ws = spreadsheet.add_worksheet(title=title, rows=max(len(rows) + 5, 20), cols=max(len(fields), 8))
    ws.update([fields] + _matrix(rows, fields), value_input_option="USER_ENTERED")
    try:
        ws.format("1:1", {"textFormat": {"bold": True}})
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload Apollo vs treg compare CSV to Google Sheets")
    parser.add_argument("--campaign", required=True)
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    icp = load_icp(campaign)
    csv_path = campaign.campaign_dir / "stages" / "04_source_compare.csv"
    summary_path = campaign.campaign_dir / "stages" / "04_source_compare_summary.json"
    if not csv_path.is_file():
        print(f"Missing {csv_path}", file=sys.stderr)
        return 2
    with csv_path.open(encoding="utf-8", newline="") as handle:
        raw_rows = list(csv.DictReader(handle))
    base_fields = list(raw_rows[0].keys()) if raw_rows else []
    scored = []
    for row in raw_rows:
        scored.append({**row, **_score_row(row, list(icp.job_titles))})
    fields = base_fields + [name for name in SCORE_FIELDS if name not in base_fields]

    prior = {}
    if summary_path.is_file():
        try:
            prior = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prior = {}

    def _yes(key: str) -> int:
        return sum(1 for row in scored if row.get(key) == "yes")

    apollo_usable = _yes("apollo_usable")
    treg_usable = _yes("treg_usable")
    treg_usd = float(prior.get("treg_usd") or 0)
    value_rows = [
        {"metric": "companies", "value": str(len(scored))},
        {"metric": "apollo_emails", "value": str(sum(1 for r in scored if (r.get("apollo_email") or "").strip()))},
        {"metric": "treg_emails", "value": str(sum(1 for r in scored if (r.get("treg_email") or "").strip()))},
        {"metric": "apollo_email_on_company_domain", "value": str(_yes("apollo_email_on_domain"))},
        {"metric": "treg_email_on_company_domain", "value": str(_yes("treg_email_on_domain"))},
        {"metric": "apollo_icp_title", "value": str(_yes("apollo_icp_title"))},
        {"metric": "treg_icp_title", "value": str(_yes("treg_icp_title"))},
        {"metric": "apollo_usable", "value": str(apollo_usable)},
        {"metric": "treg_usable", "value": str(treg_usable)},
        {"metric": "usable_winner_apollo", "value": str(sum(1 for r in scored if r["value_note"] == "apollo"))},
        {"metric": "usable_winner_treg", "value": str(sum(1 for r in scored if r["value_note"] == "treg"))},
        {"metric": "usable_tie_same_person", "value": str(sum(1 for r in scored if r["value_note"] == "tie"))},
        {"metric": "both_usable_different_person", "value": str(sum(1 for r in scored if r["value_note"] == "both_usable_different_person"))},
        {"metric": "neither_usable", "value": str(sum(1 for r in scored if r["value_note"] == "neither"))},
        {"metric": "treg_usd_metered", "value": f"{treg_usd:.4f}"},
        {
            "metric": "treg_usd_per_usable_email",
            "value": f"{(treg_usd / treg_usable):.4f}" if treg_usable else "",
        },
        {
            "metric": "usable_rule",
            "value": "Email is on the company website domain, and the title matches the ICP job titles.",
        },
    ]

    client_name = campaign.client_name or "Vista Business Brain"
    display = campaign.display_name or campaign.campaign_id
    folder_id, folder_url = get_or_create_client_folder(client_name)
    title = f"{display} — Apollo vs treg — {datetime.now(UTC).strftime('%Y-%m-%d')}"
    file_id = create_sheet_in_folder(title, folder_id)
    gc = _get_gspread_client()
    spreadsheet = gc.open_by_key(file_id)
    first = spreadsheet.sheet1
    first.update_title("Compare")
    first.update([fields] + _matrix(scored, fields), value_input_option="USER_ENTERED")
    try:
        first.format("1:1", {"textFormat": {"bold": True}})
    except Exception:
        pass
    _upsert(spreadsheet, "Value", ["metric", "value"], value_rows)

    sheet_url = f"https://docs.google.com/spreadsheets/d/{file_id}"
    now = datetime.now(UTC).isoformat()
    meta = {
        "campaign_id": campaign.campaign_id,
        "client_id": campaign.client_id,
        "client_name": client_name,
        "spreadsheet_id": file_id,
        "sheet_url": sheet_url,
        "folder_url": folder_url,
        "kind": "source_compare",
        "companies": len(scored),
        "apollo_usable": apollo_usable,
        "treg_usable": treg_usable,
        "created_at": now,
        "updated_at": now,
    }
    (campaign.campaign_dir / "campaign_sheet.json").write_text(
        json.dumps(meta, indent=2) + "\n",
        encoding="utf-8",
    )
    update_list_run(
        campaign.campaign_dir,
        sheet_url=sheet_url,
        message=(
            f"Apollo vs treg sheet ready. Usable contacts: Apollo {apollo_usable}, treg {treg_usable}. "
            f"{sheet_url}"
        ),
    )
    print(sheet_url)
    print(json.dumps({k: v for k, v in meta.items() if k.endswith("usable") or k == "companies"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
