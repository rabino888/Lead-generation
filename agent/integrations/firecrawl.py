"""
Firecrawl integration for company website deep-crawl.
Returns clean Markdown ready for LLM analysis.
"""
from __future__ import annotations

import os
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from agent.utils.logger import log

_pages_crawled = 0


def crawl_website(url: str, max_pages: int = 10) -> Optional[str]:
    """
    Crawl a company website and return all content as clean Markdown.
    Uses Firecrawl /crawl endpoint (follows internal links).
    Falls back to single-page scrape if crawl fails.
    Returns None if the site is unreachable.
    """
    global _pages_crawled

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
            log.info("Firecrawl crawled %d pages from %s (%d chars)", len(pages), url, len(combined))
            return combined or None
    except Exception as e:
        log.warning("Firecrawl crawl failed for %s: %s — trying single scrape", url, e)

    # Fallback: single page scrape
    return scrape_page(url)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
def scrape_page(url: str) -> Optional[str]:
    """
    Scrape a single page and return Markdown.
    Used as fallback when full crawl fails.
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
        markdown = result.get("markdown", "")
        if markdown:
            _pages_crawled += 1
            log.info("Firecrawl scraped single page: %s (%d chars)", url, len(markdown))
            return markdown
        return None
    except Exception as e:
        log.error("Firecrawl scrape failed for %s: %s", url, e)
        return None


def get_pages_crawled() -> int:
    """Return total pages crawled in this session."""
    return _pages_crawled
