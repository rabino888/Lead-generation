"""
Disk cache for crawled website markdown — enables LLM-only repair without re-crawling.

Files live under data/campaigns/{id}/website_cache/{domain}.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class CachedWebsiteMarkdown:
    markdown: str
    careers_url: str | None = None
    cached_at_utc: str | None = None


def _domain_key(website: str) -> str:
    raw = (website or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    host = urlparse(raw).netloc.lower().removeprefix("www.")
    return host.replace(":", "_")


def cache_dir(campaign_dir: Path) -> Path:
    return campaign_dir / "website_cache"


def cache_path(campaign_dir: Path, website: str) -> Path:
    key = _domain_key(website)
    if not key:
        raise ValueError("website URL required for cache path")
    return cache_dir(campaign_dir) / f"{key}.json"


def save_markdown(
    campaign_dir: Path,
    website: str,
    markdown: str,
    *,
    careers_url: str | None = None,
) -> Path:
    """Persist combined crawl markdown for later LLM-only repair."""
    text = (markdown or "").strip()
    if not text:
        raise ValueError("refusing to cache empty markdown")
    path = cache_path(campaign_dir, website)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "website": website,
        "domain": _domain_key(website),
        "markdown": text,
        "careers_url": careers_url or "",
        "cached_at_utc": datetime.now(UTC).isoformat(),
        "char_count": len(text),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load_markdown(campaign_dir: Path, website: str) -> CachedWebsiteMarkdown | None:
    path = cache_path(campaign_dir, website)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    md = (data.get("markdown") or "").strip()
    if len(md) < 100:
        return None
    careers = (data.get("careers_url") or "").strip() or None
    return CachedWebsiteMarkdown(
        markdown=md,
        careers_url=careers,
        cached_at_utc=data.get("cached_at_utc"),
    )


def has_cache(campaign_dir: Path, website: str) -> bool:
    return load_markdown(campaign_dir, website) is not None
