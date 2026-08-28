"""
Apollo CSV fallback importer.
Used when Ravi exports contactable leads from Apollo manually and wants the
agent to run local enrichment, qualification, dedup, and reporting.
"""
from __future__ import annotations

import csv
import logging
import uuid
from pathlib import Path
from typing import Optional

from agent.models import (
    ApolloSignals,
    ClientProfile,
    Contact,
    EnrichmentStatus,
    InputMode,
    Lead,
    LeadSource,
)
from agent.utils.logger import get_run_logger


def run(
    csv_path: str,
    client_profile: ClientProfile,
    run_id: str,
    logger: Optional[logging.Logger] = None,
) -> list[Lead]:
    """Parse an Apollo export and return only contactable leads."""
    log = logger or get_run_logger(run_id, client_profile.client_id)
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Apollo CSV not found: {csv_path}")

    leads: list[Lead] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lead = _row_to_lead(row, client_profile, run_id)
            if lead:
                leads.append(lead)

    log.info("Apollo CSV import: %d contactable leads from %s", len(leads), csv_path)
    return leads


def _row_to_lead(row: dict, client_profile: ClientProfile, run_id: str) -> Optional[Lead]:
    email = _first(row, "email", "Email", "Work Email", "Person Email", "decision_maker_email")
    if not email:
        return None

    company_name = _first(row, "company", "Company", "Company Name", "Organization", "Account Name")
    if not company_name:
        return None

    website = _first(row, "website", "Website", "Company Website", "Organization Website", "domain", "Domain")
    if website and "." in website and not website.startswith("http"):
        website = f"https://{website}"

    contact = Contact(
        apollo_person_id=_first(row, "id", "Person ID", "Apollo Person ID"),
        name=_first(row, "name", "Name", "Full Name", "Person Name"),
        title=_first(row, "title", "Title", "Job Title"),
        email=email,
        email_status=_first(row, "email_status", "Email Status"),
        linkedin_url=_first(row, "linkedin_url", "LinkedIn", "Person Linkedin Url", "Person LinkedIn URL"),
        direct_phone=_first(row, "phone", "Phone", "Direct Phone", "Work Direct Phone"),
        mobile_phone=_first(row, "mobile_phone", "Mobile Phone", "Mobile", "Personal Phone"),
    )

    signals = ApolloSignals(
        intent_topics=_split_multi(_first(row, "intent_topics", "Intent Topics")),
        intent_strength=_first(row, "intent_strength", "Intent Strength"),
        technologies_used=_split_multi(_first(row, "technologies", "Technologies")),
        funding_round=_first(row, "funding_round", "Funding Round"),
        hiring_signals=_split_multi(_first(row, "job_postings", "Hiring Signals", "Job Postings")),
    )

    return Lead(
        lead_id=f"lead_{uuid.uuid4().hex[:10]}",
        run_id=run_id,
        client_id=client_profile.client_id,
        input_mode=InputMode.APOLLO_CSV,
        apollo_org_id=_first(row, "organization_id", "Organization ID", "Apollo Organization ID"),
        company_name=company_name,
        website=website,
        industry=_first(row, "industry", "Industry"),
        location=_first(row, "location", "Location", "Company Location"),
        company_size=_first(row, "company_size", "Employees", "# Employees", "Company Size"),
        founded_year=_to_int(_first(row, "founded_year", "Founded Year")),
        company_linkedin_url=_first(row, "company_linkedin", "Company Linkedin Url", "Company LinkedIn URL"),
        lead_source=LeadSource.APOLLO,
        apollo_signals=signals,
        decision_maker=contact,
        company_phone=_first(row, "company_phone", "Company Phone"),
        enrichment_status=EnrichmentStatus.COMPLETE,
    )


def _first(row: dict, *names: str) -> Optional[str]:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _split_multi(value: Optional[str]) -> list[str]:
    if not value:
        return []
    normalized = value.replace("|", ";").replace(",", ";")
    return [part.strip() for part in normalized.split(";") if part.strip()]


def _to_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None
