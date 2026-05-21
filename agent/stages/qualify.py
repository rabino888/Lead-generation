"""
Stage 5 — Full Qualification (Claude)
Deep-analyses each lead using all collected data.
Produces: lead_score, icp_match_score, pain_points, opportunities, notes.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from agent.integrations.llm import call_llm
from agent.models import ClientProfile, ICPProfile, Lead
from agent.utils.logger import get_run_logger


def run(
    leads: list[Lead],
    client_profile: ClientProfile,
    run_id: str,
    logger: Optional[logging.Logger] = None,
) -> list[Lead]:
    """
    Returns the same leads with qualification fields populated.
    """
    log = logger or get_run_logger(run_id, client_profile.client_id)
    log.info("Stage 5 — Qualification (%d leads)", len(leads))

    for lead in leads:
        _qualify_lead(lead, client_profile.icp, log)

    # Sort by lead_score descending
    leads.sort(key=lambda l: l.lead_score or 0, reverse=True)
    log.info("Stage 5 complete — leads scored and sorted")
    return leads


def _qualify_lead(lead: Lead, icp: ICPProfile, log: logging.Logger) -> None:
    """Qualify a single lead in-place."""

    # Build context summary for Claude
    context = _build_context(lead)

    prompt = f"""You are a senior B2B sales consultant qualifying a lead for a client.

CLIENT'S IDEAL CUSTOMER PROFILE:
{_describe_icp(icp)}

LEAD DATA:
{context}

Analyse this lead thoroughly and return a JSON object:
{{
  "icp_match_score": <0-100, how well this company matches the ICP>,
  "lead_score": <0-100, overall lead quality considering all factors>,
  "pain_points": [
    "specific problem 1 you can see evidence of",
    "specific problem 2",
    "..."
  ],
  "opportunities": [
    "specific service you could offer to address pain point 1",
    "specific service for pain point 2",
    "..."
  ],
  "qualification_notes": "<2-3 sentences explaining your scoring reasoning>"
}}

Scoring guide:
- 80-100: Hot lead — clear pain, strong ICP match, likely buying window
- 60-79:  Warm lead — good fit, some signals
- 40-59:  Cool lead — possible fit but weak signals
- 0-39:   Poor lead — wrong fit or no clear opportunity

Be specific. Reference actual evidence from the lead data.
"""

    try:
        data = call_llm(prompt, max_tokens=600, expect_json=True)
        lead.icp_match_score = int(data.get("icp_match_score", 50))
        lead.lead_score = int(data.get("lead_score", 50))
        lead.pain_points = data.get("pain_points") or []
        lead.opportunities = data.get("opportunities") or []
        lead.qualification_notes = data.get("qualification_notes", "")
        log.info("Qualified '%s' — score: %d", lead.company_name, lead.lead_score)
    except Exception as e:
        log.warning("Qualification failed for '%s': %s", lead.company_name, e)
        lead.lead_score = lead.prefilter_score if hasattr(lead, 'prefilter_score') else 50
        lead.icp_match_score = 50
        lead.qualification_notes = f"Auto-qualification failed: {str(e)[:100]}"


def _build_context(lead: Lead) -> str:
    """Build a rich text summary of the lead for Claude."""
    lines = [
        f"Company: {lead.company_name}",
        f"Website: {lead.website or 'Not found'}",
        f"Industry: {lead.industry or 'Unknown'}",
        f"Location: {lead.location or 'Unknown'}",
        f"Company size: {lead.company_size or 'Unknown'}",
        f"Founded: {lead.founded_year or 'Unknown'}",
    ]

    if lead.decision_maker:
        dm = lead.decision_maker
        lines.append(f"Decision-maker: {dm.name} — {dm.title}")

    sig = lead.apollo_signals
    if sig.intent_topics:
        lines.append(f"Researching (intent): {', '.join(sig.intent_topics)}")
    if sig.intent_strength:
        lines.append(f"Intent strength: {sig.intent_strength}")
    if sig.technologies_used:
        lines.append(f"Current tech stack: {', '.join(sig.technologies_used)}")
    if sig.funding_round:
        lines.append(f"Recent funding: {sig.funding_round}")
    if sig.hiring_signals:
        lines.append(f"Currently hiring: {', '.join(sig.hiring_signals[:3])}")
    if sig.job_change_alert:
        lines.append("⚡ Decision-maker recently changed role")

    wa = lead.website_analysis
    if wa:
        if wa.services_offered:
            lines.append(f"Services they sell: {', '.join(wa.services_offered[:5])}")
        if wa.tech_stack_detected:
            lines.append(f"Website tech: {', '.join(wa.tech_stack_detected)}")
        if wa.content_quality_score:
            lines.append(f"Content quality: {wa.content_quality_score}/10")
        if wa.seo_health:
            lines.append(f"SEO issues: {', '.join(wa.seo_health)}")
        if wa.has_chatbot is not None:
            lines.append(f"Has chatbot: {'Yes' if wa.has_chatbot else 'No'}")
        if wa.has_blog is not None:
            blog_str = "Yes" if wa.has_blog else "No"
            if wa.last_blog_post_date:
                blog_str += f" (last post: {wa.last_blog_post_date})"
            lines.append(f"Has blog: {blog_str}")
        if wa.social_proof is not None:
            lines.append(f"Has testimonials/case studies: {'Yes' if wa.social_proof else 'No'}")
        if wa.website_summary:
            lines.append(f"Website summary: {wa.website_summary}")

    return "\n".join(lines)


def _describe_icp(icp: ICPProfile) -> str:
    parts = []
    if icp.industry:
        parts.append(f"Target industry: {icp.industry}")
    if icp.location:
        parts.append(f"Target location: {icp.location}")
    if icp.company_size_min or icp.company_size_max:
        parts.append(f"Company size: {icp.company_size_min or '?'}–{icp.company_size_max or '?'} employees")
    if icp.keywords:
        parts.append(f"Relevant keywords: {', '.join(icp.keywords)}")
    return "\n".join(parts) if parts else "General B2B companies"
