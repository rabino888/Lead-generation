"""
Careers page discovery.

Many companies (BPOs especially) host open positions on a dedicated subdomain
(https://jobs.concentrix.com, https://careers.transcom.com) or an external ATS
(Workday, Greenhouse, SuccessFactors…). A same-domain Firecrawl crawl never
reaches those, so hiring detection used to miss them.

Strategy, cheapest first:
  1. Extract careers-looking links from the already-crawled site markdown.
  2. Probe common careers subdomains and paths with plain HTTP requests
     (no Firecrawl credits; ~6s timeout each, run in a small thread pool).

The winning URL can then be scraped (1 Firecrawl page) and fed to the
hiring-signal analysis.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import urlparse

import httpx

from agent.utils.logger import log

_URL_RE = re.compile(r"https?://[^\s)\]\"'<>]+", re.IGNORECASE)

# Words that mark a URL as careers-related (English + Spanish).
_CAREERS_HINT = re.compile(
    r"(career|job|vacanc|join[-_]?us|work[-_]?(with|for)[-_]?us|talent|hiring"
    r"|empleo|trabaja|vacante|unete|únete|recruit)",
    re.IGNORECASE,
)

# Well-known ATS hosts — a link to these is a careers page even without a hint word.
_ATS_HOSTS = (
    "myworkdayjobs.com", "workday.com", "greenhouse.io", "lever.co",
    "smartrecruiters.com", "successfactors.com", "successfactors.eu",
    "teamtailor.com", "recruitee.com", "personio.", "bamboohr.com",
    "icims.com", "taleo.net", "factorialhr.", "jobvite.com", "ashbyhq.com",
    "workable.com", "breezy.hr", "join.com", "avature.net", "eightfold.ai",
    "phenom.com", "oraclecloud.com",
)

_HIRING_TEXT = re.compile(
    r"(open positions|open roles|current openings|job openings|we'?re hiring"
    r"|join (our|the) team|apply now|vacantes|ofertas de empleo|trabaja con nosotros"
    r"|únete al equipo|unete al equipo|posiciones abiertas)",
    re.IGNORECASE,
)


def _registered_domain(website: str) -> str:
    """concentrix.com from https://www.concentrix.com/foo — naive but fine for probing."""
    host = urlparse(website if "://" in website else f"https://{website}").netloc.lower()
    host = host.split(":")[0]
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 3 and parts[0] in ("www", "web", "es", "en"):
        parts = parts[1:]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _is_ats_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(ats in host for ats in _ATS_HOSTS)


def _looks_like_careers_url(url: str, domain: str) -> bool:
    if _is_ats_url(url):
        return True
    if not _CAREERS_HINT.search(url):
        return False
    host = urlparse(url).netloc.lower()
    # Same organisation (any subdomain) or a hint-carrying external host.
    return domain in host or _CAREERS_HINT.search(host) is not None


def extract_careers_links(markdown: Optional[str], website: str) -> list[str]:
    """Careers-looking URLs already present in crawled site content, best first."""
    if not markdown:
        return []
    domain = _registered_domain(website)
    seen: set[str] = set()
    org_links: list[str] = []
    ats_links: list[str] = []
    for raw in _URL_RE.findall(markdown):
        url = raw.rstrip(".,;:!?")
        key = url.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        if not _looks_like_careers_url(url, domain):
            continue
        # Skip documents and article-style pages — we want the careers index.
        low = url.lower()
        if any(x in low for x in (".pdf", ".doc", "wp-content/", "/blog/", "/news/", "/noticias/")):
            continue
        host = urlparse(url).netloc.lower()
        if domain in host:
            org_links.append(url)
        else:
            ats_links.append(url)
    # Prefer the org's own careers subdomain/page; shorter URLs first (index pages).
    org_links.sort(key=len)
    ats_links.sort(key=len)
    return org_links + ats_links


def filter_careers_urls(urls: list[str], website: str) -> list[str]:
    """Rank sitemap / map URLs that look like careers boards."""
    if not urls:
        return []
    domain = _registered_domain(website)
    scored: list[tuple[int, str]] = []
    for raw in urls:
        url = raw.strip().rstrip(".,;:!?")
        if not url.startswith("http"):
            continue
        low = url.lower()
        if any(x in low for x in (".pdf", ".doc", ".jpg", ".png", "/blog/", "/news/")):
            continue
        if not (_looks_like_careers_url(url, domain) or _is_ats_url(url)):
            continue
        score = len(url)
        if low.endswith("/careers") or low.endswith("/jobs") or "/careers/" in low:
            score -= 50
        scored.append((score, url))
    scored.sort(key=lambda x: x[0])
    return [u for _, u in scored]


def _probe_candidates(website: str) -> list[str]:
    domain = _registered_domain(website)
    base = website.rstrip("/")
    if "://" not in base:
        base = f"https://{base}"
    return [
        f"https://careers.{domain}",
        f"https://jobs.{domain}",
        f"{base}/careers",
        f"{base}/jobs",
        f"{base}/join-us",
        f"{base}/work-with-us",
        f"{base}/vacancies",
        f"{base}/empleo",
        f"{base}/trabaja-con-nosotros",
        f"{base}/es/empleo",
        f"https://talent.{domain}",
        f"https://empleo.{domain}",
    ]


def _probe(url: str) -> Optional[str]:
    """Return the final URL if `url` resolves to a live careers-looking page."""
    try:
        with httpx.Client(follow_redirects=True, timeout=6, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        }) as client:
            resp = client.get(url)
    except Exception:
        return None
    if resp.status_code >= 400:
        return None
    final = str(resp.url)
    if _CAREERS_HINT.search(final) or _is_ats_url(final):
        return final
    # Redirected somewhere without a careers hint (often the homepage or a
    # soft-404) — only accept if the page content clearly shows hiring.
    return final if _HIRING_TEXT.search(resp.text[:20000]) else None


def find_careers_page(
    website: Optional[str],
    crawled_markdown: Optional[str] = None,
    sitemap_urls: Optional[list[str]] = None,
) -> Optional[str]:
    """
    Best careers/jobs URL for a company, or None.
    Order: sitemap careers URLs → markdown links → HTTP probe.
    """
    if not website:
        return None

    if sitemap_urls:
        for candidate in filter_careers_urls(sitemap_urls, website):
            resolved = _probe(candidate)
            if resolved:
                log.info("Careers page from sitemap: %s", resolved)
                return resolved

    links = extract_careers_links(crawled_markdown, website)
    if links:
        # Verify the top candidate is alive; fall through to probing if dead.
        for candidate in links[:3]:
            resolved = _probe(candidate)
            if resolved:
                log.info("Careers page from site links: %s", resolved)
                return resolved

    candidates = _probe_candidates(website)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_probe, candidates))
    for resolved in results:  # keep candidate priority order
        if resolved:
            log.info("Careers page from probe: %s", resolved)
            return resolved
    return None
