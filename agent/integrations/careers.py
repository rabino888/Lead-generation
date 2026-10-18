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
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

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


# Public job-board JSON. Used when a careers page only links out to Ashby,
# Greenhouse, or Lever — scraping those apps often misses the listings.
_ATS_SKIP_SLUGS = frozenset(
    {"jobs", "job", "embed", "job_board", "job_app", "v1", "postings"}
)
_GENERIC_ROLE_TITLES = frozenset(
    {
        "careers",
        "jobs",
        "open roles",
        "open positions",
        "join us",
        "we're hiring",
        "we are hiring",
    }
)
_MAX_ATS_ROLES = 40


def ats_board_identity(url: str) -> Optional[tuple[str, str]]:
    """
    Return ('ashby'|'greenhouse'|'lever', board slug) for a job-board URL.

    Single-posting URLs collapse to the board so the listing API returns
    every open role, not one job.
    """
    raw = _clean_url(url or "")
    if not raw or _is_rejected_careers_url(raw):
        return None
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.split("/") if p]
    if "ashbyhq.com" in host:
        if not parts or parts[0].lower() in _ATS_SKIP_SLUGS:
            return None
        return ("ashby", parts[0])
    if "greenhouse.io" in host:
        token = (parse_qs(parsed.query).get("for") or [""])[0].strip()
        if token:
            return ("greenhouse", token)
        for part in parts:
            if part.lower() in _ATS_SKIP_SLUGS or part.isdigit():
                continue
            return ("greenhouse", part)
        return None
    if "lever.co" in host:
        for part in parts:
            if part.lower() in _ATS_SKIP_SLUGS:
                continue
            return ("lever", part)
        return None
    return None


def canonical_ats_board_url(url: str) -> Optional[str]:
    ident = ats_board_identity(url)
    if not ident:
        return None
    provider, slug = ident
    safe = quote(slug, safe="-_.~")
    if provider == "ashby":
        return f"https://jobs.ashbyhq.com/{safe}"
    if provider == "greenhouse":
        return f"https://job-boards.greenhouse.io/{safe}"
    if provider == "lever":
        return f"https://jobs.lever.co/{safe}"
    return None


def clean_role_titles(titles: list[Any], *, limit: int = _MAX_ATS_ROLES) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in titles:
        title = " ".join(str(raw or "").split())
        if not title or len(title) > 140:
            continue
        if title.casefold() in _GENERIC_ROLE_TITLES:
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(title)
        if len(out) >= limit:
            break
    return out


def titles_from_ats_payload(provider: str, payload: Any) -> list[str]:
    titles: list[str] = []
    if provider == "ashby" and isinstance(payload, dict):
        for job in payload.get("jobs") or []:
            if not isinstance(job, dict) or job.get("isListed") is False:
                continue
            titles.append(job.get("title") or "")
    elif provider == "greenhouse" and isinstance(payload, dict):
        for job in payload.get("jobs") or []:
            if isinstance(job, dict):
                titles.append(job.get("title") or "")
    elif provider == "lever" and isinstance(payload, list):
        for job in payload:
            if isinstance(job, dict):
                titles.append(job.get("text") or job.get("title") or "")
    return clean_role_titles(titles)


def fetch_ats_open_roles(url: str) -> list[str]:
    """Open-role titles from a public Ashby, Greenhouse, or Lever board. Empty on failure."""
    ident = ats_board_identity(url)
    if not ident:
        return []
    provider, slug = ident
    safe = quote(slug, safe="-_.~")
    try:
        if provider == "ashby":
            resp = httpx.get(
                f"https://api.ashbyhq.com/posting-api/job-board/{safe}",
                timeout=25,
                follow_redirects=True,
            )
        elif provider == "greenhouse":
            resp = httpx.get(
                f"https://boards-api.greenhouse.io/v1/boards/{safe}/jobs",
                timeout=25,
                follow_redirects=True,
            )
        else:
            resp = httpx.get(
                f"https://api.lever.co/v0/postings/{safe}",
                params={"mode": "json"},
                timeout=25,
                follow_redirects=True,
            )
    except Exception as exc:
        log.warning("ATS job board fetch failed for %s: %s", url, exc)
        return []
    if resp.status_code != 200:
        log.info("ATS job board %s returned HTTP %s for %s", provider, resp.status_code, slug)
        return []
    try:
        payload = resp.json()
    except Exception:
        return []
    titles = titles_from_ats_payload(provider, payload)
    if titles:
        log.info("ATS %s/%s: %d open roles", provider, slug, len(titles))
    return titles


def open_roles_from_ats(url: Optional[str]) -> Optional[dict[str, Any]]:
    """
    Structured listings for an ATS URL.

    Returns careers_url, open_roles, and a short markdown list — or None.
    """
    if not url:
        return None
    canonical = canonical_ats_board_url(url)
    if not canonical:
        return None
    titles = fetch_ats_open_roles(canonical)
    if not titles:
        return None
    return {
        "careers_url": canonical,
        "open_roles": titles,
        "markdown": "\n".join(f"- {title}" for title in titles),
    }


_ICIMS_JOB = re.compile(r"/jobs/\d+/([^/?#\"']+)/job", re.I)
_SMALL_WORDS = frozenset({"i", "ii", "iii", "iv", "qc", "qa"})


def _title_from_slug(slug: str) -> str:
    text = unquote(slug).replace("&amp;", " and ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    words: list[str] = []
    for word in text.split():
        if word.casefold() in _SMALL_WORDS:
            words.append(word.upper())
        else:
            words.append(word[:1].upper() + word[1:].lower())
    return " ".join(words)


def titles_from_icims_html(html: str) -> list[str]:
    return clean_role_titles([_title_from_slug(slug) for slug in _ICIMS_JOB.findall(html or "")])


def icims_board_url(url: str) -> Optional[str]:
    raw = _clean_url(url or "")
    if "://" not in raw:
        raw = "https://" + raw
    host = urlparse(raw).netloc.lower()
    if "icims.com" not in host:
        return None
    return f"https://{host}/jobs/search?ss=1&in_iframe=1"


def fetch_icims_open_roles(url: str) -> list[str]:
    """Open-role titles from an iCIMS board search page (titles are in the job URLs)."""
    board = icims_board_url(url)
    if not board:
        return []
    titles: list[str] = []
    for page in range(4):
        page_url = board if page == 0 else f"{board}&pr={page}"
        try:
            resp = httpx.get(
                page_url,
                timeout=25,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            )
        except Exception as exc:
            log.warning("iCIMS fetch failed for %s: %s", page_url, exc)
            break
        if resp.status_code != 200:
            break
        found = titles_from_icims_html(resp.text)
        if not found:
            break
        before = len(titles)
        titles = clean_role_titles(titles + found)
        if len(titles) == before:
            break
    if titles:
        log.info("iCIMS %s: %d open roles", urlparse(board).netloc, len(titles))
    return titles


def preferred_ats_board_url(markdown: Optional[str], website: str) -> Optional[str]:
    """Highest-ranked Ashby/Greenhouse/Lever board linked from crawled markdown."""
    if not markdown or not website:
        return None
    for url in extract_careers_links(markdown, website):
        canonical = canonical_ats_board_url(url)
        if canonical:
            return canonical
    return None
