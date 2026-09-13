"""
Website crawl facade for outreach-oriented intelligence.

Default crawler is Apify (`WEBSITE_CRAWLER=apify`) after Firecrawl credit
exhaustion. Set `WEBSITE_CRAWLER=firecrawl` to prefer Firecrawl when credits
are available.

Primary path: map/sitemap → scrape selected URLs (careers, blog/news,
services, about/team). Fallback: broader crawl.

Credit protection:
  Each company is capped (default 8 pages via FIRECRAWL_MAX_PAGES /
  WEBSITE_MAX_PAGES). The run-level counter still tracks Firecrawl pages
  when that backend is used.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from agent.utils.logger import log

_pages_crawled = 0
_pages_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_request_at = 0.0


def website_crawler() -> str:
    """apify (default) | firecrawl — which backend scrapes company websites."""
    raw = (os.environ.get("WEBSITE_CRAWLER") or "apify").strip().lower()
    if raw in ("firecrawl", "fc"):
        if os.environ.get("FIRECRAWL_API_KEY"):
            return "firecrawl"
        log.warning("WEBSITE_CRAWLER=firecrawl but FIRECRAWL_API_KEY missing — using apify")
        return "apify"
    return "apify"


def _firecrawl_available() -> bool:
    return bool(os.environ.get("FIRECRAWL_API_KEY"))


def _min_interval_sec() -> float:
    raw = (os.environ.get("FIRECRAWL_MIN_INTERVAL_SEC") or "7").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 7.0


def _throttle() -> None:
    """Stay under Firecrawl hobby rate limits (~11 req/min)."""
    global _last_request_at
    interval = _min_interval_sec()
    with _rate_lock:
        now = time.monotonic()
        wait = interval - (now - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _retry_after_seconds(error: Exception) -> Optional[float]:
    text = str(error)
    if "429" not in text and "rate limit" not in text.lower():
        return None
    match = re.search(r"retry after (\d+)", text, re.I)
    if match:
        return float(match.group(1)) + 2.0
    return 60.0


def _report_pages(count: int) -> None:
    """Count Firecrawl pages for the run and attribute them to the current lead."""
    global _pages_crawled
    with _pages_lock:
        _pages_crawled += count
    from agent.utils.cost_tracker import get_cost_tracker
    tracker = get_cost_tracker()
    if tracker and count:
        tracker.add_usage(firecrawl_pages=count)

# Pages we actually want — outreach intel, in priority order when the map-based
# selector cannot run. English + Spanish path patterns.
_TARGET_PATHS = [
    # Careers / jobs (same-domain indexes; ATS subdomains still use careers.py)
    "/careers*", "/career*", "/jobs*", "/join-us*", "/joinus*",
    "/work-with-us*", "/vacancies*", "/opportunities*",
    "/empleo*", "/trabaja*", "/vacantes*",

    # Blog / news indexes and recent posts
    "/blog*", "/news*", "/insights*", "/press*", "/resources*", "/articles*",
    "/noticias*", "/actualidad*",

    # Services
    "/services*", "/what-we-do*", "/solutions*", "/capabilities*", "/expertise*", "/work*",
    "/servicios*", "/soluciones*", "/que-hacemos*", "/trabajos*",

    # About / team
    "/about*", "/about-us*", "/who-we-are*", "/our-story*", "/company*",
    "/nosotros*", "/quienes-somos*", "/sobre-nosotros*", "/acerca*",
    "/team*", "/our-team*", "/people*", "/meet-the-team*",
    "/equipo*", "/nuestro-equipo*",
]


def crawl_website(url: str, max_pages: int = 8) -> Optional[str]:
    """
    Broader crawl when map-selected scrape is thin.
    Uses the active website crawler (Apify by default).
    """
    effective_max = max(1, max_pages)
    if website_crawler() == "apify":
        from agent.integrations.apify import scrape_website_apify

        return scrape_website_apify(url, max_pages=effective_max, max_depth=1)
    return _firecrawl_crawl_website(url, max_pages=effective_max)


def _firecrawl_crawl_website(url: str, max_pages: int = 8) -> Optional[str]:
    """Targeted Firecrawl crawl via includePaths."""
    global _pages_crawled

    effective_max = max(1, max_pages)

    try:
        from firecrawl import FirecrawlApp
    except ImportError:
        log.error("firecrawl-py not installed — run: pip install firecrawl-py")
        return None

    if not _firecrawl_available():
        return None

    app = FirecrawlApp(api_key=os.environ["FIRECRAWL_API_KEY"])

    try:
        _throttle()
        result = app.crawl_url(
            url,
            params={
                "limit": effective_max,
                "includePaths": _TARGET_PATHS,
                "scrapeOptions": {
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                },
            },
        )
        pages = result.get("data", [])
        if pages:
            if len(pages) > effective_max:
                log.info(
                    "Firecrawl returned %d pages for %s — capping at %d",
                    len(pages), url, effective_max,
                )
                pages = pages[:effective_max]
            sections = []
            for p in pages:
                page_url = p.get("metadata", {}).get("sourceURL", "")
                content = p.get("markdown", "").strip()
                if content:
                    header = f"### [{page_url}]" if page_url else ""
                    sections.append(f"{header}\n{content}" if header else content)

            combined = "\n\n---\n\n".join(sections)
            _report_pages(len(pages))
            log.info(
                "Firecrawl: %d targeted pages from %s (%d chars) [%d total used]",
                len(pages), url, len(combined), _pages_crawled,
            )
            return combined or None

    except Exception as e:
        log.warning(
            "Firecrawl targeted crawl failed for %s: %s — falling back to single scrape",
            url, e,
        )

    return _firecrawl_scrape_page(url)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
def scrape_page(url: str) -> Optional[str]:
    """Scrape a single URL and return Markdown (active crawler)."""
    if not url:
        return None
    if website_crawler() == "apify":
        from agent.integrations.apify import scrape_website_apify

        return scrape_website_apify(url, max_pages=1, max_depth=0)
    return _firecrawl_scrape_page(url)


def _firecrawl_scrape_page(url: str) -> Optional[str]:
    """Scrape a single URL via Firecrawl."""
    global _pages_crawled

    try:
        from firecrawl import FirecrawlApp
    except ImportError:
        return None

    if not _firecrawl_available():
        return None

    app = FirecrawlApp(api_key=os.environ["FIRECRAWL_API_KEY"])

    last_error: Exception | None = None
    for attempt in range(4):
        try:
            _throttle()
            result = app.scrape_url(
                url,
                params={
                    "formats": ["markdown", "links"],
                    "onlyMainContent": True,
                },
            )
            markdown = result.get("markdown", "").strip()
            # Append discovered hrefs so careers CTA targets survive onlyMainContent.
            links = result.get("links") or []
            if links and markdown:
                extra = []
                for item in links[:80]:
                    href = item if isinstance(item, str) else (item or {}).get("url") or ""
                    href = (href or "").strip()
                    if href.startswith("http") and href not in markdown:
                        extra.append(href)
                if extra:
                    markdown = markdown + "\n\n<!-- page_links -->\n" + "\n".join(extra)
            if markdown:
                _report_pages(1)
                log.info(
                    "Firecrawl: scraped %s (%d chars) [%d total used]",
                    url, len(markdown), _pages_crawled,
                )
                return markdown
            return None
        except Exception as e:
            last_error = e
            wait = _retry_after_seconds(e)
            if wait is not None and attempt < 3:
                log.warning(
                    "Firecrawl 429 for %s — waiting %.0fs (attempt %d/4)",
                    url, wait, attempt + 1,
                )
                time.sleep(wait)
                continue
            log.error("Firecrawl scrape failed for %s: %s", url, e)
            return None
    if last_error:
        log.error("Firecrawl scrape failed for %s: %s", url, last_error)
    return None


def get_pages_crawled() -> int:
    """Return total Firecrawl pages crawled this pipeline run."""
    return _pages_crawled


def reset_pages_crawled() -> None:
    """Reset the per-run page counter. Called at the start of each pipeline run."""
    global _pages_crawled
    _pages_crawled = 0


def scrape_urls(urls: list[str], *, concurrency: int = 3) -> Optional[str]:
    """
    Scrape specific URLs (map-selected outreach pages) and join as markdown.
    Preserves input order when using Firecrawl; Apify returns dataset order.
    """
    cleaned = [u for u in urls if u]
    if not cleaned:
        return None

    if website_crawler() == "apify":
        from agent.integrations.apify import scrape_website_urls_apify

        return scrape_website_urls_apify(
            cleaned,
            max_pages=max(1, min(len(cleaned), 12)),
            max_depth=0,
        )

    from agent.utils.concurrency import map_ordered

    def _one(url: str) -> tuple[str, Optional[str]]:
        return url, _firecrawl_scrape_page(url)

    pairs = map_ordered(
        cleaned,
        _one,
        concurrency=1,
        label="Firecrawl scrape",
    )
    sections: list[str] = []
    for url, content in pairs:
        if content and content.strip():
            # scrape_page may already prefix ### [url]
            body = content.strip()
            if body.startswith("### ["):
                sections.append(body)
            else:
                sections.append(f"### [{url}]\n{body}")
    return "\n\n---\n\n".join(sections) if sections else None


def _sitemap_urls(url: str, limit: int = 100) -> list[str]:
    """Fetch sitemap.xml directly (no crawler credits)."""
    links: list[str] = []
    if not url:
        return links
    if "://" not in url:
        url = f"https://{url}"
    base = url.rstrip("/")

    import httpx

    sitemap_urls = [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/sitemap-index.xml",
    ]
    loc_re = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I)
    seen: set[str] = set()
    try:
        with httpx.Client(follow_redirects=True, timeout=12, headers={
            "User-Agent": "Mozilla/5.0 (compatible; LeadGenBot/1.0)",
        }) as client:
            for sm in sitemap_urls:
                try:
                    resp = client.get(sm)
                    if resp.status_code >= 400:
                        continue
                    for match in loc_re.findall(resp.text):
                        u = match.strip()
                        if u and u not in seen:
                            seen.add(u)
                            links.append(u)
                    if links:
                        log.info("Sitemap: %d URLs from %s", len(links), sm)
                        break
                except Exception:
                    continue
    except Exception as e:
        log.warning("Sitemap fetch failed for %s: %s", base, e)
    return links[:limit]


def map_website_urls(url: str, limit: int = 100) -> list[str]:
    """
    Discover site URLs via sitemap.xml (default) or Firecrawl map when enabled.
    Used to find careers/jobs paths before targeted scrape.
    """
    if website_crawler() == "firecrawl" and _firecrawl_available():
        mapped = _firecrawl_map_urls(url, limit=limit)
        if mapped:
            return mapped
    return _sitemap_urls(url, limit=limit)


def _firecrawl_map_urls(url: str, limit: int = 100) -> list[str]:
    links: list[str] = []
    if not url:
        return links
    if "://" not in url:
        url = f"https://{url}"
    base = url.rstrip("/")

    try:
        from firecrawl import FirecrawlApp

        app = FirecrawlApp(api_key=os.environ["FIRECRAWL_API_KEY"])
        _throttle()
        result = app.map_url(base, params={"limit": limit})
        if isinstance(result, dict):
            raw_links = result.get("links") or result.get("urls") or []
        else:
            raw_links = getattr(result, "links", None) or []
        for item in raw_links:
            if isinstance(item, str):
                links.append(item)
            elif isinstance(item, dict) and item.get("url"):
                links.append(item["url"])
        if links:
            log.info("Firecrawl map: %d URLs for %s", len(links), base)
            return links[:limit]
    except Exception as e:
        log.warning("Firecrawl map failed for %s: %s — trying sitemap.xml", base, e)
    return []


def crawl_website_with_fallback(url: str, max_pages: int = 8) -> tuple[Optional[str], str]:
    """
    Crawl website content; returns (markdown, source).
    source: apify | firecrawl | none
    Default: Apify first; Firecrawl only if explicitly selected or as secondary.
    """
    primary = website_crawler()
    if primary == "apify":
        from agent.integrations.apify import scrape_website_apify

        apify_md = scrape_website_apify(url, max_pages=max_pages, max_depth=1)
        if apify_md and len(apify_md.strip()) >= 100:
            return apify_md, "apify"
        if _firecrawl_available():
            fc_md = _firecrawl_crawl_website(url, max_pages=max_pages)
            if fc_md and len(fc_md.strip()) >= 100:
                return fc_md, "firecrawl"
        return apify_md, "apify" if apify_md else "none"

    markdown = _firecrawl_crawl_website(url, max_pages=max_pages)
    if markdown and len(markdown.strip()) >= 200:
        return markdown, "firecrawl"

    from agent.integrations.apify import scrape_website_apify

    apify_md = scrape_website_apify(url, max_pages=max_pages, max_depth=1)
    if apify_md and len(apify_md.strip()) >= 100:
        return apify_md, "apify"
    if markdown:
        return markdown, "firecrawl"
    return apify_md, "apify" if apify_md else "none"
