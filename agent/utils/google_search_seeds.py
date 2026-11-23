"""
Google Search (Apify apify/google-search-scraper) → company seed rows.

Deterministic, client-agnostic. Queries, country, language and campaign-specific
domain excludes all come from the campaign's seed_sources.json — never from code.

Built-in excludes cover platforms that are never a seed company in any campaign
(social networks, encyclopedias, job boards, review sites, generic news, government
and education TLDs). Industry media, trade bodies and regulators are campaign
specific: list them in config.exclude_domains.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Iterable
from urllib.parse import urlparse

log = logging.getLogger(__name__)

DEFAULT_ACTOR = "apify/google-search-scraper"
DEFAULT_PAGES_PER_QUERY = 3
DEFAULT_RESULTS_PER_PAGE = 10
DEFAULT_TARGET = 150

# Never a seed company, in any campaign or country.
BUILTIN_EXCLUDE_DOMAINS: frozenset[str] = frozenset(
    {
        # social / video / community
        "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
        "youtube.com", "tiktok.com", "pinterest.com", "reddit.com", "quora.com",
        "medium.com", "substack.com", "github.com",
        # reference / search / marketplaces
        "wikipedia.org", "google.com", "amazon.com", "apple.com", "play.google.com",
        # company databases / directories
        "crunchbase.com", "zoominfo.com", "dnb.com", "owler.com", "pitchbook.com",
        "craft.co", "cbinsights.com", "tracxn.com", "rocketreach.co", "apollo.io",
        "companieshouse.gov.uk", "endole.co.uk", "yell.com", "yelp.com",
        "bloomberg.com", "opencorporates.com", "f6s.com", "golden.com",
        # job boards / employer reviews
        "indeed.com", "glassdoor.com", "glassdoor.co.uk", "reed.co.uk",
        "totaljobs.com", "monster.com", "ziprecruiter.com", "welcometothejungle.com",
        # consumer review sites
        "trustpilot.com", "tripadvisor.com", "g2.com", "capterra.com",
        "feefo.com", "reviews.io",
        # generic news
        "bbc.co.uk", "bbc.com", "theguardian.com", "ft.com", "reuters.com",
        "forbes.com", "cnbc.com", "telegraph.co.uk", "independent.co.uk",
        "thetimes.co.uk", "dailymail.co.uk", "businessinsider.com", "techcrunch.com",
        "sifted.eu", "cityam.com", "yahoo.com", "msn.com", "slashdot.org",
    }
)

# Host suffixes that are never commercial seed companies.
BUILTIN_EXCLUDE_SUFFIXES: tuple[str, ...] = (
    ".gov", ".gov.uk", ".gob.es", ".gouv.fr", ".europa.eu", ".edu", ".ac.uk",
    ".nhs.uk", ".mil", ".int",
)

_TITLE_SPLIT = re.compile(r"\s+[|\-–—:·•]\s+")
_GENERIC_SEGMENTS = frozenset(
    {"home", "homepage", "official site", "official website", "welcome", "about us"}
)


def host_of(url: str) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = f"https://{url}"
    return urlparse(url).netloc.lower().split(":")[0].removeprefix("www.")


_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "ac", "gov", "ltd", "plc", "me", "nhs"})


def registrable_domain(host: str) -> str:
    """'quote.acme.co.uk' -> 'acme.co.uk'; 'careers.acme.com' -> 'acme.com'."""
    parts = [p for p in (host or "").lower().split(".") if p]
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else (parts[0] if parts else "")


def site_root(url: str) -> str:
    """Company homepage for a SERP hit: keep www/apex host, collapse other subdomains."""
    host = host_of(url)
    if not host:
        return ""
    reg = registrable_domain(host)
    if host != reg:
        # quote./careers./uk. subdomain: www.<reg> resolves far more often than the apex.
        return f"https://www.{reg}"
    # Keep the www. the SERP used: some apex domains do not resolve.
    raw = urlparse(url if url.startswith("http") else f"https://{url}").netloc.lower()
    return f"https://www.{reg}" if raw.split(":")[0] == f"www.{reg}" else f"https://{reg}"


def _domain_matches(host: str, domains: Iterable[str]) -> bool:
    for d in domains:
        d = (d or "").strip().lower().removeprefix("www.")
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


def is_excluded_host(host: str, extra_excludes: Iterable[str] = ()) -> bool:
    if not host:
        return True
    if host.endswith(BUILTIN_EXCLUDE_SUFFIXES) or f".{host}" in BUILTIN_EXCLUDE_SUFFIXES:
        return True
    return _domain_matches(host, BUILTIN_EXCLUDE_DOMAINS) or _domain_matches(
        host, extra_excludes
    )


def _brand_label(host: str) -> str:
    """'quote.hiscox.co.uk' -> 'hiscox'. Strips common multi-part public suffixes."""
    reg = registrable_domain(host)
    return reg.split(".")[0] if reg else ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def company_name_from_result(title: str, host: str) -> str:
    """
    Pick the brand out of a SERP title deterministically.

    'Home Insurance | Get a Quote | Aviva' on aviva.co.uk -> 'Aviva'.
    Falls back to the domain label when no title segment matches it.
    """
    label = _brand_label(host) if host else ""
    nlabel = _norm(label)
    segments = [s.strip() for s in _TITLE_SPLIT.split(title or "") if s.strip()]
    segments = [s for s in segments if s.lower() not in _GENERIC_SEGMENTS]
    if nlabel:
        for seg in segments:
            nseg = _norm(seg)
            if nseg and (nseg == nlabel or nlabel in nseg and len(nseg) <= len(nlabel) + 12):
                return seg
        for seg in segments:
            nseg = _norm(seg)
            if nseg and len(nseg) >= 3 and nseg in nlabel:
                return seg
        # 'Acme Underwriting' on acmeuw.com: first word is the domain's prefix.
        for seg in segments:
            first = _norm(seg.split()[0]) if seg.split() else ""
            if len(first) >= 3 and nlabel.startswith(first) and len(seg) <= 40:
                return seg
    if label:
        return label.replace("-", " ").title()
    return segments[0] if segments else ""


def normalize_queries(raw: Iterable[Any]) -> list[str]:
    """Accept ["q", ...] or [{"query": "q"}, ...]; drop blanks and duplicates."""
    out: list[str] = []
    seen: set[str] = set()
    for q in raw or []:
        text = (q.get("query") or q.get("keyword") or "") if isinstance(q, dict) else str(q)
        text = str(text).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out


def serp_items_to_seed_rows(
    items: list[dict[str, Any]],
    *,
    geo_segment: str,
    location: str = "",
    exclude_domains: Iterable[str] = (),
    seen_hosts: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """
    Flatten organicResults into one seed row per root domain.

    Returns (rows, dropped) where dropped lists {host, reason}.
    Dedupe key is the registrable domain (subdomains collapse into one company).
    seen_hosts holds registrable domains and is updated in place.
    """
    seen = seen_hosts if seen_hosts is not None else set()
    excludes = [d for d in exclude_domains if d]
    rows: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    for item in items or []:
        sq = item.get("searchQuery") or {}
        query = (sq.get("term") if isinstance(sq, dict) else str(sq)) or ""
        page = sq.get("page") if isinstance(sq, dict) else None
        for res in item.get("organicResults") or []:
            url = (res.get("url") or "").strip()
            host = host_of(url)
            if is_excluded_host(host, excludes):
                if host:
                    dropped.append({"host": host, "reason": "excluded_domain"})
                continue
            reg = registrable_domain(host)
            if reg in seen:
                continue
            seen.add(reg)
            name = company_name_from_result(res.get("title") or "", host)
            pos = res.get("position")
            note = f"Google Search | query: {query}"
            if pos is not None:
                note += f" | pos {pos}" + (f" p{page}" if page else "")
            rows.append(
                {
                    "seed_id": "",
                    "company_name": name,
                    "website": site_root(url),
                    "location": location,
                    "geo_segment": geo_segment,
                    "source_url": url,
                    "status": "pending",
                    "notes": note,
                    "industry": "",
                    "company_size": "",
                    "google_snippet": (res.get("description") or "").strip()[:300],
                }
            )
    return rows, dropped


def scrape_google_search(
    queries: list[str],
    *,
    pages_per_query: int = DEFAULT_PAGES_PER_QUERY,
    results_per_page: int = DEFAULT_RESULTS_PER_PAGE,
    country_code: str = "",
    language_code: str = "",
    actor_id: str = DEFAULT_ACTOR,
) -> tuple[list[dict[str, Any]], float]:
    """One Apify run over all queries. Returns (dataset items, billed USD)."""
    if not queries:
        return [], 0.0
    if not os.environ.get("APIFY_TOKEN"):
        raise RuntimeError("APIFY_TOKEN missing — cannot run google_search seed source")
    from agent.integrations.apify import _actor_dataset_items, _get_client
    from agent.utils.apify_spend import record_apify_run_billed

    run_input: dict[str, Any] = {
        "queries": "\n".join(queries),
        "maxPagesPerQuery": max(1, int(pages_per_query)),
        "resultsPerPage": max(1, min(100, int(results_per_page))),
        "mobileResults": False,
        "saveHtml": False,
        "saveHtmlToKeyValueStore": False,
        "includeUnfilteredResults": False,
    }
    if country_code:
        run_input["countryCode"] = country_code.lower()
    if language_code:
        run_input["languageCode"] = language_code.lower()
    log.info(
        "Apify Google Search (%s): %d quer(y/ies) x %d page(s)",
        actor_id,
        len(queries),
        run_input["maxPagesPerQuery"],
    )
    client = _get_client()
    run = client.actor(actor_id).call(run_input=run_input)
    usd = record_apify_run_billed(run)
    return _actor_dataset_items(client, run), usd
