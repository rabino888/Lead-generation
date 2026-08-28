"""
Stage 6 — Report Generation
Writes CSV + Google Sheet with collected prospect intelligence.
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from agent.integrations.sheets import write_leads_to_sheet
from agent.models import ClientProfile, Lead, RunState
from agent.utils.logger import get_run_logger


def run(
    qualified_leads: list[Lead],
    partial_leads: list[Lead],
    client_profile: ClientProfile,
    run_state: RunState,
    sheet_name: Optional[str],
    logger: Optional[logging.Logger] = None,
) -> tuple[Optional[str], Optional[str], None]:
    """
    Returns (sheet_url, csv_path, recommendations).
    recommendations is always None — outreach copy is handled outside this agent.
    """
    log = logger or get_run_logger(run_state.run_id, client_profile.client_id)
    log.info(
        "Stage 6 — Report (%d contactable, %d not-delivered)",
        len(qualified_leads), len(partial_leads),
    )

    if not sheet_name:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        keyword = run_state.keyword or "leads"
        sheet_name = f"{date_str} — {keyword}"

    csv_path = _write_csv(
        qualified_leads, partial_leads, client_profile, run_state.run_id, log
    )

    sheet_url = None
    folder_url = None
    import time
    for attempt in range(1, 4):
        try:
            sheet_url, folder_url = write_leads_to_sheet(
                sheet_name=sheet_name,
                qualified_leads=qualified_leads,
                partial_leads=partial_leads,
                run_id=run_state.run_id,
                client_profile=client_profile,
            )
            log.info("Client folder: %s", folder_url)
            break
        except Exception as e:
            if attempt < 3:
                log.warning(
                    "Google Sheet write failed (attempt %d/3): %s — retrying in %ds",
                    attempt, e, attempt * 3,
                )
                time.sleep(attempt * 3)
            else:
                log.error("Failed to write Google Sheet after 3 attempts: %s", e)

    log.info("Stage 6 complete — CSV: %s | Sheet: %s", csv_path, sheet_url)
    return sheet_url, csv_path, None


def _write_csv(
    qualified: list[Lead],
    partial: list[Lead],
    client_profile: ClientProfile,
    run_id: str,
    log: logging.Logger,
) -> str:
    """Write leads to CSV. Only contactable leads are delivered by default."""
    output_dir = Path("data") / "clients" / client_profile.client_id / "runs"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = str(output_dir / f"{run_id}.csv")

    fieldnames = [
        "lead_id", "company_name", "website", "industry", "location",
        "company_size", "founded_year", "company_linkedin",
        "decision_maker_apollo_person_id",
        "decision_maker_name", "decision_maker_title", "decision_maker_email",
        "decision_maker_email_status", "decision_maker_location", "decision_maker_linkedin",
        "decision_maker_direct_phone", "decision_maker_mobile_phone",
        "decision_maker_linkedin_interests", "decision_maker_linkedin_post_summary",
        "decision_maker_linkedin_about", "decision_maker_linkedin_headline", "decision_maker_is_hiring",
        "company_linkedin_description", "company_is_hiring", "company_open_jobs_count",
        "company_open_jobs_summary",
        "company_phone", "company_generic_email",
        "intent_topics", "intent_strength", "technologies_used",
        "funding_round", "hiring_signals",
        "website_is_hiring", "website_hiring_signals", "website_open_roles", "website_careers_url",
        "tech_stack", "services_offered", "content_quality_score",
        "seo_health", "has_chatbot", "has_blog", "last_blog_post",
        "social_proof", "website_summary",
        "icp_match_score", "lead_score", "score_method", "matched_terms", "score_evidence",
        "pain_points", "pain_point_evidence",
        "opportunities", "qualification_notes",
        "enrichment_cost_usd",
        "enrichment_status", "scraped_at",
    ]

    def lead_to_dict(lead: Lead) -> dict:
        dm = lead.decision_maker
        wa = lead.website_analysis
        sig = lead.apollo_signals
        return {
            "lead_id": lead.lead_id,
            "company_name": lead.company_name,
            "website": lead.website or "",
            "industry": lead.industry or "",
            "location": lead.location or "",
            "company_size": lead.company_size or "",
            "founded_year": lead.founded_year or "",
            "company_linkedin": lead.company_linkedin_url or "",
            "decision_maker_apollo_person_id": dm.apollo_person_id if dm else "",
            "decision_maker_name": dm.name if dm else "",
            "decision_maker_title": dm.title if dm else "",
            "decision_maker_email": dm.email if dm else "",
            "decision_maker_email_status": dm.email_status if dm else "",
            "decision_maker_location": dm.location if dm else "",
            "decision_maker_linkedin": dm.linkedin_url if dm else "",
            "decision_maker_direct_phone": dm.direct_phone if dm else "",
            "decision_maker_mobile_phone": dm.mobile_phone if dm else "",
            "decision_maker_linkedin_interests": "; ".join(dm.linkedin_interests) if dm else "",
            "decision_maker_linkedin_post_summary": dm.linkedin_post_summary if dm else "",
            "decision_maker_linkedin_about": dm.linkedin_about if dm else "",
            "decision_maker_linkedin_headline": dm.linkedin_headline if dm else "",
            "decision_maker_is_hiring": dm.is_hiring if dm else "",
            "company_linkedin_description": lead.company_linkedin_description or "",
            "company_is_hiring": lead.company_is_hiring or "",
            "company_open_jobs_count": lead.company_open_jobs_count if lead.company_open_jobs_count is not None else "",
            "company_open_jobs_summary": lead.company_open_jobs_summary or "",
            "company_phone": lead.company_phone or "",
            "company_generic_email": lead.company_generic_email or "",
            "intent_topics": "; ".join(sig.intent_topics),
            "intent_strength": sig.intent_strength or "",
            "technologies_used": "; ".join(sig.technologies_used),
            "funding_round": sig.funding_round or "",
            "hiring_signals": "; ".join(sig.hiring_signals),
            "website_is_hiring": wa.website_is_hiring if wa else "",
            "website_hiring_signals": "; ".join(wa.website_hiring_signals if wa else []),
            "website_open_roles": "; ".join(wa.website_open_roles if wa else []),
            "website_careers_url": wa.website_careers_url if wa else "",
            "tech_stack": "; ".join(wa.tech_stack_detected if wa else []),
            "services_offered": "; ".join(wa.services_offered if wa else []),
            "content_quality_score": wa.content_quality_score if wa else "",
            "seo_health": "; ".join(wa.seo_health if wa else []),
            "has_chatbot": wa.has_chatbot if wa else "",
            "has_blog": wa.has_blog if wa else "",
            "last_blog_post": wa.last_blog_post_date if wa else "",
            "social_proof": wa.social_proof if wa else "",
            "website_summary": wa.website_summary if wa else "",
            "icp_match_score": lead.icp_match_score or "",
            "lead_score": lead.lead_score or "",
            "score_method": lead.score_method or "",
            "matched_terms": "; ".join(lead.matched_terms or []),
            "score_evidence": "; ".join(lead.score_evidence or []),
            "pain_points": "; ".join(lead.pain_points),
            "pain_point_evidence": "; ".join(lead.pain_point_evidence),
            "opportunities": "; ".join(lead.opportunities),
            "qualification_notes": lead.qualification_notes or "",
            "enrichment_cost_usd": lead.enrichment_cost_usd if lead.enrichment_cost_usd is not None else "",
            "enrichment_status": lead.enrichment_status.value,
            "scraped_at": lead.scraped_at.strftime("%Y-%m-%d %H:%M:%S"),
        }

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for lead in qualified:
            writer.writerow(lead_to_dict(lead))

        if partial:
            writer.writerow({k: ("── PARTIAL LEADS ──" if k == "lead_id" else "") for k in fieldnames})
            for lead in partial:
                writer.writerow(lead_to_dict(lead))

    log.info("CSV written: %s (%d rows)", csv_path, len(qualified) + len(partial))
    return csv_path
