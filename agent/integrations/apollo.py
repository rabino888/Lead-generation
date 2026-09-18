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
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from agent.models import ApolloSignals, Contact, ICPProfile, RawCompany, LeadSource
from agent.utils.job_titles import (
    primary_title,
    seniorities_from_job_titles,
    title_matches_icp,
    title_priority,
)
from agent.utils.logger import log
from agent.webhooks.apollo_phone import resolve_apollo_phone_webhook_url

APOLLO_BASE = "https://api.apollo.io/api/v1"
_credits_used = 0


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": os.environ["APOLLO_API_KEY"],
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _post(
    endpoint: str,
    payload: Optional[dict] = None,
    params: Optional[list[tuple[str, Any]]] = None,
) -> dict:
    """POST to Apollo API with retry on transient errors."""
    url = f"{APOLLO_BASE}/{endpoint}"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload or {}, params=params, headers=_headers())
        if resp.status_code != 200:
            # Log the real status and body BEFORE raise_for_status() hides it in RetryError
            log.error(
                "Apollo %s -> HTTP %d | body: %s",
                endpoint,
                resp.status_code,
                resp.text[:400],
            )
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("retry-after", "10"))
            log.warning("Apollo rate limit hit — waiting %ss", retry_after)
            time.sleep(retry_after)
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

    # Build keyword query — extract the business-type part, not the location
    search_terms: list[str] = []
    clean_keyword = keyword
    if keyword:
        # Extract location from keyword so we can filter separately
        clean_keyword, extracted_location = _split_keyword_and_location(keyword)
        if clean_keyword:
            search_terms.append(clean_keyword)
        # Use extracted location as fallback if ICP has no location
        if extracted_location and not icp.location:
            icp = icp.model_copy(update={"location": extracted_location})
    if not search_terms and icp.industry:
        search_terms.append(icp.industry)
    if search_terms:
        payload["q_keywords"] = " ".join(search_terms)

    # Industry filtering — much more precise than keyword alone
    # Maps keyword/ICP industry to Apollo's standardised industry categories
    industries = _resolve_industries(clean_keyword or "", icp.industry or "")
    if industries:
        payload["organization_industries"] = industries

    # Location filtering — use country code + city name separately for accuracy
    if icp.location:
        country_codes = _location_to_country_codes(icp.location)
        if country_codes:
            payload["organization_country_codes"] = country_codes
        # Also add city as a location string for narrower matching
        payload["organization_locations"] = [icp.location]

    # Company size filtering
    if icp.company_size_min or icp.company_size_max:
        low = icp.company_size_min or 1
        high = icp.company_size_max or 10000
        payload["organization_num_employees_ranges"] = [f"{low},{high}"]
    else:
        # Default: cap at 5 000 employees when no size is specified.
        # This prevents Fortune-500 companies (Google, Amazon, etc.) from
        # dominating results when searching for agencies / boutique firms.
        payload["organization_num_employees_ranges"] = ["1,5000"]

    # Exclude keywords (e.g. "recruitment", "freelance")
    if icp.excluded_keywords:
        payload["q_not_keywords"] = " ".join(icp.excluded_keywords)

    log.info("Apollo search payload: %s", {k: v for k, v in payload.items() if k != "api_key"})

    companies = _apollo_search_with_fallback(payload)
    log.info("Apollo returned %d companies", len(companies))

    results: list[RawCompany] = []
    for org in companies:
        signals = _extract_signals(org)
        results.append(RawCompany(
            apollo_org_id=org.get("id") or org.get("organization_id"),
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


def _apollo_search_with_fallback(payload: dict) -> list:
    """
    Execute an Apollo company search with progressive filter relaxation.
    If the strict payload returns 0 results:
      1. Retry without industry filter (keeps location + size)
      2. Retry without location filter too (keyword + size only)
    Returns the first non-empty result list, or [] if all retries fail.
    """
    def _do_search(p: dict) -> list:
        try:
            data = _post("mixed_companies/search", p)
            return data.get("organizations", []) or data.get("accounts", []) or []
        except Exception as e:
            log.error("Apollo company search failed: %s", e)
            return []

    # 1. Full search
    companies = _do_search(payload)
    if companies:
        return companies

    # 2. Drop industry filter (it can be too narrow on basic plan)
    if "organization_industries" in payload:
        relaxed = {k: v for k, v in payload.items() if k != "organization_industries"}
        log.info("Apollo: 0 results with industry filter — retrying without it")
        log.info("Apollo retry payload: %s", {k: v for k, v in relaxed.items() if k != "api_key"})
        companies = _do_search(relaxed)
        if companies:
            return companies

    # 3. Also drop location filter (keyword + size only)
    if any(k in payload for k in ("organization_country_codes", "organization_locations")):
        broad = {k: v for k, v in payload.items()
                 if k not in ("organization_industries", "organization_country_codes", "organization_locations")}
        log.info("Apollo: still 0 results — retrying without location filter")
        log.info("Apollo broad payload: %s", {k: v for k, v in broad.items() if k != "api_key"})
        companies = _do_search(broad)
        return companies

    return []


def enrich_contacts(
    company_name: str,
    website: Optional[str],
    job_titles: list[str],
    max_contacts: int = 1,
    contact_locations: Optional[list[str]] = None,
) -> tuple[Optional[Contact], Optional[str]]:
    """
    Stage 4: Find and reveal a contactable decision-maker at a company.
    Returns (Contact | None, generic_email | None). A contact is returned only
    when Apollo reveals a real email; generic emails are kept as metadata only.

    contact_locations restricts the PERSON's location (Apollo person_locations[]).
    Strict: if set and no candidate matches, the company yields no contact —
    a global BPO's Manila HR director is useless for a Spain-hub campaign.
    """
    generic_email = _guess_generic_email(website) if website else None

    people = _search_contact_candidates(
        company_name=company_name,
        website=website,
        job_titles=job_titles,
        max_candidates=max(max_contacts + 5, 8),
        contact_locations=contact_locations,
    )
    if not people:
        log.info("Apollo: no contact candidates for %s", company_name)
        return None, generic_email

    # Only keep people whose primary title matches the campaign ICP job_titles.
    matched = [
        p for p in people
        if title_matches_icp(str(p.get("title") or ""), job_titles)
    ]
    if not matched:
        rejected_titles = [primary_title(str(p.get("title") or "")) or "?" for p in people[:5]]
        log.info(
            "Apollo: %d candidates for %s but none match ICP job_titles — sample titles: %s",
            len(people),
            company_name,
            "; ".join(rejected_titles),
        )
        return None, generic_email

    if len(matched) < len(people):
        log.info(
            "Apollo: ICP title filter kept %d/%d candidates for %s",
            len(matched),
            len(people),
            company_name,
        )

    # Search is free but does not reveal emails. Spend credits only on people
    # Apollo says are likely to have an email, trying the strongest ICP matches first.
    prioritized = sorted(
        matched,
        key=lambda p: (
            title_priority(str(p.get("title") or ""), job_titles),
            not bool(p.get("has_email")),
            _seniority_rank(str(p.get("seniority") or "")),
        ),
    )
    # Phone reveal is opt-in via webhook URL; APOLLO_SKIP_PHONE=1 forces email-only.
    skip_phone = os.environ.get("APOLLO_SKIP_PHONE", "").strip().lower() in ("1", "true", "yes")
    reveal_phone = bool(resolve_apollo_phone_webhook_url()) and not skip_phone
    max_attempts = max(1, int(os.environ.get("APOLLO_MAX_REVEAL_ATTEMPTS", "2")))

    for person in prioritized[:max_attempts]:
        if not person.get("has_email"):
            continue
        revealed = _reveal_person(person, reveal_phone=reveal_phone)
        if revealed and revealed.email:
            log.info(
                "Apollo: revealed contactable decision-maker for %s: %s (%s)",
                company_name,
                revealed.name or "unknown",
                revealed.title or "unknown title",
            )
            return revealed, generic_email

    log.info("Apollo: candidates found for %s, but no email was revealed", company_name)
    return None, generic_email


def _search_contact_candidates(
    company_name: str,
    website: Optional[str],
    job_titles: list[str],
    max_candidates: int,
    contact_locations: Optional[list[str]] = None,
) -> list[dict]:
    """Use Apollo People API Search with documented query-array filters."""
    params: list[tuple[str, Any]] = [
        ("per_page", min(max_candidates, 100)),
        ("page", 1),
        (
            "include_similar_titles",
            os.environ.get("APOLLO_INCLUDE_SIMILAR_TITLES", "true").lower(),
        ),
    ]
    # Comma-separated list, e.g. "verified,unverified". Default keeps the
    # original strict behaviour; relax for marginal accounts where Apollo has
    # people but no verified-status email (the reveal step is still the gate).
    email_statuses = os.environ.get("APOLLO_EMAIL_STATUSES", "verified")
    for status in email_statuses.split(","):
        status = status.strip()
        if status:
            params.append(("contact_email_status[]", status))

    for location in contact_locations or []:
        params.append(("person_locations[]", location))

    domain = _extract_domain(website) if website else None
    if domain:
        params.append(("q_organization_domains_list[]", domain))
    else:
        params.append(("q_organization_name", company_name))

    for title in job_titles:
        if title:
            params.append(("person_titles[]", title))

    for seniority in seniorities_from_job_titles(job_titles):
        params.append(("person_seniorities[]", seniority))

    try:
        data = _post("mixed_people/api_search", params=params)
    except Exception as e:
        log.error("Apollo contact search failed for %s: %s", company_name, e)
        return []

    _record_apollo_credits(data)
    people = data.get("people", []) or []
    log.info(
        "Apollo: %d contact candidates for %s (total_entries=%s)",
        len(people),
        company_name,
        data.get("total_entries"),
    )
    return people


def _record_apollo_credits(data: dict) -> int:
    """Bump session + CostTracker from an Apollo people/match response."""
    global _credits_used
    credits = int(data.get("credits_consumed") or data.get("credit_consumed") or 0)
    _credits_used += credits
    if credits:
        from agent.utils.cost_tracker import get_cost_tracker
        tracker = get_cost_tracker()
        if tracker:
            tracker.add_usage(apollo_credits=credits)
    return credits


def _contact_from_match(match: dict, person: Optional[dict] = None) -> Optional[Contact]:
    person = person or {}
    email = match.get("email") or match.get("personal_email") or person.get("email")
    if not email and not match.get("linkedin_url") and not person.get("linkedin_url"):
        return None
    person_id = match.get("id") or person.get("id")
    return Contact(
        apollo_person_id=person_id,
        name=match.get("name") or person.get("name"),
        title=primary_title(match.get("title") or person.get("title") or "")
        or match.get("title")
        or person.get("title"),
        email=email,
        email_status=match.get("email_status") or match.get("email_status_unavailable_reason"),
        location=_person_location(match) or _person_location(person),
        linkedin_url=match.get("linkedin_url") or person.get("linkedin_url"),
        direct_phone=(
            match.get("direct_phone_number")
            or match.get("phone_number")
            or match.get("sanitized_phone")
        ),
        mobile_phone=(
            match.get("mobile_phone")
            or match.get("mobile_phone_number")
            or match.get("phone_number")
        ),
    )


def _reveal_person(person: dict, reveal_phone: bool = False) -> Optional[Contact]:
    """Reveal email/phone for a People Search candidate using People Enrichment."""
    person_id = person.get("id")
    if not person_id:
        return None

    params: list[tuple[str, Any]] = [
        ("id", person_id),
        ("reveal_personal_emails", "true"),
        ("reveal_phone_number", "true" if reveal_phone else "false"),
    ]
    webhook_url = resolve_apollo_phone_webhook_url()
    if reveal_phone and webhook_url:
        params.append(("webhook_url", webhook_url))

    try:
        data = _post("people/match", params=params)
    except Exception as e:
        log.warning("Apollo person reveal failed for id=%s: %s", person_id, e)
        return None

    _record_apollo_credits(data)
    match = _first_person_match(data)
    if not match:
        return None
    contact = _contact_from_match(match, person)
    if not contact or not contact.email:
        return None
    return contact


def enrich_person_by_email(
    email: str,
    *,
    reveal_personal_emails: bool = True,
) -> Optional[Contact]:
    """
    People Enrichment by email — recovers LinkedIn URL / name / title for an
    already-known address. Costs Apollo credits (tracked via get_credits_used()).
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return None

    params: list[tuple[str, Any]] = [
        ("email", email),
        ("reveal_personal_emails", "true" if reveal_personal_emails else "false"),
        ("reveal_phone_number", "false"),
    ]
    try:
        data = _post("people/match", params=params)
    except Exception as e:
        log.warning("Apollo email enrich failed for %s: %s", email, e)
        return None

    credits = _record_apollo_credits(data)
    match = _first_person_match(data)
    if not match:
        log.info("Apollo email enrich: no person for %s (credits=%s)", email, credits)
        return None
    contact = _contact_from_match(match)
    log.info(
        "Apollo email enrich: %s -> linkedin=%s name=%s (credits=%s)",
        email,
        "yes" if contact and contact.linkedin_url else "no",
        (contact.name if contact else None) or "?",
        credits,
    )
    return contact


def enrich_person_by_linkedin(
    linkedin_url: str,
    *,
    reveal_personal_emails: bool = True,
    first_name: str = "",
    last_name: str = "",
    organization_name: str = "",
    domain: str = "",
) -> Optional[Contact]:
    """
    People Enrichment by LinkedIn URL (talent T05).

    Primary match: ``linkedin_url`` + ``reveal_personal_emails``.
    Fallback when URL match returns no person: name + organization / domain.
    Phone reveal is never requested here (talent phone is TS-8 / T06).

    Credits are tracked via ``get_credits_used()`` / CostTracker.
    Returned Contact may lack ``email`` — callers enforce the D3 personal-email gate.
    """
    url = (linkedin_url or "").strip()
    if not url:
        return None

    params: list[tuple[str, Any]] = [
        ("linkedin_url", url),
        ("reveal_personal_emails", "true" if reveal_personal_emails else "false"),
        ("reveal_phone_number", "false"),
    ]
    contact = _people_match_contact(params, label=f"linkedin={url}")
    if contact:
        return contact

    # Fallback: name + company identity when LinkedIn URL is unknown to Apollo
    fn = (first_name or "").strip()
    ln = (last_name or "").strip()
    org = (organization_name or "").strip()
    dom = (domain or "").strip().lower()
    if not (fn or ln) or not (org or dom):
        return None

    fallback: list[tuple[str, Any]] = [
        ("reveal_personal_emails", "true" if reveal_personal_emails else "false"),
        ("reveal_phone_number", "false"),
    ]
    if fn:
        fallback.append(("first_name", fn))
    if ln:
        fallback.append(("last_name", ln))
    if org:
        fallback.append(("organization_name", org))
    if dom:
        fallback.append(("domain", dom))
    # Keep LinkedIn URL in the request when present — Apollo may still use it as a hint
    fallback.append(("linkedin_url", url))
    return _people_match_contact(
        fallback,
        label=f"name={fn} {ln} org={org or dom}",
    )


def _people_match_contact(
    params: list[tuple[str, Any]],
    *,
    label: str,
) -> Optional[Contact]:
    """POST people/match, record credits, map first person to Contact."""
    try:
        data = _post("people/match", params=params)
    except Exception as e:
        log.warning("Apollo people/match failed (%s): %s", label, e)
        return None

    credits = _record_apollo_credits(data)
    match = _first_person_match(data)
    if not match:
        log.info("Apollo people/match: no person for %s (credits=%s)", label, credits)
        return None
    contact = _contact_from_match(match)
    log.info(
        "Apollo people/match: %s -> email=%s linkedin=%s (credits=%s)",
        label,
        "yes" if contact and contact.email else "no",
        "yes" if contact and contact.linkedin_url else "no",
        credits,
    )
    return contact


def _person_location(person: dict) -> Optional[str]:
    """'Barcelona, Spain' from Apollo person fields (city/state/country)."""
    if not person:
        return None
    parts = [person.get("city"), person.get("state"), person.get("country")]
    location = ", ".join(str(p) for p in parts if p)
    return location or None


def _first_person_match(data: dict) -> dict:
    """Normalize Apollo's single and bulk-ish enrichment response shapes."""
    if isinstance(data.get("person"), dict):
        return data["person"]
    matches = data.get("matches") or []
    if matches and isinstance(matches[0], dict):
        return matches[0]
    if isinstance(data.get("contact"), dict):
        return data["contact"]
    return {}


def _seniority_rank(value: str) -> int:
    value = value.lower()
    order = ["owner", "founder", "chief", "ceo", "cmo", "vp", "head", "director", "manager"]
    for i, token in enumerate(order):
        if token in value:
            return i
    return len(order)


def get_company_linkedin_url(
    company_name: str,
    website: Optional[str] = None,
) -> Optional[str]:
    """Best-effort company LinkedIn URL from Apollo org enrich (no Apify)."""
    org = enrich_company(company_name, website)
    raw = (org.get("linkedin_url") or "").strip()
    if not raw:
        return None
    if not raw.startswith("http"):
        raw = f"https://{raw}"
    return raw.split("?")[0].rstrip("/")


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
        _record_apollo_credits(data)
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


def reset_credits_used() -> None:
    """Reset per-run Apollo credit counter."""
    global _credits_used
    _credits_used = 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_signals(org: dict) -> ApolloSignals:
    """Extract Apollo intelligence signals — empty on basic plan, populated on premium."""
    try:
        technologies = org.get("technologies") or org.get("current_technologies") or []
        technology_names = org.get("technology_names") or []
        if not technology_names:
            technology_names = [t.get("name", "") for t in technologies if isinstance(t, dict)]

        job_postings = org.get("job_postings") or org.get("latest_job_postings") or []
        hiring_signals = [
            j.get("title", "")
            for j in job_postings[:5]
            if isinstance(j, dict) and j.get("title")
        ]

        growth = (
            org.get("headcount_six_month_growth")
            or org.get("headcount_growth")
            or org.get("employee_growth")
        )
        return ApolloSignals(
            intent_topics=org.get("intent_keywords", []) or org.get("intent_topics", []) or [],
            intent_strength=org.get("intent_strength") or org.get("overall_intent"),
            technologies_used=[name for name in technology_names if name],
            funding_round=org.get("latest_funding_stage") or org.get("latest_funding_round"),
            hiring_signals=hiring_signals,
            growth_signals=str(growth) if growth is not None else None,
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


def _location_to_country_code(location: str) -> Optional[str]:
    """Map common location strings to ISO 3166-1 alpha-2 country codes."""
    codes = _location_to_country_codes(location)
    return codes[0] if codes else None


def _location_to_country_codes(location: str) -> list[str]:
    """Map common location strings to ISO 3166-1 alpha-2 country codes."""
    location_lower = location.lower()
    country_map = {
        "spain": "ES", "españa": "ES", "madrid": "ES", "barcelona": "ES",
        "uk": "GB", "united kingdom": "GB", "england": "GB", "london": "GB",
        "usa": "US", "united states": "US", "america": "US",
        "germany": "DE", "deutschland": "DE", "berlin": "DE",
        "france": "FR", "paris": "FR",
        "italy": "IT", "rome": "IT",
        "netherlands": "NL", "amsterdam": "NL",
        "portugal": "PT", "lisbon": "PT",
        "mexico": "MX", "colombia": "CO", "argentina": "AR",
        "brazil": "BR", "brasil": "BR",
        "canada": "CA", "australia": "AU",
    }
    codes: list[str] = []
    for key, code in country_map.items():
        if key in location_lower and code not in codes:
            codes.append(code)
    return codes


def _split_keyword_and_location(keyword: str) -> tuple[str, Optional[str]]:
    """
    Heuristically split a freeform keyword like "digital marketing agency Madrid"
    into (business_type, location).  Returns (original_keyword, None) if no
    recognisable location is found.
    """
    location_hints = [
        "madrid", "barcelona", "spain", "españa", "portugal", "london", "uk", "paris",
        "berlin", "amsterdam", "lisbon", "rome", "new york", "los angeles",
        "san francisco", "chicago", "toronto", "sydney", "melbourne",
        "mexico city", "bogota", "buenos aires", "sao paulo",
    ]
    kw_lower = keyword.lower()
    for hint in location_hints:
        if hint in kw_lower:
            # Remove the location word(s) from the keyword
            clean = kw_lower.replace(hint, "").strip().strip(",").strip()
            return clean or keyword, hint.title()
    return keyword, None


def _resolve_industries(keyword: str, icp_industry: str) -> list[str]:
    """
    Map a keyword + ICP industry to Apollo's standardised industry strings.
    Returns an empty list if no confident mapping is found (Apollo will search
    without industry restriction).
    """
    combined = (keyword + " " + icp_industry).lower()

    if any(t in combined for t in ["marketing", "advertising", "agency", "branding", "creative", "media buy"]):
        return ["marketing and advertising", "advertising services", "public relations and communications"]
    if any(t in combined for t in ["software", "saas", "app", "platform", "startup"]):
        return ["computer software", "internet", "information technology and services"]
    if any(t in combined for t in ["consulting", "advisory", "management consulting"]):
        return ["management consulting", "business consulting and services"]
    if any(t in combined for t in [
        "bpo", "business process outsourcing", "outsourcing", "offshoring",
        "contact center", "call center", "customer support", "customer service",
        "atención al cliente", "soporte telefónico",
    ]):
        return ["outsourcing/offshoring", "consumer services", "telecommunications"]
    if any(t in combined for t in ["seo", "sem", "content", "inbound", "digital"]):
        return ["marketing and advertising", "internet", "advertising services"]
    if any(t in combined for t in ["pr ", "public relations", "communications"]):
        return ["public relations and communications", "marketing and advertising"]
    if any(t in combined for t in ["restaurant", "food", "cafe", "catering"]):
        return ["restaurants", "food and beverages"]
    if any(t in combined for t in ["real estate", "property", "inmobiliaria"]):
        return ["real estate"]
    if any(t in combined for t in ["law firm", "legal", "attorney", "abogado"]):
        return ["law practice", "legal services"]
    return []
