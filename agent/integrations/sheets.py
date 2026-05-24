"""
Google Sheets integration.
- read_clients_sheet()   → list[ClientProfile]   (reads management sheet)
- write_leads_to_sheet() → (sheet_url, folder_url)
    Creates a new standalone Google Sheet inside the client's Drive folder.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from agent.models import ClientProfile, ICPProfile, Lead, EnrichmentStatus
from agent.utils.logger import log

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_gspread_client() -> gspread.Client:
    """
    Authenticate gspread.
    Prefers GOOGLE_SERVICE_ACCOUNT_FILE (local dev), falls back to
    GOOGLE_SERVICE_ACCOUNT JSON string (Railway).
    """
    file_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "").strip()

    if file_path and os.path.exists(file_path):
        creds = Credentials.from_service_account_file(file_path, scopes=SCOPES)
    elif json_str:
        info = json.loads(json_str)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        raise RuntimeError(
            "No Google credentials configured. "
            "Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT."
        )
    return gspread.authorize(creds)


# ── Client Management Sheet ───────────────────────────────────────────────────

def read_clients_sheet() -> list[ClientProfile]:
    """
    Read the LeadGen Clients management sheet.

    Expected columns (Ravi fills these in manually for each client):
    client_id | name | api_key | icp_industry | icp_location |
    icp_size_min | icp_size_max | icp_job_titles | client_email | active

    If a row has no api_key, one is auto-generated and written back to the sheet.
    Ravi can then copy it from the spreadsheet and give it to the client.
    """
    gc = _get_gspread_client()
    sheet_id = os.environ["CLIENTS_SHEET_ID"]
    ws = gc.open_by_key(sheet_id).sheet1

    # Get header row to locate the api_key column (1-indexed for cell updates)
    headers = ws.row_values(1)
    try:
        api_key_col = headers.index("api_key") + 1  # 1-indexed
    except ValueError:
        api_key_col = None

    rows = ws.get_all_records()

    clients: list[ClientProfile] = []
    for i, row in enumerate(rows):
        if not row.get("client_id"):
            continue

        sheet_row = i + 2  # 1-indexed row number (row 1 = header, row 2 = first data row)

        # ── Auto-generate API key if missing ────────────────────────────────
        api_key = str(row.get("api_key", "")).strip()
        if not api_key:
            if api_key_col:
                api_key = f"lgk-{secrets.token_urlsafe(32)}"
                ws.update_cell(sheet_row, api_key_col, api_key)
                log.info(
                    "Auto-generated API key for client '%s' — visible in the Clients sheet",
                    row["client_id"],
                )
            else:
                log.warning("Client '%s' has no api_key and no api_key column found", row["client_id"])
                continue  # Can't authenticate without a key

        # ── Job titles — strip accidental emails/URLs ────────────────────────
        job_titles_raw = str(row.get("icp_job_titles", "")).strip()
        job_titles = [
            t.strip() for t in job_titles_raw.split(",")
            if t.strip() and "@" not in t and "http" not in t
        ]
        if not job_titles:
            job_titles = ["CEO", "Founder", "Director", "Marketing Director", "Head of Marketing"]

        icp = ICPProfile(
            industry=row.get("icp_industry") or None,
            location=row.get("icp_location") or None,
            company_size_min=int(row["icp_size_min"]) if row.get("icp_size_min") else None,
            company_size_max=int(row["icp_size_max"]) if row.get("icp_size_max") else None,
            job_titles=job_titles,
        )

        active_val = str(row.get("active", "TRUE")).upper()
        clients.append(ClientProfile(
            client_id=str(row["client_id"]),
            name=str(row.get("name", "")),
            api_key=api_key,
            client_email=str(row.get("client_email", "")) or None,
            icp=icp,
            active=active_val in ("TRUE", "1", "YES"),
        ))

    return clients


# ── Deduplication Registry (Dedup tab) ───────────────────────────────────────
# Stored in the same Clients spreadsheet on a tab called "Dedup".
# This survives Railway redeploys — no local files needed.
# Columns: client_id | domain | company_name | date_delivered

def _get_or_create_dedup_ws(wb: gspread.Spreadsheet) -> gspread.Worksheet:
    """Return the Dedup worksheet, creating it with headers if it doesn't exist."""
    try:
        return wb.worksheet("Dedup")
    except gspread.WorksheetNotFound:
        ws = wb.add_worksheet(title="Dedup", rows=2000, cols=4)
        ws.append_row(
            ["client_id", "domain", "company_name", "date_delivered"],
            value_input_option="USER_ENTERED",
        )
        log.info("Sheets: created 'Dedup' tab in Clients spreadsheet")
        return ws


def get_seen_domains(client_id: str) -> set[str]:
    """
    Return the set of domains already delivered to this client.
    Reads from the Dedup tab in the Clients spreadsheet.
    """
    gc = _get_gspread_client()
    sheet_id = os.environ["CLIENTS_SHEET_ID"]
    wb = gc.open_by_key(sheet_id)
    ws = _get_or_create_dedup_ws(wb)

    rows = ws.get_all_records()
    domains = {
        str(r["domain"]).strip().lower()
        for r in rows
        if str(r.get("client_id", "")).strip() == client_id
        and r.get("domain")
    }
    return domains


def add_seen_domains(client_id: str, entries: list[dict]) -> None:
    """
    Append new domain entries to the Dedup tab.
    Each entry is a dict with keys: domain, company_name (optional).
    """
    if not entries:
        return

    gc = _get_gspread_client()
    sheet_id = os.environ["CLIENTS_SHEET_ID"]
    wb = gc.open_by_key(sheet_id)
    ws = _get_or_create_dedup_ws(wb)

    now = datetime.utcnow().isoformat()
    rows = [
        [client_id, e["domain"], e.get("company_name", ""), now]
        for e in entries
        if e.get("domain")
    ]
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        log.info("Sheets: added %d domains to Dedup tab for client %s", len(rows), client_id)


# ── Lead Output Sheet ─────────────────────────────────────────────────────────

def write_leads_to_sheet(
    sheet_name: str,
    qualified_leads: list[Lead],
    partial_leads: list[Lead],
    recommendations: Optional[str],
    run_id: str,
    client_profile: ClientProfile,
) -> tuple[str, str]:
    """
    Creates a new Google Sheet inside the client's Drive folder.
    Returns (sheet_url, folder_url).
    """
    from agent.integrations.drive import create_sheet_in_folder, get_or_create_client_folder

    # 1. Ensure client folder exists in shared Drive
    folder_id, folder_url = get_or_create_client_folder(
        client_name=client_profile.name,
        client_email=client_profile.client_email or "",
    )

    # 2. Create a new Google Sheet file inside that folder
    file_id = create_sheet_in_folder(sheet_name, folder_id)

    # 3. Open with gspread and write data
    gc = _get_gspread_client()
    spreadsheet = gc.open_by_key(file_id)
    ws = spreadsheet.sheet1
    ws.update_title("Leads")

    headers = [
        "lead_id", "company_name", "website", "industry", "location",
        "company_size", "founded_year", "company_linkedin",
        "decision_maker_name", "decision_maker_title", "decision_maker_email",
        "decision_maker_linkedin", "decision_maker_direct_phone",
        "company_phone", "company_generic_email",
        "intent_topics", "intent_strength", "technologies_used",
        "funding_round", "hiring_signals",
        "tech_stack", "services_offered", "content_quality_score",
        "seo_health", "has_chatbot", "has_blog", "last_blog_post",
        "social_proof", "website_summary",
        "icp_match_score", "lead_score", "pain_points",
        "opportunities", "qualification_notes",
        "personalized_hook", "recommended_first_service",
        "enrichment_status", "scraped_at",
    ]

    rows: list[list] = [headers]

    for lead in qualified_leads:
        rows.append(_lead_to_row(lead))

    if partial_leads:
        rows.append([""] * len(headers))
        rows.append([f"── PARTIAL LEADS ({len(partial_leads)}) ──"] + [""] * (len(headers) - 1))
        rows.append(headers)
        for lead in partial_leads:
            rows.append(_lead_to_row(lead))

    if recommendations:
        rows.append([""] * len(headers))
        rows.append(["── AI RECOMMENDATIONS ──"] + [""] * (len(headers) - 1))
        rows.append([recommendations] + [""] * (len(headers) - 1))

    ws.update(rows, value_input_option="USER_ENTERED")

    try:
        ws.format("1:1", {"textFormat": {"bold": True}})
    except Exception:
        pass

    sheet_url = f"https://docs.google.com/spreadsheets/d/{file_id}"
    log.info("Sheet written: %s | Folder: %s", sheet_url, folder_url)
    return sheet_url, folder_url


# ── Row builder ───────────────────────────────────────────────────────────────

def _lead_to_row(lead: Lead) -> list:
    dm = lead.decision_maker
    wa = lead.website_analysis
    sig = lead.apollo_signals

    def _attr(obj, attr, default=""):
        if obj is None:
            return default
        return getattr(obj, attr, default) or default

    return [
        lead.lead_id,
        lead.company_name,
        lead.website or "",
        lead.industry or "",
        lead.location or "",
        lead.company_size or "",
        lead.founded_year or "",
        lead.company_linkedin_url or "",
        _attr(dm, "name"),
        _attr(dm, "title"),
        _attr(dm, "email"),
        _attr(dm, "linkedin_url"),
        _attr(dm, "direct_phone"),
        lead.company_phone or "",
        lead.company_generic_email or "",
        ", ".join(sig.intent_topics),
        sig.intent_strength or "",
        ", ".join(sig.technologies_used),
        sig.funding_round or "",
        ", ".join(sig.hiring_signals),
        ", ".join(_attr(wa, "tech_stack_detected", [])),
        ", ".join(_attr(wa, "services_offered", [])),
        _attr(wa, "content_quality_score"),
        ", ".join(_attr(wa, "seo_health", [])),
        _attr(wa, "has_chatbot"),
        _attr(wa, "has_blog"),
        _attr(wa, "last_blog_post_date"),
        _attr(wa, "social_proof"),
        _attr(wa, "website_summary"),
        lead.icp_match_score or "",
        lead.lead_score or "",
        ", ".join(lead.pain_points),
        ", ".join(lead.opportunities),
        lead.qualification_notes or "",
        lead.personalized_hook or "",
        lead.recommended_first_service or "",
        lead.enrichment_status.value,
        lead.scraped_at.strftime("%Y-%m-%d %H:%M:%S"),
    ]
