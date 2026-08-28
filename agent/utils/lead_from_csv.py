"""
Rebuild Lead objects from a stage CSV (apollo_contactable / enriched / scored).
"""
from __future__ import annotations

from typing import Any, Optional

from agent.models import (
    Contact,
    EnrichmentStatus,
    InputMode,
    Lead,
    WebsiteAnalysis,
)


def _split(value: Any) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in str(value).split(";") if p.strip()]


def _parse_bool(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def _parse_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def lead_from_row(row: dict[str, Any], *, run_id: str, client_id: str) -> Lead:
    email = (row.get("decision_maker_email") or "").strip() or None
    dm = None
    if email or row.get("decision_maker_name") or row.get("decision_maker_linkedin"):
        dm = Contact(
            apollo_person_id=(row.get("decision_maker_apollo_person_id") or "").strip() or None,
            name=(row.get("decision_maker_name") or "").strip() or None,
            title=(row.get("decision_maker_title") or "").strip() or None,
            email=email,
            email_status=(row.get("decision_maker_email_status") or "").strip() or None,
            location=(row.get("decision_maker_location") or "").strip() or None,
            linkedin_url=(row.get("decision_maker_linkedin") or "").strip() or None,
            direct_phone=(row.get("decision_maker_direct_phone") or "").strip() or None,
            mobile_phone=(row.get("decision_maker_mobile_phone") or "").strip() or None,
            linkedin_interests=_split(row.get("decision_maker_linkedin_interests")),
            linkedin_post_summary=(row.get("decision_maker_linkedin_post_summary") or "").strip()
            or None,
            linkedin_about=(row.get("decision_maker_linkedin_about") or "").strip() or None,
            linkedin_headline=(row.get("decision_maker_linkedin_headline") or "").strip() or None,
            is_hiring=(row.get("decision_maker_is_hiring") or "").strip() or None,
        )

    has_wa = any(
        (row.get(k) or "").strip()
        for k in (
            "website_summary",
            "has_blog",
            "has_chatbot",
            "tech_stack",
            "services_offered",
            "seo_health",
            "website_careers_url",
        )
    )
    wa = None
    if has_wa:
        wa = WebsiteAnalysis(
            tech_stack_detected=_split(row.get("tech_stack")),
            services_offered=_split(row.get("services_offered")),
            content_quality_score=_parse_int(row.get("content_quality_score")),
            seo_health=_split(row.get("seo_health")),
            has_chatbot=_parse_bool(row.get("has_chatbot")),
            has_blog=_parse_bool(row.get("has_blog")),
            last_blog_post_date=(row.get("last_blog_post") or "").strip() or None,
            social_proof=_parse_bool(row.get("social_proof")),
            website_summary=(row.get("website_summary") or "").strip() or None,
            website_is_hiring=(row.get("website_is_hiring") or "").strip() or None,
            website_hiring_signals=_split(row.get("website_hiring_signals")),
            website_open_roles=_split(row.get("website_open_roles")),
            website_careers_url=(row.get("website_careers_url") or "").strip() or None,
        )

    lead_id = (row.get("lead_id") or "").strip() or f"lead_rebuild_{(row.get('company_name') or 'x')[:20]}"
    company_linkedin = (
        (row.get("company_linkedin") or row.get("company_linkedin_url") or "").strip() or None
    )
    return Lead(
        lead_id=lead_id,
        run_id=(row.get("run_id") or run_id).strip() or run_id,
        client_id=client_id,
        input_mode=InputMode.CURATED_SEEDS,
        company_name=(row.get("company_name") or "").strip() or "Unknown",
        website=(row.get("website") or "").strip() or None,
        industry=(row.get("industry") or "").strip() or None,
        location=(row.get("location") or "").strip() or None,
        company_size=(row.get("company_size") or "").strip() or None,
        company_linkedin_url=company_linkedin,
        company_linkedin_description=(row.get("company_linkedin_description") or "").strip() or None,
        company_is_hiring=(row.get("company_is_hiring") or "").strip() or None,
        company_open_jobs_count=_parse_int(row.get("company_open_jobs_count")),
        company_open_jobs_summary=(row.get("company_open_jobs_summary") or "").strip() or None,
        decision_maker=dm,
        website_analysis=wa,
        icp_match_score=_parse_int(row.get("icp_match_score")),
        lead_score=_parse_int(row.get("lead_score")),
        score_method=(row.get("score_method") or "").strip() or None,
        matched_terms=_split(row.get("matched_terms")),
        score_evidence=_split(row.get("score_evidence")),
        pain_points=_split(row.get("pain_points")),
        qualification_notes=(row.get("qualification_notes") or "").strip() or None,
        enrichment_status=EnrichmentStatus.COMPLETE if email else EnrichmentStatus.PARTIAL,
    )
