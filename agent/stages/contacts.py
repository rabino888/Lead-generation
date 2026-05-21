"""
Stage 4 — Contact Enrichment (Apollo)
Finds decision-makers at each company.
Splits results into qualified (decision-maker found) and partial (not found).
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from agent.integrations.apollo import enrich_contacts
from agent.models import (
    ClientProfile, EnrichmentStatus, InputMode, Lead,
    RawCompany, WebsiteAnalysis,
)
from agent.utils.logger import get_run_logger


def run(
    companies: list[RawCompany],
    website_analyses: dict[str, Optional[WebsiteAnalysis]],
    client_profile: ClientProfile,
    input_mode: InputMode,
    run_id: str,
    logger: Optional[logging.Logger] = None,
) -> tuple[list[Lead], list[Lead]]:
    """
    Returns (qualified_leads, partial_leads).
    Qualified = decision-maker email found.
    Partial   = no decision-maker, uses generic email, goes to end of report.
    """
    log = logger or get_run_logger(run_id, client_profile.client_id)
    log.info("Stage 4 — Contact enrichment (%d companies)", len(companies))

    qualified: list[Lead] = []
    partial: list[Lead] = []
    job_titles = client_profile.icp.job_titles

    for company in companies:
        wa = website_analyses.get(company.company_name)

        decision_maker, generic_email = enrich_contacts(
            company_name=company.company_name,
            website=company.website,
            job_titles=job_titles,
        )

        lead = Lead(
            lead_id=f"lead_{uuid.uuid4().hex[:10]}",
            run_id=run_id,
            client_id=client_profile.client_id,
            input_mode=input_mode,
            # Company data
            company_name=company.company_name,
            website=company.website,
            industry=company.industry,
            location=company.location,
            company_size=company.company_size,
            founded_year=company.founded_year,
            company_linkedin_url=company.company_linkedin_url,
            lead_source=company.lead_source,
            apollo_signals=company.apollo_signals,
            # Contacts
            decision_maker=decision_maker,
            company_generic_email=generic_email,
            # Website
            website_analysis=wa,
        )

        if decision_maker and decision_maker.email:
            lead.enrichment_status = EnrichmentStatus.COMPLETE
            qualified.append(lead)
            log.info("Qualified: %s (%s)", company.company_name, decision_maker.email)
        else:
            lead.enrichment_status = EnrichmentStatus.PARTIAL
            lead.partial_reason = "No decision-maker email found"
            partial.append(lead)
            log.info("Partial: %s — no decision-maker found", company.company_name)

    log.info(
        "Stage 4 complete — %d qualified, %d partial",
        len(qualified), len(partial)
    )
    return qualified, partial
