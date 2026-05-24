"""
Firecrawl integration for company website deep-crawl.
Returns clean Markdown ready for LLM analysis.

Credit protection:
  FIRECRAWL_MAX_PAGES_PER_RUN env var (default: 25) caps total pages per
  server process. Once the cap is hit, further crawl requests are skipped
  so a single run can't drain the monthly quota.
"""
from __future__ import annotations

import os
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from agent.utils.logger import log

_pages_crawled = 0

# Hard cap on pages per server lifetime (resets on restart / Railway redeploy).
# Each site uses max 3 pages by default, so 25 pages ≈ 8 full site crawls per run.
_MAX_PAGES = int(os.environ.get("FIRECRAWL_MAX_PAGES_PER_RUN", "25"))


def crawl_website(url: str, max_pages: int = 3) -> Optional[str]:
    """
    Crawl a company website and return all content as clean Markdown.
    Uses Firecrawl /crawl endpoint (follows internal links up to max_pages).
    Falls back to single-page scrape if crawl fails.
    Returns None if the site is unreachable or the page cap is reached.

    max_pages default is 3 (home + about + services) to conserve credits.
    """
    global _pages_crawled

    # ── Credit guard ──────────────────────────────────────────────────────────
    if _pages_crawled >= _MAX_PAGES:
        log.warning(
            "Firecrawl: page cap (%d) reached — skipping %s (already used %d pages this run)",
            _MAX_PAGES, url, _pages_crawled,
        )
        return None

    # Don't request more pages than the remaining budget allows
    remaining_budget = _MAX_PAGES - _pages_crawled
    max_pages = min(max_pages, remaining_budget)

    try:
        from firecrawl import FirecrawlApp
    except ImportError:
        log.error("firecrawl-py not installed — run: pip install firecrawl-py")
        return None

    app = FirecrawlApp(api_key=os.environ["FIRECRAWL_API_KEY"])

    # Try full crawl first
    try:
        result = app.crawl_url(
            url,
            params={
                "limit": max_pages,
                "scrapeOptions": {
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                },
            },
        )
        pages = result.get("data", [])
        if pages:
            combined = "\n\n---\n\n".join(
                p.get("markdown", "") for p in pages if p.get("markdown")
            )
            _pages_crawled += len(pages)
            log.info(
                "Firecrawl crawled %d pages from %s (%d chars) [%d/%d used]",
                len(pages), url, len(combined), _pages_crawled, _MAX_PAGES,
            )
            return combined or None
    except Exception as e:
        log.warning("Firecrawl crawl failed for %s: %s — trying single scrape", url, e)

    # Fallback: single page scrape (costs 1 page)
    return scrape_page(url)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
def scrape_page(url: str) -> Optional[str]:
    """
    Scrape a single page and return Markdown.
    Used as fallback when full crawl fails.
    """
    global _pages_crawled

    # Credit guard applies to single scrapes too
    if _pages_crawled >= _MAX_PAGES:
        log.warning("Firecrawl: page cap reached — skipping scrape of %s", url)
        return None

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
        markdown = result.get("markdown", "")
        if markdown:
            _pages_crawled += 1
            log.info(
                "Firecrawl scraped single page: %s (%d chars) [%d/%d used]",
                url, len(markdown), _pages_crawled, _MAX_PAGES,
            )
            return markdown
        return None
    except Exception as e:
        log.error("Firecrawl scrape failed for %s: %s", url, e)
        return None


def get_pages_crawled() -> int:
    """Return total pages crawled in this process lifetime."""
    return _pages_crawled


def reset_pages_crawled() -> None:
    """
    Reset the page counter. Call at the start of each pipeline run if you
    want the cap to apply per-run rather than per-server-restart.
    """
    global _pages_crawled
    _pages_crawled = 0
