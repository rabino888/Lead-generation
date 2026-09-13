"""
Careers page discovery.

Many companies host open positions on a dedicated subdomain
(https://jobs.concentrix.com) or an external ATS (Workday, Greenhouse,
Ashby…). A same-domain crawl often stops on a marketing /careers page whose
"See open roles" CTA points at the real portal.

Strategy, cheapest first:
  1. Prefer ATS / job-board links already present in crawled markdown.
  2. Rank sitemap / map careers URLs (ATS first, then marketing indexes).
  3. Probe common careers subdomains and paths with plain HTTP
     (~6s timeout each, small thread pool).
  4. After scraping a marketing careers index, hop +1 page when a CTA or
     ATS link is present but role listings are not.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import urlparse

import httpx

from agent.utils.logger import log

_URL_RE = re.compile(r"https?://[^\s)\]\"'<>]+", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

# Words that mark a URL as careers-related (English + Spanish).
_CAREERS_HINT = re.compile(
    r"(career|job|vacanc|join[-_]?us|work[-_]?(with|for)[-_]?us|talent|hiring"
    r"|empleo|trabaja|vacante|unete|únete|recruit|positions)",
    re.IGNORECASE,
)

# Well-known ATS / external job hosts — prefer these over marketing /careers.
_ATS_HOSTS = (
    "myworkdayjobs.com", "workday.com", "greenhouse.io", "lever.co",
    "smartrecruiters.com", "successfactors.com", "successfactors.eu",
    "teamtailor.com", "recruitee.com", "personio.", "bamboohr.com",
    "icims.com", "taleo.net", "factorialhr.", "jobvite.com", "ashbyhq.com",
    "workable.com", "breezy.hr", "join.com", "avature.net", "eightfold.ai",
    "phenom.com", "oraclecloud.com", "gem.com", "ycombinator.com",
)

_HIRING_TEXT = re.compile(
    r"(open positions|open roles|current openings|job openings|we'?re hiring"
    r"|join (our|the) team|apply now|vacantes|ofertas de empleo|trabaja con nosotros"
    r"|únete al equipo|unete al equipo|posiciones abiertas|see open roles|"
    r"view (all )?(openings|jobs|positions)|explore (open )?roles)",
    re.IGNORECASE,
)

_CTA_ANCHOR = re.compile(
    r"(see\s+(all\s+)?open\s+roles|see\s+(all\s+)?jobs|view\s+(all\s+)?"
    r"(openings|jobs|positions|roles)|open\s+roles|browse\s+(open\s+)?"
    r"(jobs|roles)|explore\s+(open\s+)?roles|current\s+openings|"
    r"job\s+openings|we'?re\s+hiring|apply\s+(now|here)|view\s+openings)",
    re.IGNORECASE,
)

# False positives: API/docs "jobs" endpoints, auth walls, asset paths.
_REJECT_CAREERS = re.compile(
    r"/api/|/sdk-api/|/reference/|/rest-api/|docs\.|/docs/|"
    r"login\.|/login\b|/signin\b|set-defjob|/oauth|"
    r"\.(pdf|docx?|xlsx?|png|jpe?g|gif|svg)(?:\?|$)",
    re.IGNORECASE,
)

_ROLE_SIGNAL = re.compile(
    r"(engineer|scientist|designer|manager|director|founder|"
    r"internship|full[- ]?time|part[- ]?time|remote|apply\s+for|"
    r"job\s+title|open\s+position|positions?\s+open|"
    r"senior\s+\w+|staff\s+\w+|principal\s+\w+)",
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


def _clean_url(url: str) -> str:
    return url.strip().rstrip(".,;:!?)]}\"'" )


def _norm_key(url: str) -> str:
    return _clean_url(url).lower().rstrip("/")


def _is_ats_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    if "ycombinator.com" in host and "/jobs" not in path and "/companies/" not in path:
        return False
    return any(ats in host for ats in _ATS_HOSTS)


def _is_rejected_careers_url(url: str) -> bool:
    return bool(_REJECT_CAREERS.search(url))


def _looks_like_careers_url(url: str, domain: str) -> bool:
    if _is_rejected_careers_url(url):
        return False
    if _is_ats_url(url):
        return True
    if not _CAREERS_HINT.search(url):
        return False
    host = urlparse(url).netloc.lower()
    # Same organisation (any subdomain) or a hint-carrying external host.
    return domain in host or _CAREERS_HINT.search(host) is not None


def _rank_careers_url(url: str, domain: str) -> int:
    """Lower score = better. ATS/job boards beat marketing /careers pages."""
    low = url.lower()
    score = len(url)
    if _is_ats_url(url):
        score -= 500
    host = urlparse(url).netloc.lower()
    if host.startswith("jobs.") or host.startswith("careers.") or host.startswith("talent."):
        score -= 120
    if low.rstrip("/").endswith(("/careers", "/jobs", "/join-us", "/empleo")):
        score -= 40
    if domain and domain in host and not _is_ats_url(url):
        score += 30  # demote same-domain marketing relative to ATS
    if _is_rejected_careers_url(url):
        score += 10_000
    return score


def extract_careers_links(markdown: Optional[str], website: str) -> list[str]:
    """Careers-looking URLs already present in crawled site content, best first."""
    if not markdown:
        return []
    domain = _registered_domain(website)
    seen: set[str] = set()
    scored: list[tuple[int, str]] = []
    for raw in _URL_RE.findall(markdown):
        url = _clean_url(raw)
        key = _norm_key(url)
        if key in seen:
            continue
        seen.add(key)
        if not _looks_like_careers_url(url, domain):
            continue
        low = url.lower()
        if any(x in low for x in ("wp-content/", "/blog/", "/news/", "/noticias/")):
            continue
        scored.append((_rank_careers_url(url, domain), url))
    scored.sort(key=lambda x: x[0])
    return [u for _, u in scored]


def filter_careers_urls(urls: list[str], website: str) -> list[str]:
    """Rank sitemap / map URLs that look like careers boards."""
    if not urls:
        return []
    domain = _registered_domain(website)
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for raw in urls:
        url = _clean_url(raw)
        if not url.startswith("http"):
            continue
        key = _norm_key(url)
        if key in seen:
            continue
        seen.add(key)
        low = url.lower()
        if any(x in low for x in ("/blog/", "/news/", "/noticias/")):
            continue
        if not (_looks_like_careers_url(url, domain) or _is_ats_url(url)):
            continue
        scored.append((_rank_careers_url(url, domain), url))
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
    if _is_rejected_careers_url(url):
        return None
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
    if _is_rejected_careers_url(final):
        return None
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
    Order: markdown ATS/board links → sitemap careers URLs → other site links → HTTP probe.
    """
    if not website:
        return None

    # Prefer real portals already linked from crawled content (no HTTP needed when ATS).
    md_links = extract_careers_links(crawled_markdown, website)
    for candidate in md_links[:5]:
        if _is_ats_url(candidate):
            log.info("Careers portal from site links (ATS): %s", candidate)
            return candidate.split("?")[0].rstrip("/") or candidate

    if sitemap_urls:
        for candidate in filter_careers_urls(sitemap_urls, website):
            resolved = _probe(candidate)
            if resolved:
                log.info("Careers page from sitemap: %s", resolved)
                return resolved

    if md_links:
        for candidate in md_links[:5]:
            if _is_ats_url(candidate):
                continue  # already tried above
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


def careers_index_needs_portal_hop(
    markdown: Optional[str],
    *,
    careers_url: Optional[str] = None,
) -> bool:
    """
    True when the scraped careers page looks like a marketing landing page
    (CTA / hiring copy) rather than an actual job board with listings.
    """
    if careers_url and _is_ats_url(careers_url):
        return False
    text = (markdown or "").strip()
    if not text:
        return True
    if careers_url and _is_rejected_careers_url(careers_url):
        return True
    if _is_ats_url(text):  # ATS URL already present in body
        # Still hop if current page itself is not the ATS — caller decides target.
        pass
    role_hits = len(_ROLE_SIGNAL.findall(text))
    hiring_hits = len(_HIRING_TEXT.findall(text))
    # Short marketing page with hiring CTA, or hiring language without role signals.
    if len(text) < 2500 and hiring_hits >= 1 and role_hits < 3:
        return True
    if hiring_hits >= 1 and role_hits < 2:
        return True
    if _CTA_ANCHOR.search(text) and role_hits < 3:
        return True
    return False


def pick_careers_portal_hop(
    markdown: Optional[str],
    website: str,
    *,
    current_url: Optional[str] = None,
) -> Optional[str]:
    """
    Pick one deeper careers/portal URL from a marketing careers page.
    Prefer ATS hosts, then CTA-anchored markdown links, then other careers links.
    """
    if not markdown or not website:
        return None
    domain = _registered_domain(website)
    current_key = _norm_key(current_url) if current_url else ""

    def _usable(url: str) -> Optional[str]:
        url = _clean_url(url)
        if not url.startswith("http"):
            return None
        if _norm_key(url) == current_key:
            return None
        if _is_rejected_careers_url(url):
            return None
        if not (_looks_like_careers_url(url, domain) or _is_ats_url(url)):
            return None
        # Drop same-path hash-only "links" like /company#careers when we already
        # scraped a better index — still allow if clearly ATS.
        if current_url and not _is_ats_url(url):
            cur = urlparse(current_url)
            nxt = urlparse(url)
            if (
                cur.netloc.lower() == nxt.netloc.lower()
                and cur.path.rstrip("/") == nxt.path.rstrip("/")
            ):
                return None
        return url

    # 1) Markdown links whose anchor is a "see open roles" CTA.
    cta_hits: list[tuple[int, str]] = []
    for anchor, href in _MD_LINK_RE.findall(markdown):
        if not _CTA_ANCHOR.search(anchor):
            continue
        url = _usable(href)
        if url:
            cta_hits.append((_rank_careers_url(url, domain), url))
    if cta_hits:
        cta_hits.sort(key=lambda x: x[0])
        return cta_hits[0][1]

    # 2) Any ATS / ranked careers URL in the page body.
    for url in extract_careers_links(markdown, website):
        picked = _usable(url)
        if picked and _is_ats_url(picked):
            return picked

    # 3) Fallback: best non-current careers link that is a jobs subdomain.
    for url in extract_careers_links(markdown, website):
        picked = _usable(url)
        if not picked:
            continue
        host = urlparse(picked).netloc.lower()
        if host.startswith(("jobs.", "careers.", "talent.")):
            return picked
    return None
