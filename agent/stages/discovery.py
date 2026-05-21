"""
Stage 1 — Discovery
Apollo-first. Apify fallback when Apollo returns fewer results than needed
or when ICP targets local/niche businesses.
Mode 3 (company_list): Apify finds missing websites, skips Apollo search.
"""
from __future__ import annotations

import logging
from typing import Optional

from agent.integrations import apollo, apify
from agent.models import ClientProfile, ICPProfile, InputMode, RawCompany, RunRequest
from agent.utils.logger import get_run_logger


def run(
    request: RunRequest,
    client_profile: ClientProfile,
    run_id: str,
    logger: Optional[logging.Logger] = None,
) -> list[RawCompany]:
    """
    Returns a deduplicated list of RawCompany records ready for pre-filtering.
    """
    log = logger or get_run_logger(run_id, client_profile.client_id)
    log.info("Stage 1 — Discovery (mode: %s)", request.mode.value)

    icp = request.icp or client_profile.icp
    target = request.max_leads * 3  # Fetch 3x — pre-filter will reduce this

    # ── Mode 3: Company List ──────────────────────────────────────────────────
    if request.mode == InputMode.COMPANY_LIST:
        return _enrich_company_list(request.company_list or [], icp, run_id, log)

    # ── Mode 1 & 2: Apollo-first ──────────────────────────────────────────────
    companies = apollo.search_companies(
        icp=icp,
        keyword=request.keyword,
        max_results=min(target, 100),
    )
    log.info("Apollo returned %d companies", len(companies))

    # Apify fallback: if Apollo is short AND keyword suggests local/niche
    if len(companies) < request.max_leads and _is_local_search(request.keyword, icp):
        log.info("Apollo results insufficient — triggering Apify fallback")
        location = icp.location or _extract_location_from_keyword(request.keyword or "")
        apify_companies = apify.google_maps_search(
            keyword=request.keyword or icp.industry or "business",
            location=location or "",
            max_results=target - len(companies),
        )
        companies = _merge_and_deduplicate(companies, apify_companies)
        log.info("After Apify fallback + dedup: %d companies", len(companies))

    log.info("Stage 1 complete — %d companies discovered", len(companies))
    return companies


def _enrich_company_list(
    company_names: list[str],
    icp: ICPProfile,
    run_id: str,
    log: logging.Logger,
) -> list[RawCompany]:
    """
    Mode 3: For each company name, use Apify to find website + LinkedIn signals.
    """
    from agent.models import ApolloSignals, LeadSource
    log.info("Mode 3 — enriching %d uploaded company names", len(company_names))

    results: list[RawCompany] = []
    for name in company_names:
        name = name.strip()
        if not name:
            continue

        website = apify.find_company_website(name)
        linkedin_data = apify.get_linkedin_profile(name, website) if website else {}

        results.append(RawCompany(
            company_name=name,
            website=website,
            industry=linkedin_data.get("industry") or icp.industry,
            location=linkedin_data.get("location"),
            company_size=str(linkedin_data.get("company_size", "")) or None,
            founded_year=linkedin_data.get("founded_year"),
            company_linkedin_url=linkedin_data.get("company_linkedin_url"),
            lead_source=LeadSource.MANUAL,
            apollo_signals=ApolloSignals(),
        ))

    log.info("Mode 3 — resolved %d/%d companies", len([r for r in results if r.website]), len(results))
    return results


def _merge_and_deduplicate(
    primary: list[RawCompany],
    secondary: list[RawCompany],
) -> list[RawCompany]:
    """Merge two lists, deduplicate by domain."""
    seen_domains: set[str] = set()
    merged: list[RawCompany] = []

    for company in primary + secondary:
        domain = _extract_domain(company.website) if company.website else company.company_name.lower()
        if domain not in seen_domains:
            seen_domains.add(domain)
            merged.append(company)

    return merged


def _is_local_search(keyword: Optional[str], icp: ICPProfile) -> bool:
    """Heuristic: does this search target local businesses?"""
    local_terms = ["restaurant", "clinic", "gym", "shop", "store", "local", "near", "maps"]
    text = ((keyword or "") + " " + (icp.industry or "")).lower()
    return any(term in text for term in local_terms) or bool(icp.location)


def _extract_location_from_keyword(keyword: str) -> str:
    """Very basic: take last word(s) as potential location."""
    words = keyword.strip().split()
    if len(words) >= 2:
        return " ".join(words[-2:])
    return keyword


def _extract_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
        return (parsed.netloc or parsed.path).replace("www.", "").lower().strip("/")
    except Exception:
        return url.lower()
