"""
Stage 7 — Report Generation
Writes CSV + Google Sheet.
Generates the AI recommendations paragraph.
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from agent.integrations.llm import call_llm
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
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Returns (sheet_url, csv_path, recommendations).
    """
    log = logger or get_run_logger(run_state.run_id, client_profile.client_id)
    log.info(
        "Stage 7 — Report (%d qualified, %d partial)",
        len(qualified_leads), len(partial_leads)
    )

    # Generate AI recommendations paragraph
    recommendations = _generate_recommendations(
        qualified_leads, partial_leads, client_profile, log
    )

    # Build sheet name
    if not sheet_name:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        keyword = run_state.keyword or "leads"
        sheet_name = f"{date_str} — {keyword}"

    # Write CSV
    csv_path = _write_csv(
        qualified_leads, partial_leads, client_profile, run_state.run_id, log
    )

    # Write Google Sheet
    sheet_url = None
    try:
        sheet_url = write_leads_to_sheet(
            sheet_name=sheet_name,
            qualified_leads=qualified_leads,
            partial_leads=partial_leads,
            recommendations=recommendations,
            run_id=run_state.run_id,
        )
    except Exception as e:
        log.error("Failed to write Google Sheet: %s", e)

    log.info("Stage 7 complete — CSV: %s | Sheet: %s", csv_path, sheet_url)
    return sheet_url, csv_path, recommendations


def _generate_recommendations(
    qualified: list[Lead],
    partial: list[Lead],
    client_profile: ClientProfile,
    log: logging.Logger,
) -> Optional[str]:
    """Generate a batch-level AI recommendations paragraph."""
    all_leads = qualified + partial
    if not all_leads:
        return None

    # Summarise the batch for Claude
    summary_lines = []
    for lead in all_leads[:20]:  # Cap at 20 to avoid token overflow
        pain = "; ".join(lead.pain_points[:2]) if lead.pain_points else "none identified"
        summary_lines.append(
            f"- {lead.company_name} ({lead.industry or '?'}, score: {lead.lead_score or '?'}): {pain}"
        )

    prompt = f"""You are a senior B2B sales strategist reviewing a batch of qualified leads.

CLIENT: {client_profile.name}
TOTAL LEADS: {len(all_leads)} ({len(qualified)} qualified, {len(partial)} partial)

LEAD BATCH SUMMARY:
{chr(10).join(summary_lines)}

Write ONE paragraph (4-6 sentences) of strategic recommendations for this batch.
Include:
- Patterns you notice across the batch (common pain points, industries, sizes)
- Which leads or segments to prioritise first and why
- Any gaps or things a human reviewer might miss
- A specific next action to take with the top leads

Be direct, specific, and actionable. Do not use bullet points — write flowing prose.
"""

    try:
        text = call_llm(prompt, max_tokens=300, expect_json=False)
        log.info("AI recommendations generated (%d chars)", len(text))
        return text
    except Exception as e:
        log.warning("Recommendations generation failed: %s", e)
        return None


def _write_csv(
    qualified: list[Lead],
    partial: list[Lead],
    client_profile: ClientProfile,
    run_id: str,
    log: logging.Logger,
) -> str:
    """Write leads to CSV. Qualified first, partial section at end."""
    output_dir = Path("data") / "clients" / client_profile.client_id / "runs"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = str(output_dir / f"{run_id}.csv")

    fieldnames = [
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
            "decision_maker_name": dm.name if dm else "",
            "decision_maker_title": dm.title if dm else "",
            "decision_maker_email": dm.email if dm else "",
            "decision_maker_linkedin": dm.linkedin_url if dm else "",
            "decision_maker_direct_phone": dm.direct_phone if dm else "",
            "company_phone": lead.company_phone or "",
            "company_generic_email": lead.company_generic_email or "",
            "intent_topics": "; ".join(sig.intent_topics),
            "intent_strength": sig.intent_strength or "",
            "technologies_used": "; ".join(sig.technologies_used),
            "funding_round": sig.funding_round or "",
            "hiring_signals": "; ".join(sig.hiring_signals),
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
            "pain_points": "; ".join(lead.pain_points),
            "opportunities": "; ".join(lead.opportunities),
            "qualification_notes": lead.qualification_notes or "",
            "personalized_hook": lead.personalized_hook or "",
            "recommended_first_service": lead.recommended_first_service or "",
            "enrichment_status": lead.enrichment_status.value,
            "scraped_at": lead.scraped_at.strftime("%Y-%m-%d %H:%M:%S"),
        }

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for lead in qualified:
            writer.writerow(lead_to_dict(lead))

        if partial:
            # Visual separator
            writer.writerow({k: ("── PARTIAL LEADS ──" if k == "lead_id" else "") for k in fieldnames})
            for lead in partial:
                writer.writerow(lead_to_dict(lead))

    log.info("CSV written: %s (%d rows)", csv_path, len(qualified) + len(partial))
    return csv_path
