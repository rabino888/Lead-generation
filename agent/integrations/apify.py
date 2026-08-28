"""
Apify integration — fallback discovery and company list enrichment.

Actors used:
  - Google Maps Scraper:       compass/crawler-google-places
  - Google Search Scraper:     apify/google-search-scraper
  - LinkedIn Company Scraper:  harvestapi/linkedin-company (default; works under
                                 Apify restricted-run permissions)
                                 automation-lab/linkedin-company-scraper (legacy — LinkedIn 999)
  - LinkedIn Profile Scraper:  apimaestro/linkedin-profile-detail (default, $5/1k,
                                 input=username; works under restricted-run permissions)
                                 Backup URL discovery: apify/google-search-scraper
                                 (site:linkedin.com/in — when Apollo omits linkedin_url)
  - LinkedIn Posts Scraper:    harvestapi/linkedin-profile-posts (default; honors maxPosts)
                                 data-slayer/linkedin-profile-posts-scraper (legacy — no post cap in API)
  - LinkedIn Jobs Scraper:     DISABLED by default (APIFY_SKIP_LINKEDIN_JOBS=1).
                                 Do NOT use afanasenko/linkedin-jobs-scraper — burned
                                 $6.27/53 empty SUCCEEDED runs on 2026-08-25.
                                 Candidate: gtgyani206/linkedin-company-scraper
                                 (company page → open jobs; smoke OK on Confido).
                                 Seed discovery stays on curious_coder/linkedin-jobs-scraper
                                 with jobs *search* URLs (not /jobs/view/ or company /jobs/).
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from agent.models import ApolloSignals, RawCompany, LeadSource
from agent.utils.logger import log

_HIRING_PHRASES = (
    "we're hiring", "we are hiring", "now hiring", "join our team",
    "open positions", "open roles", "job opening", "recruiting",
    "hiring for", "join us at", "growing team", "talent acquisition",
    "#hiring", "#wearehiring",
)

# Blocked — expensive empty SUCCEEDED runs (see docs/Improvements 24-08.md §5).
_BLOCKED_JOBS_ACTORS = frozenset(
    {
        "afanasenko/linkedin-jobs-scraper",
        "afanasenko~linkedin-jobs-scraper",
    }
)


def _get_client():
    from apify_client import ApifyClient
    return ApifyClient(os.environ["APIFY_TOKEN"])


def _actor_dataset_items(client, run) -> list[dict]:
    dataset_id = (
        run.default_dataset_id
        if hasattr(run, "default_dataset_id")
        else run.get("defaultDatasetId")
    )
    return list(client.dataset(dataset_id).iterate_items())


def _normalize_linkedin_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    return url.split("?")[0].rstrip("/")


def _skip_linkedin_posts() -> bool:
    return os.environ.get("APIFY_SKIP_LINKEDIN_POSTS", "").lower() in ("1", "true", "yes")


def _skip_linkedin_jobs() -> bool:
    """
    Company open-jobs scrape. Default SKIP until a cheap actor is approved.
    Set APIFY_SKIP_LINKEDIN_JOBS=0 and APIFY_LINKEDIN_JOBS_ACTOR=gtgyani206/linkedin-company-scraper
    after smoke_company_jobs.py passes cost gates.
    """
    if _skip_linkedin_posts():
        return True
    # Default "1" — opt-in only (afanasenko incident 2026-08-25).
    return os.environ.get("APIFY_SKIP_LINKEDIN_JOBS", "1").lower() in ("1", "true", "yes")


def _skip_person_profile() -> bool:
    """Gate the person profile scraper — highest Apify spend, lowest yield."""
    return os.environ.get("APIFY_SKIP_PERSON_PROFILE", "").lower() in ("1", "true", "yes")


def _default_max_posts() -> int:
    return max(1, int(os.environ.get("APIFY_MAX_LINKEDIN_POSTS", "5")))


def _record_apify_run(run) -> float:
    """Record billed Apify USD (re-fetches run so usageTotalUsd is present)."""
    from agent.utils.apify_spend import record_apify_run_billed

    return record_apify_run_billed(run)


def _posts_actor_input(profile_url: str, max_posts: int, actor_id: str) -> dict:
    if "data-slayer" in actor_id:
        # data-slayer OpenAPI schema only accepts linkedin_url + maxPages (NOT maxItems).
        # maxPages=1 still returns ~60 dataset rows (full activity page) — billed per row.
        log.warning(
            "data-slayer posts actor ignores maxItems; ~60 posts/page billed. "
            "Set APIFY_LINKEDIN_POSTS_ACTOR=harvestapi/linkedin-profile-posts for a real cap."
        )
        return {
            "linkedin_url": profile_url,
            "maxPages": 1,
        }
    if "harvestapi" in actor_id:
        return {"targetUrls": [profile_url], "maxPosts": max_posts}
    return {
        "linkedin_url": profile_url,
        "profileUrls": [profile_url],
        "maxItems": max_posts,
    }


def google_maps_search(keyword: str, location: str, max_results: int = 50) -> list[RawCompany]:
    """
    Search Google Maps for businesses matching keyword + location.
    Used as Apollo fallback for local/niche businesses.
    """
    # Build a clean search string — avoid duplicating location in keyword
    search_query = keyword.strip()
    if location and location.lower() not in search_query.lower():
        search_query = f"{search_query} {location}"

    log.info("Apify Google Maps: '%s' (max %d)", search_query, max_results)
    client = _get_client()

    try:
        run = client.actor("compass/crawler-google-places").call(run_input={
            "searchStringsArray": [search_query],
            "maxCrawledPlaces": max_results,
            "language": "en",
            "exportPlaceUrls": False,
        })
        _record_apify_run(run)
        # apify-client 3.x returns a Run model (attribute access), not a dict
        dataset_id = (
            run.default_dataset_id
            if hasattr(run, "default_dataset_id")
            else run.get("defaultDatasetId")
        )
        items = list(client.dataset(dataset_id).iterate_items())
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
        _record_apify_run(run)
        dataset_id = (
            run.default_dataset_id
            if hasattr(run, "default_dataset_id")
            else run.get("defaultDatasetId")
        )
        items = list(client.dataset(dataset_id).iterate_items())
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
    company_url = find_linkedin_company_url(company_name, website)
    if not company_url:
        return {}
    profile = get_linkedin_company_profile(company_url)
    return {
        "company_size": profile.get("employee_count"),
        "industry": profile.get("industry"),
        "company_linkedin_url": profile.get("linkedin_url"),
        "founded_year": profile.get("founded_year"),
        "description": profile.get("description"),
    }


def find_linkedin_company_url(company_name: str, website: Optional[str] = None) -> Optional[str]:
    """Resolve a public LinkedIn company page URL via Google search."""
    if not os.environ.get("APIFY_TOKEN"):
        return None

    queries = [
        f'"{company_name}" site:linkedin.com/company',
    ]
    domain = ""
    if website:
        from agent.integrations.apollo import _extract_domain
        domain = _extract_domain(website) or ""
        if domain:
            queries.append(f'"{domain}" site:linkedin.com/company')

    client = _get_client()
    name_key = re.sub(r"[^a-z0-9]+", "", company_name.lower())

    for query in queries:
        log.info("Apify LinkedIn company search: '%s'", query)
        try:
            run = client.actor("apify/google-search-scraper").call(run_input={
                "queries": query,
                "maxPagesPerQuery": 1,
                "resultsPerPage": 8,
                "mobileResults": False,
            })
            _record_apify_run(run)
            items = _actor_dataset_items(client, run)
        except Exception as e:
            log.warning("LinkedIn company search failed for '%s': %s", company_name, e)
            continue

        for item in items:
            for result in item.get("organicResults", []):
                url = result.get("url", "")
                if "linkedin.com/company/" not in url.lower():
                    continue
                slug = url.lower().split("linkedin.com/company/", 1)[-1].split("/")[0].split("?")[0]
                if not slug or slug in {"jobs", "school"}:
                    continue
                slug_key = re.sub(r"[^a-z0-9]+", "", slug)
                title = (result.get("title") or "").lower()
                domain_query = website and domain in query
                if domain_query or (
                    name_key
                    and (name_key in slug_key or slug_key in name_key or company_name.lower()[:8] in title)
                ):
                    resolved = _normalize_linkedin_url(url)
                    log.info("Found LinkedIn company for '%s': %s", company_name, resolved)
                    return resolved

    log.warning("Could not find LinkedIn company URL for '%s'", company_name)
    return None


def _skip_dm_linkedin_backup() -> bool:
    """Deprecated — pipeline no longer uses Google-search DM LinkedIn backup."""
    return os.environ.get("APIFY_SKIP_DM_LINKEDIN_BACKUP", "").lower() in ("1", "true", "yes")


def _name_tokens(name: str) -> list[str]:
    return [t for t in re.sub(r"[^a-z0-9]+", " ", name.lower()).split() if len(t) >= 2]


def find_linkedin_person_url(
    person_name: str,
    company_name: str,
    *,
    title: Optional[str] = None,
    website: Optional[str] = None,
) -> Optional[str]:
    """
    [Manual scripts only — not used by contacts stage.]

    Resolve a public linkedin.com/in/ URL via Google search (~$0.001–0.01/query).
    Pipeline policy: Apollo must return linkedin_url; missing URL = reject lead.
    """
    if not os.environ.get("APIFY_TOKEN"):
        return None
    person_name = (person_name or "").strip()
    company_name = (company_name or "").strip()
    if not person_name or not company_name:
        return None

    queries = [
        f'"{person_name}" "{company_name}" site:linkedin.com/in',
        f"{person_name} {company_name} linkedin",
    ]
    if title:
        queries.insert(0, f'"{person_name}" "{title}" "{company_name}" site:linkedin.com/in')
    if website:
        from agent.integrations.apollo import _extract_domain
        domain = _extract_domain(website) or ""
        if domain:
            queries.append(f'"{person_name}" "{domain}" site:linkedin.com/in')

    tokens = _name_tokens(person_name)
    client = _get_client()

    for query in queries:
        log.info("Apify LinkedIn person search: '%s'", query)
        try:
            run = client.actor("apify/google-search-scraper").call(
                run_input={
                    "queries": query,
                    "maxPagesPerQuery": 1,
                    "resultsPerPage": 8,
                    "mobileResults": False,
                }
            )
            _record_apify_run(run)
            items = _actor_dataset_items(client, run)
        except Exception as e:
            log.warning("LinkedIn person search failed for '%s': %s", person_name, e)
            continue

        best_url: Optional[str] = None
        best_score = 0
        company_key = re.sub(r"[^a-z0-9]+", "", company_name.lower())

        for item in items:
            for result in item.get("organicResults", []):
                url = result.get("url", "")
                if "linkedin.com/in/" not in url.lower():
                    continue
                normalized = _normalize_linkedin_url(url)
                if not normalized:
                    continue
                slug = normalized.lower().split("linkedin.com/in/", 1)[-1]
                slug = slug.split("/")[0].split("?")[0]
                title_text = (result.get("title") or "").lower()
                score = 0
                if tokens and all(t in slug or t in title_text for t in tokens[:2]):
                    score += 3
                elif tokens and any(t in slug or t in title_text for t in tokens):
                    score += 2
                if company_key and company_key[:8] in title_text:
                    score += 2
                if "linkedin" in title_text and person_name.split()[0].lower() in title_text:
                    score += 1
                if score > best_score:
                    best_score = score
                    best_url = normalized

        if best_url and best_score >= 2:
            log.info(
                "Found LinkedIn person for '%s' @ '%s': %s (score=%d)",
                person_name,
                company_name,
                best_url,
                best_score,
            )
            return best_url

    log.warning(
        "Could not find LinkedIn person URL for '%s' at '%s'",
        person_name,
        company_name,
    )
    return None


def backup_dm_linkedin_url(
    person_name: Optional[str],
    company_name: str,
    *,
    title: Optional[str] = None,
    website: Optional[str] = None,
) -> Optional[str]:
    """[Deprecated — not used by pipeline.] Manual Google-search lookup for DM LinkedIn URLs."""
    if _skip_dm_linkedin_backup():
        return None
    return find_linkedin_person_url(
        person_name or "",
        company_name,
        title=title,
        website=website,
    )


def _profile_actor_input(profile_url: str, actor_id: str) -> dict:
    """Build actor-specific input for the person profile scraper."""
    if "harvestapi" in actor_id:
        # harvestapi/linkedin-profile-scraper — $4/1k in default (no email) mode.
        # Emails come from Apollo, so the cheap mode is all we need.
        return {"queries": [profile_url]}
    if "apimaestro" in actor_id:
        # apimaestro/linkedin-profile-detail takes the public identifier only.
        username = profile_url.rstrip("/").rsplit("/", 1)[-1]
        return {"username": username}
    return {"profileUrls": [profile_url]}


def _normalize_profile_item(item: dict, profile_url: str) -> dict:
    """Normalize person profile output across actors (harvestapi/apimaestro/automation-lab)."""
    if isinstance(item.get("element"), dict):  # harvestapi wraps results
        item = item["element"]
    basic = item.get("basic_info") if isinstance(item.get("basic_info"), dict) else {}

    def _first_str(*values) -> str:
        for v in values:
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    name = _first_str(
        item.get("name"), item.get("fullname"), item.get("fullName"),
        basic.get("fullname"), basic.get("fullName"), basic.get("name"),
        " ".join(p for p in [item.get("firstName"), item.get("lastName")] if isinstance(p, str)),
    )
    about = _first_str(item.get("about"), item.get("summary"), basic.get("about"), basic.get("summary"))
    headline = _first_str(item.get("headline"), basic.get("headline"))
    location = item.get("location") or basic.get("location")
    if isinstance(location, dict):
        location = _first_str(
            location.get("linkedinText"), location.get("full"),
            location.get("city"), location.get("country"),
        ) or None
    linkedin_url = _first_str(
        item.get("linkedinUrl"), item.get("url"), item.get("profileUrl"),
        basic.get("profile_url"), basic.get("profileUrl"),
    ) or profile_url
    follower_count = item.get("followerCount") or basic.get("follower_count") or basic.get("followerCount")

    return {
        "name": name or None,
        "linkedin_url": linkedin_url,
        "headline": headline[:500] if headline else None,
        "about": about[:2000] if about else None,
        "location": location,
        "follower_count": follower_count,
    }


def get_linkedin_person_profile(profile_url: Optional[str]) -> dict:
    """Fetch public LinkedIn person profile: about, headline, location."""
    profile_url = _normalize_linkedin_url(profile_url)
    if not profile_url:
        return {}
    if not os.environ.get("APIFY_TOKEN"):
        return {}

    actor_id = os.environ.get(
        "APIFY_LINKEDIN_PROFILE_ACTOR",
        # apimaestro ($5/1k) — harvestapi/linkedin-profile-scraper is cheaper ($4/1k)
        # but crashes under Apify restricted-run permissions (named KV store 403).
        "apimaestro/linkedin-profile-detail",
    )
    log.info("Apify LinkedIn profile (%s): %s", actor_id, profile_url)
    client = _get_client()

    # One retry: actor runs occasionally crash at startup (e.g. transient 403
    # on key-value store creation) and return an empty dataset.
    for attempt in (1, 2):
        try:
            run = client.actor(actor_id).call(run_input=_profile_actor_input(profile_url, actor_id))
            _record_apify_run(run)
            items = _actor_dataset_items(client, run)
            if items:
                return _normalize_profile_item(items[0], profile_url)
            log.warning(
                "Apify LinkedIn profile returned no items for '%s' (attempt %d)",
                profile_url, attempt,
            )
        except Exception as e:
            log.warning(
                "Apify LinkedIn profile failed for '%s' (attempt %d): %s",
                profile_url, attempt, e,
            )
    return {}


def get_linkedin_company_profile(company_linkedin_url: Optional[str]) -> dict:
    """Fetch public LinkedIn company profile: description, size, industry."""
    company_linkedin_url = _normalize_linkedin_url(company_linkedin_url)
    if not company_linkedin_url:
        return {}
    if not os.environ.get("APIFY_TOKEN"):
        return {}

    actor_id = os.environ.get(
        "APIFY_LINKEDIN_COMPANY_ACTOR",
        "harvestapi/linkedin-company",  # automation-lab actor gets LinkedIn 999 on every call
    )
    log.info("Apify LinkedIn company (%s): %s", actor_id, company_linkedin_url)
    client = _get_client()

    if "harvestapi" in actor_id:
        run_input = {"companies": [company_linkedin_url], "targetUrls": [company_linkedin_url]}
    else:
        run_input = {"companyUrls": [company_linkedin_url]}

    try:
        run = client.actor(actor_id).call(run_input=run_input)
        _record_apify_run(run)
        items = _actor_dataset_items(client, run)
        if not items:
            return {}
        item = items[0]
        if isinstance(item.get("element"), dict):  # some harvestapi actors wrap results
            item = item["element"]
        return _normalize_company_item(item, company_linkedin_url)
    except Exception as e:
        log.warning("Apify LinkedIn company failed for '%s': %s", company_linkedin_url, e)
        return {}


def _normalize_company_item(item: dict, company_linkedin_url: str) -> dict:
    description = (item.get("description") or item.get("about") or "").strip()

    industry = item.get("industry") or item.get("industries")
    if isinstance(industry, list):
        industry = ", ".join(
            i.get("name", "") if isinstance(i, dict) else str(i) for i in industry if i
        ) or None

    employee_count = (
        item.get("employeeCount") or item.get("staffCount") or item.get("company_size")
    )
    founded = item.get("foundedYear") or item.get("foundedOn")
    if isinstance(founded, dict):
        founded = founded.get("year")

    return {
        "name": item.get("name") or item.get("companyName"),
        "linkedin_url": item.get("linkedinUrl") or item.get("url") or company_linkedin_url,
        "description": description[:2000] if description else None,
        "industry": industry,
        "employee_count": employee_count,
        "website": item.get("websiteUrl") or item.get("website"),
        "founded_year": founded,
        "job_search_url": item.get("jobSearchUrl"),
    }


def get_linkedin_company_jobs(
    company_linkedin_url: Optional[str],
    company_name: Optional[str] = None,
    max_jobs: int = 5,
) -> dict:
    """
    Fetch open LinkedIn job postings for a company page (single company).
    For multiple companies use get_linkedin_company_jobs_batch().
    """
    if not company_linkedin_url:
        return {"is_hiring": None, "open_jobs_count": 0, "job_titles": [], "summary": None}
    batch = get_linkedin_company_jobs_batch([company_linkedin_url], max_jobs=max_jobs)
    url = _normalize_linkedin_url(company_linkedin_url)
    if url and url.endswith("/jobs"):
        url = url[:-len("/jobs")]
    key = url or company_linkedin_url
    result = batch.get(key) or batch.get(company_linkedin_url) or {}
    if not result:
        return {"is_hiring": None, "open_jobs_count": 0, "job_titles": [], "summary": None}
    if company_name and result.get("job_titles"):
        # Re-apply company name filter from legacy single-company path
        filtered = []
        for title in result.get("job_titles") or []:
            if title:
                filtered.append(title)
        result = dict(result)
        result["job_titles"] = filtered
        count = len(filtered)
        result["open_jobs_count"] = count
        result["is_hiring"] = "yes" if count > 0 else "no"
        result["summary"] = "; ".join(filtered[:5]) if filtered else None
    return result


def get_linkedin_company_jobs_batch(
    company_linkedin_urls: list[str],
    *,
    max_jobs: int = 5,
    actor_id: Optional[str] = None,
) -> dict[str, dict]:
    """
    Batch fetch open jobs for up to 100 company LinkedIn URLs in one Apify run.
    Returns dict keyed by normalized company URL.
    """
    normalized: list[str] = []
    key_map: dict[str, str] = {}
    for raw in company_linkedin_urls:
        url = _normalize_linkedin_url(raw)
        if not url:
            continue
        if url.endswith("/jobs"):
            url = url[:-len("/jobs")]
        if "/company/" not in url.lower():
            continue
        if url not in key_map:
            normalized.append(url)
            key_map[url] = url

    empty = {"is_hiring": None, "open_jobs_count": 0, "job_titles": [], "summary": None}
    if not normalized:
        return {}
    if _skip_linkedin_jobs():
        return {u: dict(empty) for u in normalized}
    if not os.environ.get("APIFY_TOKEN"):
        return {u: dict(empty) for u in normalized}

    actor_id = actor_id or os.environ.get(
        "APIFY_LINKEDIN_JOBS_ACTOR",
        "gtgyani206/linkedin-company-scraper",
    )
    if actor_id in _BLOCKED_JOBS_ACTORS or "afanasenko" in actor_id:
        log.error("Blocked LinkedIn jobs actor '%s'", actor_id)
        return {u: dict(empty) for u in normalized}

    client = _get_client()
    results: dict[str, dict] = {u: dict(empty) for u in normalized}
    log.info("Apify LinkedIn company jobs batch (%s): %d companies", actor_id, len(normalized))

    try:
        if "gtgyani" in actor_id:
            run_input = {
                "companyUrls": normalized[:100],
                "maxJobs": max_jobs,
                "runProfile": "fast",
                "scrapeDescription": False,
            }
        else:
            run_input = {
                "operationMode": "searchCompanyJobs",
                "searchCompanyJobsCompanyUrls": normalized[:100],
                "maxItemsMode3": max_jobs,
            }
        run = client.actor(actor_id).call(run_input=run_input)
        _record_apify_run(run)
        items = _actor_dataset_items(client, run)
    except Exception as e:
        log.warning("Apify LinkedIn jobs batch failed: %s", e)
        return results

    for item in items:
        title = (item.get("title") or item.get("jobTitle") or item.get("position") or "").strip()
        item_url = (
            item.get("companyLinkedinUrl")
            or item.get("companyUrl")
            or item.get("companyLinkedinUrl")
            or item.get("inputUrl")
            or item.get("companyLinkedinUrl")
            or ""
        )
        company_url = _normalize_linkedin_url(str(item_url))
        if company_url and company_url.endswith("/jobs"):
            company_url = company_url[:-len("/jobs")]
        if not company_url:
            continue
        bucket = results.get(company_url)
        if bucket is None:
            # fuzzy match slug
            slug = company_url.lower().split("/company/")[-1].split("/")[0]
            for k in results:
                if slug and slug in k.lower():
                    bucket = results[k]
                    break
        if bucket is None:
            continue
        if title and title not in bucket["job_titles"]:
            bucket["job_titles"].append(title)

    for url, bucket in results.items():
        count = len(bucket["job_titles"])
        bucket["open_jobs_count"] = count
        bucket["is_hiring"] = "yes" if count > 0 else "no"
        bucket["summary"] = "; ".join(bucket["job_titles"][:5]) if bucket["job_titles"] else None

    log.info("Apify LinkedIn jobs batch returned titles for %d/%d companies", sum(1 for b in results.values() if b["job_titles"]), len(normalized))
    return results


def scrape_website_apify(url: str, max_pages: int = 3) -> Optional[str]:
    """
    Fallback website scrape when Firecrawl returns thin/empty content.
    Uses Apify website-content-crawler (configurable via APIFY_WEBSITE_SCRAPER_ACTOR).
  Future: replace with in-house scraper when volume justifies maintenance.
    """
    if not url or not os.environ.get("APIFY_TOKEN"):
        return None
    if "://" not in url:
        url = f"https://{url}"
    actor_id = os.environ.get(
        "APIFY_WEBSITE_SCRAPER_ACTOR",
        "apify/website-content-crawler",
    )
    client = _get_client()
    try:
        run_input = {
            "startUrls": [{"url": url}],
            "maxCrawlPages": max(1, min(max_pages, 10)),
            "maxCrawlDepth": 1,
            "crawlerType": "cheerio",
        }
        log.info("Apify website fallback (%s): %s", actor_id, url)
        run = client.actor(actor_id).call(run_input=run_input)
        _record_apify_run(run)
        items = _actor_dataset_items(client, run)
        sections: list[str] = []
        for item in items[:max_pages]:
            md = (item.get("markdown") or item.get("text") or item.get("content") or "").strip()
            page_url = item.get("url") or item.get("metadata", {}).get("url") or url
            if md:
                sections.append(f"### [{page_url}]\n{md}")
        combined = "\n\n---\n\n".join(sections)
        if combined:
            log.info("Apify website fallback: %d sections from %s", len(sections), url)
            return combined
    except Exception as e:
        log.warning("Apify website fallback failed for %s: %s", url, e)
    return None


def enrich_decision_maker_linkedin(profile_url: Optional[str], max_posts: Optional[int] = None) -> dict:
    """Combined person enrichment: profile about, posts, and hiring signals from posts."""
    if max_posts is None:
        max_posts = _default_max_posts()
    if _skip_person_profile():
        log.info("LinkedIn person profile skipped — APIFY_SKIP_PERSON_PROFILE is set")
        profile = {}
    else:
        profile = get_linkedin_person_profile(profile_url)
    if _skip_linkedin_posts():
        return {
            **profile,
            "posts": [],
            "interests": [],
            "post_summary": None,
            "is_hiring": None,
        }
    posts_data = get_linkedin_profile_posts(profile_url, max_posts=max_posts)
    posts = posts_data.get("posts") or []
    person_hiring = detect_hiring_from_posts(posts)
    return {
        **profile,
        "posts": posts,
        "interests": posts_data.get("interests") or [],
        "post_summary": posts_data.get("summary"),
        "is_hiring": person_hiring,
    }


def enrich_company_linkedin(
    company_name: str,
    website: Optional[str] = None,
    company_linkedin_url: Optional[str] = None,
    max_jobs: int = 5,
    fetch_open_jobs: bool = True,
) -> dict:
    """Combined company enrichment: page URL, profile, and optional open jobs."""
    from agent.integrations.apollo import get_company_linkedin_url

    linkedin_url = _normalize_linkedin_url(company_linkedin_url)
    if not linkedin_url:
        linkedin_url = get_company_linkedin_url(company_name, website)
    if not linkedin_url:
        linkedin_url = find_linkedin_company_url(company_name, website)

    profile = get_linkedin_company_profile(linkedin_url) if linkedin_url else {}
    jobs: dict = {}
    if linkedin_url and fetch_open_jobs and not _skip_linkedin_jobs():
        jobs = get_linkedin_company_jobs(linkedin_url, company_name=company_name, max_jobs=max_jobs)
    return {
        "linkedin_url": profile.get("linkedin_url") or linkedin_url,
        "description": profile.get("description"),
        "industry": profile.get("industry"),
        "employee_count": profile.get("employee_count"),
        "is_hiring": jobs.get("is_hiring"),
        "open_jobs_count": jobs.get("open_jobs_count", 0),
        "open_jobs_summary": jobs.get("summary"),
    }


def get_linkedin_profile_posts(profile_url: Optional[str], max_posts: Optional[int] = None) -> dict:
    """
    Fetch recent public LinkedIn posts for a decision-maker profile.
    Best effort only: returns empty post fields if Apify or the actor fails.
    """
    if max_posts is None:
        max_posts = _default_max_posts()
    if _skip_linkedin_posts():
        log.info("LinkedIn posts skipped — APIFY_SKIP_LINKEDIN_POSTS is set")
        return {"posts": [], "interests": [], "summary": None}
    if not profile_url:
        return {"posts": [], "interests": [], "summary": None}
    if not os.environ.get("APIFY_TOKEN"):
        log.warning("Apify LinkedIn posts skipped — APIFY_TOKEN is not configured")
        return {"posts": [], "interests": [], "summary": None}

    actor_id = os.environ.get(
        "APIFY_LINKEDIN_POSTS_ACTOR",
        "harvestapi/linkedin-profile-posts",
    )
    log.info("Apify LinkedIn posts: %s (max %d)", profile_url, max_posts)
    client = _get_client()

    try:
        run = client.actor(actor_id).call(run_input=_posts_actor_input(profile_url, max_posts, actor_id))
        _record_apify_run(run)
        items = _actor_dataset_items(client, run)
    except Exception as e:
        log.warning("Apify LinkedIn posts failed for '%s': %s", profile_url, e)
        return {"posts": [], "interests": [], "summary": None}

    if len(items) > max_posts:
        log.warning(
            "Apify posts actor returned %d dataset items (cap %d) — excess rows may still be billed",
            len(items),
            max_posts,
        )

    posts = [_normalize_post(item) for item in items[:max_posts]]
    posts = [p for p in posts if p.get("text") or p.get("url")]
    interests = _extract_interests(posts)
    summary = _summarize_posts(posts, interests)
    hiring = detect_hiring_from_posts(posts)

    log.info("Apify LinkedIn posts returned %d posts for %s", len(posts), profile_url)
    return {"posts": posts, "interests": interests, "summary": summary, "is_hiring": hiring}


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


def _normalize_post(item: dict) -> dict:
    text = (
        item.get("text")
        or item.get("postText")
        or item.get("content")
        or item.get("headline")
        or item.get("commentary")
        or ""
    )
    url = item.get("url") or item.get("postUrl") or item.get("shareUrl") or item.get("link")
    posted_at = (
        item.get("postedAt")
        or item.get("date")
        or item.get("publishedAt")
        or item.get("time")
        or item.get("datePublished")
        or item.get("created_at")
    )
    return {
        "text": str(text).strip()[:1000],
        "url": url,
        "posted_at": posted_at,
        "likes": item.get("likes") or item.get("likeCount") or item.get("numLikes"),
        "comments": item.get("comments") or item.get("commentCount") or item.get("numComments"),
    }


def detect_hiring_from_posts(posts: list[dict]) -> Optional[str]:
    """Return yes/no if recent posts mention hiring."""
    if not posts:
        return None
    text = " ".join((p.get("text") or "").lower() for p in posts)
    if any(phrase in text for phrase in _HIRING_PHRASES):
        return "yes"
    return "no"


def _extract_interests(posts: list[dict]) -> list[str]:
    interests: list[str] = []
    keywords = [
        "ai", "automation", "legaltech", "compliance", "growth", "hiring",
        "marketing", "sales", "operations", "productivity", "cost", "efficiency",
        "cybersecurity", "data", "crm", "process", "innovation",
    ]
    text = " ".join((p.get("text") or "").lower() for p in posts)
    for keyword in keywords:
        if keyword in text:
            interests.append(keyword)
    return interests[:10]


def _summarize_posts(posts: list[dict], interests: list[str]) -> Optional[str]:
    if not posts:
        return None
    snippets = []
    for post in posts[:3]:
        text = (post.get("text") or "").replace("\n", " ").strip()
        if text:
            snippets.append(text[:180])
    if interests:
        return f"Recent LinkedIn themes: {', '.join(interests)}. Examples: " + " | ".join(snippets)
    if snippets:
        return "Recent LinkedIn post examples: " + " | ".join(snippets)
    return None
