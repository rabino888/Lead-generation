"""
Apify integration — fallback discovery and company list enrichment.

Actors used:
  - Google Maps Scraper:       compass/crawler-google-places
  - Google Search Scraper:     apify/google-search-scraper
  - LinkedIn Company Scraper:  curious_coder/linkedin-company-profile-scraper
"""
from __future__ import annotations

import os
from typing import Optional

from agent.models import ApolloSignals, RawCompany, LeadSource
from agent.utils.logger import log


def _get_client():
    from apify_client import ApifyClient
    return ApifyClient(os.environ["APIFY_TOKEN"])


def google_maps_search(keyword: str, location: str, max_results: int = 50) -> list[RawCompany]:
    """
    Search Google Maps for businesses matching keyword + location.
    Used as Apollo fallback for local/niche businesses.
    """
    log.info("Apify Google Maps: '%s' in '%s' (max %d)", keyword, location, max_results)
    client = _get_client()

    try:
        run = client.actor("compass/crawler-google-places").call(run_input={
            "searchStringsArray": [f"{keyword} {location}"],
            "maxCrawledPlaces": max_results,
            "language": "en",
            "exportPlaceUrls": False,
        })
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    except Exception as e:
        log.error("Apify Google Maps failed: %s", e)
        return []

    results: list[RawCompany] = []
    for item in items:
        website = item.get("website")
        if not website:
            continue  # Skip if no website — Firecrawl can't enrich without one
        results.append(RawCompany(
            company_name=item.get("title", "Unknown"),
            website=website,
            industry=item.get("categoryName"),
            location=_format_maps_location(item),
            company_size=None,
            lead_source=LeadSource.APIFY,
            apollo_signals=ApolloSignals(),
        ))

    log.info("Apify Google Maps returned %d companies with websites", len(results))
    return results


def google_search_companies(query: str, max_results: int = 20) -> list[RawCompany]:
    """
    Google Search for company websites by query string.
    Used to find website URLs for companies in the uploaded company list.
    """
    log.info("Apify Google Search: '%s'", query)
    client = _get_client()

    try:
        run = client.actor("apify/google-search-scraper").call(run_input={
            "queries": query,
            "maxPagesPerQuery": 1,
            "resultsPerPage": max_results,
            "mobileResults": False,
        })
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    except Exception as e:
        log.error("Apify Google Search failed: %s", e)
        return []

    results: list[RawCompany] = []
    for item in items:
        for result in item.get("organicResults", []):
            url = result.get("url", "")
            if not url or _is_generic_site(url):
                continue
            results.append(RawCompany(
                company_name=result.get("title", "Unknown"),
                website=url,
                lead_source=LeadSource.APIFY,
                apollo_signals=ApolloSignals(),
            ))

    log.info("Apify Google Search returned %d results", len(results))
    return results


def find_company_website(company_name: str) -> Optional[str]:
    """
    Find the website for a company by name (used in Mode 3 — company list).
    Runs a targeted Google search and returns the first credible result.
    """
    results = google_search_companies(f"{company_name} official website", max_results=5)
    for r in results:
        if r.website:
            log.info("Found website for '%s': %s", company_name, r.website)
            return r.website
    log.warning("Could not find website for '%s'", company_name)
    return None


def get_linkedin_profile(company_name: str, website: Optional[str] = None) -> dict:
    """
    Fetch LinkedIn company profile for basic signals (employee count, industry).
    Used in Mode 3 to enrich manually-provided company names.
    Returns a dict with available fields (may be empty if scraper fails).
    """
    log.info("Apify LinkedIn: looking up '%s'", company_name)
    client = _get_client()

    search_query = company_name
    if website:
        from agent.integrations.apollo import _extract_domain
        domain = _extract_domain(website)
        if domain:
            search_query = domain

    try:
        run = client.actor("curious_coder/linkedin-company-profile-scraper").call(run_input={
            "queries": [search_query],
            "proxy": {"useApifyProxy": True},
        })
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        if items:
            item = items[0]
            return {
                "company_size": item.get("employeeCount"),
                "industry": item.get("industries", [None])[0] if item.get("industries") else None,
                "company_linkedin_url": item.get("linkedInUrl"),
                "founded_year": item.get("foundedOn", {}).get("year") if item.get("foundedOn") else None,
            }
    except Exception as e:
        log.warning("Apify LinkedIn lookup failed for '%s': %s", company_name, e)

    return {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_maps_location(item: dict) -> Optional[str]:
    parts = [
        item.get("city"),
        item.get("state"),
        item.get("country"),
        item.get("address"),
    ]
    return next((p for p in parts if p), None)


def _is_generic_site(url: str) -> bool:
    """Filter out obvious non-company URLs from search results."""
    generic = [
        "linkedin.com", "facebook.com", "twitter.com", "instagram.com",
        "youtube.com", "wikipedia.org", "yelp.com", "tripadvisor.com",
        "google.com", "amazon.com", "crunchbase.com",
    ]
    return any(g in url.lower() for g in generic)
