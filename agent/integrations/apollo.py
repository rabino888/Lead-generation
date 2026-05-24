"""
Apollo.io API integration.
- search_companies()   → list[RawCompany]   (Stage 1)
- enrich_contacts()    → Contact + generic email  (Stage 4)

Apollo basic plan (5,000 credits/month):
  - Company/people search: available
  - Email enrichment: 1 credit per verified email
  - Intent signals / technographics: premium — gracefully returns empty if unavailable
"""
from __future__ import annotations

import os
import time
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from agent.models import ApolloSignals, Contact, ICPProfile, RawCompany, LeadSource
from agent.utils.logger import log

APOLLO_BASE = "https://api.apollo.io/api/v1"
_credits_used = 0


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": os.environ["APOLLO_API_KEY"],
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _post(endpoint: str, payload: dict) -> dict:
    """POST to Apollo API with retry on transient errors."""
    url = f"{APOLLO_BASE}/{endpoint}"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers=_headers())
        if resp.status_code == 429:
            log.warning("Apollo rate limit hit — waiting 10s")
            time.sleep(10)
            raise Exception("Rate limited")
        resp.raise_for_status()
        return resp.json()


def search_companies(
    icp: ICPProfile,
    keyword: Optional[str],
    max_results: int = 100,
) -> list[RawCompany]:
    """
    Stage 1: Search Apollo for companies matching the ICP.
    Returns raw company list with all available signals.
    Credits used: search is free; enrichment costs credits.
    """
    global _credits_used

    payload: dict = {
        "per_page": min(max_results, 100),
        "page": 1,
    }

    # Build search filters from ICP
    if keyword:
        payload["q_keywords"] = keyword
    if icp.industry:
        payload["organization_industry_tag_ids"] = []
        payload["q_organization_keyword_tags"] = [icp.industry]
    if icp.location:
        payload["organization_locations"] = [icp.location]
    if icp.company_size_min or icp.company_size_max:
        size_range = []
        if icp.company_size_min:
            size_range.append(str(icp.company_size_min))
        if icp.company_size_max:
            size_range.append(str(icp.company_size_max))
        payload["organization_num_employees_ranges"] = [",".join(size_range)]

    try:
        data = _post("mixed_companies/search", payload)
    except Exception as e:
        log.error("Apollo company search failed: %s", e)
        return []

    companies = data.get("organizations", []) or data.get("accounts", []) or []
    log.info("Apollo returned %d companies", len(companies))

    results: list[RawCompany] = []
    for org in companies:
        signals = _extract_signals(org)
        results.append(RawCompany(
            company_name=org.get("name", "Unknown"),
            website=_clean_url(org.get("website_url") or org.get("primary_domain")),
            industry=org.get("industry"),
            location=_format_location(org),
            company_size=_format_size(org),
            founded_year=org.get("founded_year"),
            company_linkedin_url=org.get("linkedin_url"),
            lead_source=LeadSource.APOLLO,
            apollo_signals=signals,
        ))

    return results


def enrich_contacts(
    company_name: str,
    website: Optional[str],
    job_titles: list[str],
    max_contacts: int = 1,
) -> tuple[Optional[Contact], Optional[str]]:
    """
    Stage 4: Find decision-makers at a company.
    Returns (Contact | None, generic_email | None).
    Each verified email costs 1 Apollo credit.
    """
    global _credits_used

    payload: dict = {
        "per_page": max_contacts + 3,  # fetch a few extra, take best match
        "page": 1,
        "q_organization_name": company_name,
        "person_titles": job_titles,
        "contact_email_status": ["verified", "likely to engage"],
    }
    if website:
        domain = _extract_domain(website)
        if domain:
            payload["q_organization_domains"] = [domain]

    try:
        data = _post("mixed_people/search", payload)
    except Exception as e:
        log.error("Apollo contact search failed for %s: %s", company_name, e)
        return None, None

    people = data.get("people", []) or []

    # Pick the most senior match
    decision_maker: Optional[Contact] = None
    for person in people[:max_contacts]:
        email = person.get("email")
        if not email:
            continue
        _credits_used += 1
        decision_maker = Contact(
            name=person.get("name"),
            title=person.get("title"),
            email=email,
            linkedin_url=person.get("linkedin_url"),
            direct_phone=person.get("direct_phone_number") or person.get("phone_number"),
        )
        break  # Take first verified match

    # Try to find a generic company email pattern
    generic_email = _guess_generic_email(website) if website else None

    if decision_maker:
        log.info("Found decision-maker: %s (%s) at %s", decision_maker.name, decision_maker.title, company_name)
    else:
        log.info("No decision-maker found for %s — will mark partial", company_name)

    return decision_maker, generic_email


def enrich_company(
    company_name: str,
    website: Optional[str] = None,
) -> dict:
    """
    Enrichment API: takes a known company name/domain and returns full Apollo data.
    Used in Mode 3 (company list upload) to fill in missing company details.
    Returns raw dict of whatever Apollo knows — may be empty on basic plan.
    """
    payload: dict = {"name": company_name}
    if website:
        domain = _extract_domain(website)
        if domain:
            payload["domain"] = domain

    try:
        data = _post("organizations/enrich", payload)
        org = data.get("organization") or {}
        if org:
            log.info("Apollo enriched company: %s", company_name)
        return org
    except Exception as e:
        log.warning("Apollo company enrichment failed for '%s': %s", company_name, e)
        return {}


def get_credits_used() -> int:
    """Return total Apollo credits used in this session."""
    return _credits_used


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_signals(org: dict) -> ApolloSignals:
    """Extract Apollo intelligence signals — empty on basic plan, populated on premium."""
    try:
        return ApolloSignals(
            intent_topics=org.get("intent_keywords", []) or [],
            intent_strength=org.get("intent_strength"),
            technologies_used=[t.get("name", "") for t in (org.get("technologies") or [])],
            funding_round=org.get("latest_funding_stage"),
            hiring_signals=[j.get("title", "") for j in (org.get("job_postings") or [])[:5]],
            growth_signals=org.get("headcount_six_month_growth"),
            job_change_alert=bool(org.get("job_change_alert")),
            apollo_lead_score=org.get("score"),
        )
    except Exception:
        return ApolloSignals()


def _format_location(org: dict) -> Optional[str]:
    parts = [
        org.get("city"),
        org.get("state"),
        org.get("country"),
    ]
    return ", ".join(p for p in parts if p) or None


def _format_size(org: dict) -> Optional[str]:
    low = org.get("estimated_num_employees_min") or org.get("num_employees_lower_bound")
    high = org.get("estimated_num_employees_max") or org.get("num_employees_upper_bound")
    if low and high:
        return f"{low}–{high}"
    if low:
        return f"{low}+"
    return org.get("employee_count") or None


def _clean_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    return url


def _extract_domain(url: str) -> Optional[str]:
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
        domain = parsed.netloc or parsed.path
        return domain.replace("www.", "").strip("/") or None
    except Exception:
        return None


def _guess_generic_email(website: str) -> Optional[str]:
    """Best-effort generic email from domain (info@domain.com)."""
    domain = _extract_domain(website)
    if domain:
        return f"info@{domain}"
    return None
