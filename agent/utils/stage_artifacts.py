"""
Write / read deterministic funnel stage CSVs under data/campaigns/{id}/stages/.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from agent.models import Lead
from agent.utils.campaign_config import CampaignConfig
from agent.utils.icp_rules import ensure_stages_dir, stage_path


def _root_domain(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if str(url).startswith("http") else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def write_stage_csv(
    campaign: CampaignConfig,
    stage_key: str,
    rows: Iterable[dict[str, Any]],
    fieldnames: list[str] | None = None,
) -> Path:
    ensure_stages_dir(campaign)
    rows_list = list(rows)
    path = stage_path(campaign, stage_key)
    if not fieldnames:
        fieldnames = list(rows_list[0].keys()) if rows_list else ["company_name", "website"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows_list:
            w.writerow(row)
    return path


def read_stage_csv(campaign: CampaignConfig, stage_key: str) -> list[dict[str, str]]:
    path = stage_path(campaign, stage_key)
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def dedupe_seed_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Domain + name dedupe for stage 03. Returns (kept, dropped)."""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    seen_domains: set[str] = set()
    seen_names: set[str] = set()
    for row in rows:
        name = (row.get("company_name") or "").strip().lower()
        domain = _root_domain(row.get("website") or "")
        if domain and domain in seen_domains:
            out = dict(row)
            out["dedupe_reason"] = f"duplicate_domain:{domain}"
            dropped.append(out)
            continue
        if not domain and name and name in seen_names:
            out = dict(row)
            out["dedupe_reason"] = "duplicate_name"
            dropped.append(out)
            continue
        if domain:
            seen_domains.add(domain)
        if name:
            seen_names.add(name)
        kept.append(row)
    return kept, dropped


def _join(values: list[str] | None) -> str:
    return "; ".join(values or [])


def lead_to_stage_row(lead: Lead) -> dict[str, Any]:
    """Persist contact + LinkedIn + website enrichment fields for stage CSVs."""
    dm = lead.decision_maker
    wa = lead.website_analysis
    return {
        "lead_id": lead.lead_id,
        "company_name": lead.company_name,
        "website": lead.website or "",
        "industry": lead.industry or "",
        "location": lead.location or "",
        "company_size": lead.company_size or "",
        "company_linkedin": lead.company_linkedin_url or "",
        "company_linkedin_description": lead.company_linkedin_description or "",
        "company_is_hiring": lead.company_is_hiring or "",
        "company_open_jobs_count": (
            lead.company_open_jobs_count if lead.company_open_jobs_count is not None else ""
        ),
        "company_open_jobs_summary": lead.company_open_jobs_summary or "",
        "decision_maker_apollo_person_id": (dm.apollo_person_id if dm else "") or "",
        "decision_maker_name": (dm.name if dm else "") or "",
        "decision_maker_title": (dm.title if dm else "") or "",
        "decision_maker_email": (dm.email if dm else "") or "",
        "decision_maker_email_status": (dm.email_status if dm else "") or "",
        "decision_maker_location": (dm.location if dm else "") or "",
        "decision_maker_linkedin": (dm.linkedin_url if dm else "") or "",
        "decision_maker_direct_phone": (dm.direct_phone if dm else "") or "",
        "decision_maker_mobile_phone": (dm.mobile_phone if dm else "") or "",
        "decision_maker_linkedin_interests": _join(dm.linkedin_interests if dm else None),
        "decision_maker_linkedin_post_summary": (dm.linkedin_post_summary if dm else "") or "",
        "decision_maker_linkedin_about": (dm.linkedin_about if dm else "") or "",
        "decision_maker_linkedin_headline": (dm.linkedin_headline if dm else "") or "",
        "decision_maker_is_hiring": (dm.is_hiring if dm else "") or "",
        "website_is_hiring": (wa.website_is_hiring if wa else "") or "",
        "website_hiring_signals": _join(wa.website_hiring_signals if wa else None),
        "website_open_roles": _join(wa.website_open_roles if wa else None),
        "website_careers_url": (wa.website_careers_url if wa else "") or "",
        "tech_stack": _join(wa.tech_stack_detected if wa else None),
        "services_offered": _join(wa.services_offered if wa else None),
        "content_quality_score": (
            wa.content_quality_score if wa and wa.content_quality_score is not None else ""
        ),
        "seo_health": _join(wa.seo_health if wa else None),
        "has_chatbot": "" if not wa or wa.has_chatbot is None else wa.has_chatbot,
        "has_blog": "" if not wa or wa.has_blog is None else wa.has_blog,
        "last_blog_post": (wa.last_blog_post_date if wa else "") or "",
        "social_proof": "" if not wa or wa.social_proof is None else wa.social_proof,
        "website_summary": (wa.website_summary if wa else "") or "",
        "icp_match_score": lead.icp_match_score if lead.icp_match_score is not None else "",
        "lead_score": lead.lead_score if lead.lead_score is not None else "",
        "score_method": lead.score_method or "",
        "matched_terms": _join(lead.matched_terms),
        "score_evidence": _join(lead.score_evidence),
        "pain_points": _join(lead.pain_points),
        "qualification_notes": lead.qualification_notes or "",
        "run_id": lead.run_id,
    }


def qa_flags_for_leads(
    leads: list[Lead],
    *,
    contact_locations: list[str],
    borderline_max: int = 35,
) -> list[dict[str, Any]]:
    """Flag borderline scores, location mismatches, missing evidence for human QA."""
    flags: list[dict[str, Any]] = []
    loc_need = [c.lower() for c in contact_locations if c]
    scores = [l.lead_score for l in leads if l.lead_score is not None]
    for lead in leads:
        reasons: list[str] = []
        dm = lead.decision_maker
        if loc_need and dm and dm.location:
            loc = dm.location.lower()
            if not any(c in loc for c in loc_need):
                reasons.append("location_mismatch")
        if lead.lead_score is not None and lead.lead_score <= borderline_max:
            reasons.append(f"borderline_score:{lead.lead_score}")
        if not (lead.matched_terms or lead.pain_points):
            reasons.append("no_matched_terms")
        if reasons:
            row = lead_to_stage_row(lead)
            row["qa_flags"] = ";".join(reasons)
            flags.append(row)

    if scores and len(set(scores)) == 1:
        for lead in leads:
            row = lead_to_stage_row(lead)
            row["qa_flags"] = "score_spread_flat"
            flags.append(row)
    return flags
