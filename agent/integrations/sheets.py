"""
Google Sheets integration.
- read_clients_sheet()  → list[ClientProfile]
- write_leads_to_sheet() → sheet URL
"""
from __future__ import annotations

import json
import os
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


def _get_client() -> gspread.Client:
    """Authenticate and return a gspread client."""
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT"]
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def read_clients_sheet() -> list[ClientProfile]:
    """
    Read the LeadGen Clients management sheet.
    Expected columns: client_id | name | api_key | icp_industry | icp_location |
                      icp_size_min | icp_size_max | icp_job_titles | active
    """
    gc = _get_client()
    sheet_id = os.environ["CLIENTS_SHEET_ID"]
    ws = gc.open_by_key(sheet_id).sheet1
    rows = ws.get_all_records()

    clients: list[ClientProfile] = []
    for row in rows:
        if not row.get("client_id"):
            continue

        # Parse job titles (comma-separated string in sheet)
        job_titles_raw = str(row.get("icp_job_titles", "")).strip()
        job_titles = [t.strip() for t in job_titles_raw.split(",") if t.strip()] or [
            "CEO", "Founder", "Director", "Manager"
        ]

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
            api_key=str(row["api_key"]),
            icp=icp,
            active=active_val in ("TRUE", "1", "YES"),
        ))

    return clients


def write_leads_to_sheet(
    sheet_name: str,
    qualified_leads: list[Lead],
    partial_leads: list[Lead],
    recommendations: Optional[str],
    run_id: str,
) -> str:
    """
    Write all leads to a new tab in the outputs sheet.
    Returns the URL of the sheet.
    """
    gc = _get_client()
    sheet_id = os.environ["OUTPUTS_SHEET_ID"]
    spreadsheet = gc.open_by_key(sheet_id)

    # Create new worksheet tab
    tab_name = sheet_name[:100]  # Google Sheets tab name limit
    try:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=2000, cols=30)
    except Exception:
        # Tab already exists — append timestamp to make unique
        tab_name = f"{tab_name[:90]} {datetime.utcnow().strftime('%H%M%S')}"
        ws = spreadsheet.add_worksheet(title=tab_name, rows=2000, cols=30)

    headers = [
        "lead_id", "company_name", "website", "industry", "location",
        "company_size", "founded_year", "company_linkedin",
        # Contacts
        "decision_maker_name", "decision_maker_title", "decision_maker_email",
        "decision_maker_linkedin", "decision_maker_direct_phone",
        "company_phone", "company_generic_email",
        # Apollo signals
        "intent_topics", "intent_strength", "technologies_used",
        "funding_round", "hiring_signals",
        # Website
        "tech_stack", "services_offered", "content_quality_score",
        "seo_health", "has_chatbot", "has_blog", "last_blog_post",
        "social_proof", "website_summary",
        # Qualification
        "icp_match_score", "lead_score", "pain_points",
        "opportunities", "qualification_notes",
        # Outreach
        "personalized_hook", "recommended_first_service",
        # Meta
        "enrichment_status", "scraped_at",
    ]

    rows: list[list] = [headers]

    def lead_to_row(lead: Lead) -> list:
        dm = lead.decision_maker or {}
        wa = lead.website_analysis
        sig = lead.apollo_signals

        def _get(obj, attr, default=""):
            if obj is None:
                return default
            if isinstance(obj, dict):
                return obj.get(attr, default)
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
            # Contacts
            _get(lead.decision_maker, "name"),
            _get(lead.decision_maker, "title"),
            _get(lead.decision_maker, "email"),
            _get(lead.decision_maker, "linkedin_url"),
            _get(lead.decision_maker, "direct_phone"),
            lead.company_phone or "",
            lead.company_generic_email or "",
            # Apollo signals
            ", ".join(sig.intent_topics),
            sig.intent_strength or "",
            ", ".join(sig.technologies_used),
            sig.funding_round or "",
            ", ".join(sig.hiring_signals),
            # Website
            ", ".join(_get(wa, "tech_stack_detected", [])),
            ", ".join(_get(wa, "services_offered", [])),
            _get(wa, "content_quality_score"),
            ", ".join(_get(wa, "seo_health", [])),
            _get(wa, "has_chatbot"),
            _get(wa, "has_blog"),
            _get(wa, "last_blog_post_date"),
            _get(wa, "social_proof"),
            _get(wa, "website_summary"),
            # Qualification
            lead.icp_match_score or "",
            lead.lead_score or "",
            ", ".join(lead.pain_points),
            ", ".join(lead.opportunities),
            lead.qualification_notes or "",
            # Outreach
            lead.personalized_hook or "",
            lead.recommended_first_service or "",
            # Meta
            lead.enrichment_status.value,
            lead.scraped_at.strftime("%Y-%m-%d %H:%M:%S"),
        ]

    # Qualified leads
    for lead in qualified_leads:
        rows.append(lead_to_row(lead))

    # Visual separator + partial leads section
    if partial_leads:
        rows.append([""] * len(headers))
        rows.append([f"── PARTIAL LEADS ({len(partial_leads)}) ──"] + [""] * (len(headers) - 1))
        rows.append(headers)
        for lead in partial_leads:
            rows.append(lead_to_row(lead))

    # AI recommendations paragraph
    if recommendations:
        rows.append([""] * len(headers))
        rows.append(["── AI RECOMMENDATIONS ──"] + [""] * (len(headers) - 1))
        rows.append([recommendations] + [""] * (len(headers) - 1))

    # Write all rows at once
    ws.update(rows, value_input_option="USER_ENTERED")

    # Bold header row
    try:
        ws.format("1:1", {"textFormat": {"bold": True}})
    except Exception:
        pass  # Formatting is nice-to-have, not critical

    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
    log.info("Leads written to Google Sheet: %s (tab: %s)", sheet_url, tab_name)
    return sheet_url
