"""
Firecrawl integration for targeted website intelligence.
Returns clean Markdown from only the pages that matter for lead scoring:
homepage, about, team, services, contact, news/blog, case studies.

We deliberately skip product pages, tag archives, pagination, etc.
This keeps token counts low and signal-to-noise high.

Credit protection:
  Each company website is capped at 5 pages by default. The run-level counter
  is kept for usage reporting only.
"""
from __future__ import annotations

import os
import threading
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from agent.utils.logger import log

_pages_crawled = 0
_pages_lock = threading.Lock()


def _report_pages(count: int) -> None:
    """Count pages for the run and attribute them to the current lead."""
    global _pages_crawled
    with _pages_lock:
        _pages_crawled += count
    from agent.utils.cost_tracker import get_cost_tracker
    tracker = get_cost_tracker()
    if tracker and count:
        tracker.add_usage(firecrawl_pages=count)

# Pages we actually want — the signal-rich pages for ICP scoring.
# English + Spanish patterns to handle Madrid agencies.
_TARGET_PATHS = [
    # Homepage is always the crawl start point — no pattern needed

    # About / company story
    "/about*", "/about-us*", "/who-we-are*", "/our-story*", "/company*",
    "/nosotros*", "/quienes-somos*", "/sobre-nosotros*", "/acerca*",

    # Team / people
    "/team*", "/our-team*", "/people*", "/meet-the-team*",
    "/equipo*", "/nuestro-equipo*",

    # Services / what we do
    "/services*", "/what-we-do*", "/solutions*", "/capabilities*", "/expertise*", "/work*",
    "/servicios*", "/soluciones*", "/que-hacemos*", "/trabajos*",

    # Case studies / portfolio / clients
    "/case-studies*", "/case-study*", "/portfolio*", "/clients*", "/projects*",
    "/casos*", "/clientes*", "/proyectos*", "/trabajos*",

    # Latest developments / news / blog (just the index page, not individual posts)
    "/news*", "/blog*", "/insights*", "/press*", "/resources*",
    "/noticias*", "/actualidad*",

    # Contact — sometimes has office locations, phone numbers
    "/contact*", "/get-in-touch*", "/contacto*",
]


def crawl_website(url: str, max_pages: int = 5) -> Optional[str]:
    """
    Crawl a company website, targeting only signal-rich pages.
    Uses Firecrawl includePaths to restrict to: about, team, services,
    case studies, news index, contact.

    max_pages=5 gives enough budget for homepage + 4 targeted pages.
    Falls back to a single-page homepage scrape if the targeted crawl fails.
    Returns None if the site is unreachable.
    """
    global _pages_crawled

    effective_max = max(1, max_pages)

    try:
        from firecrawl import FirecrawlApp
    except ImportError:
        log.error("firecrawl-py not installed — run: pip install firecrawl-py")
        return None

    app = FirecrawlApp(api_key=os.environ["FIRECRAWL_API_KEY"])

    # ── Targeted crawl ────────────────────────────────────────────────────────
    try:
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
        log.warning("Firecrawl targeted crawl failed for %s: %s — falling back to single scrape", url, e)

    # ── Fallback: homepage-only scrape ────────────────────────────────────────
    return scrape_page(url)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
def scrape_page(url: str) -> Optional[str]:
    """
    Scrape a single page (homepage) and return Markdown.
    Used as fallback when the targeted crawl returns nothing.
    """
    global _pages_crawled

    try:
        from firecrawl import FirecrawlApp
    except ImportError:
        return None

    app = FirecrawlApp(api_key=os.environ["FIRECRAWL_API_KEY"])

    try:
        result = app.scrape_url(
            url,
            params={
                "formats": ["markdown"],
                "onlyMainContent": True,
            },
        )
        markdown = result.get("markdown", "").strip()
        if markdown:
            _report_pages(1)
            log.info(
                "Firecrawl: scraped homepage %s (%d chars) [%d total used]",
                url, len(markdown), _pages_crawled,
            )
            return markdown
        return None
    except Exception as e:
        log.error("Firecrawl scrape failed for %s: %s", url, e)
        return None


def get_pages_crawled() -> int:
    """Return total pages crawled this pipeline run."""
    return _pages_crawled


def reset_pages_crawled() -> None:
    """Reset the per-run page counter. Called at the start of each pipeline run."""
    global _pages_crawled
    _pages_crawled = 0


def map_website_urls(url: str, limit: int = 100) -> list[str]:
    """
    Discover site URLs via Firecrawl map (preferred) or sitemap.xml fetch.
    Used to find careers/jobs paths before targeted scrape.
    Does not count toward Firecrawl page crawl budget when map succeeds.
    """
    links: list[str] = []
    if not url:
        return links
    if "://" not in url:
        url = f"https://{url}"
    base = url.rstrip("/")

    if os.environ.get("FIRECRAWL_API_KEY"):
        try:
            from firecrawl import FirecrawlApp

            app = FirecrawlApp(api_key=os.environ["FIRECRAWL_API_KEY"])
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

    # Fallback: fetch sitemap.xml directly (no Firecrawl credits)
    import re
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


def crawl_website_with_fallback(url: str, max_pages: int = 5) -> tuple[Optional[str], str]:
    """
    Crawl website content; returns (markdown, source).
    source: firecrawl | apify | none
    """
    markdown = crawl_website(url, max_pages=max_pages)
    if markdown and len(markdown.strip()) >= 200:
        return markdown, "firecrawl"

    from agent.integrations.apify import scrape_website_apify

    apify_md = scrape_website_apify(url, max_pages=max_pages)
    if apify_md and len(apify_md.strip()) >= 100:
        return apify_md, "apify"
    if markdown:
        return markdown, "firecrawl"
    return apify_md, "apify" if apify_md else "none"
