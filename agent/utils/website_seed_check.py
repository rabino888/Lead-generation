"""Homepage check before Apollo.

Rejects a seed when its website is dead, parked, closed, a jobs or social
platform, or a hosting page listed for a product brand. HTTP 401, 403, 408,
and 429 are kept: the site refused the fetch. A live store on a parent domain
(an acquired brand whose page names the parent) is kept.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html import unescape
from typing import Any, Callable, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Fetch answered, but the body is not evidence the site is down.
_KEEP_STATUSES = frozenset({401, 403, 408, 429})

# Jobs boards, social networks, and directories. A brand's own site is not one of these.
_PLATFORM_HOSTS = (
    "workable.com",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "smartrecruiters.com",
    "myworkdayjobs.com",
    "nextdoor.com",
    "facebook.com",
    "fb.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "tiktok.com",
    "linktr.ee",
    "wikipedia.org",
    "yelp.com",
    "amazon.com",
    "etsy.com",
    "ebay.com",
    "pinterest.com",
    "reddit.com",
    "crunchbase.com",
    "zoominfo.com",
    "google.com",
)

_PARKING_HOSTS = (
    "hugedomains.com",
    "afternic.com",
    "dan.com",
    "sedo.com",
    "bodis.com",
    "above.com",
    "parkingcrew.net",
)

_SHOPIFY_DEAD = (
    "this store is unavailable",
    "this shop is unavailable",
    "store is currently unavailable",
    "sorry, this store is currently unavailable",
)

_PARKED_PHRASES = (
    "domain is for sale",
    "this domain may be for sale",
    "buy this domain",
    "this domain is parked",
    "sedoparking",
)

_CLOSED = re.compile(
    r"had to close"
    r"|permanently closed"
    r"|no longer in business"
    r"|out of business"
    r"|ceased (?:trading|operations|business)"
    r"|closed our doors"
    r"|closed in (?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s+20\d\d",
    re.I,
)

_HOSTING_PHRASES = (
    "web hosting",
    "website hosting",
    "domain registration",
    "domain name registration",
    "shared hosting",
    "reseller hosting",
    "cloud server",
    "cloud servers",
    "cpanel",
    "vps hosting",
)

_PRODUCT_BRAND = re.compile(
    r"\b(?:apparel|textile|textiles|garment|skincare|cosmetic|cosmetics|"
    r"beauty|supplement|supplements|nutrition|fragrance|jewelry|jewellery|"
    r"furniture|fashion)\b",
    re.I,
)

_ORIGIN_ERROR = re.compile(
    r"error 52[0-6]|origin is unreachable|web server is down|bad gateway error",
    re.I,
)


@dataclass
class HomepageSnapshot:
    status: Optional[int]
    final_url: str
    html: str = ""
    error: str = ""


def _host(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.netloc or parsed.path or "").lower().split("@")[-1]
    return host.split(":")[0].removeprefix("www.")


def _host_is(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def visible_text(html: str) -> str:
    no_script = re.sub(r"(?is)<(script|style|noscript)\b[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?is)<[^>]+>", " ", no_script)
    return re.sub(r"\s+", " ", unescape(text)).strip().lower()


def _company_allows_platform(company_name: str, host: str) -> bool:
    name = re.sub(r"[^a-z0-9]+", " ", (company_name or "").lower())
    label = host.split(".")[0]
    if len(label) < 3:
        return False
    return bool(re.search(rf"\b{re.escape(label)}\b", name))


def homepage_problem(
    company_name: str,
    website: str,
    snapshot: HomepageSnapshot,
    *,
    industry: str = "",
) -> Optional[str]:
    """Return a rejection reason, or None when the seed may go to Apollo."""
    if snapshot.error == "unknown":
        return None
    status = snapshot.status
    if status in _KEEP_STATUSES:
        return None
    if snapshot.error == "http" or status is None:
        return "site_unreachable"
    if status in (404, 410):
        return f"site_not_found:{status}"
    if status >= 500 or status >= 400:
        return f"site_http_{status}"

    final_host = _host(snapshot.final_url or website)
    if any(_host_is(final_host, host) for host in _PLATFORM_HOSTS):
        if not _company_allows_platform(company_name, final_host):
            return f"wrong_site:platform:{final_host}"
    if final_host == "shopify.com" or final_host.endswith(".shopify.com"):
        return "wrong_site:platform:shopify.com"
    if any(_host_is(final_host, host) for host in _PARKING_HOSTS):
        return f"site_parked:{final_host}"

    text = visible_text(snapshot.html)
    if not text:
        return None
    if any(phrase in text for phrase in _SHOPIFY_DEAD):
        return "site_shopify_unavailable"
    if any(phrase in text for phrase in _PARKED_PHRASES):
        return "site_parked"
    if _CLOSED.search(text[:3000]):
        return "site_closed"
    if len(text) < 5000 and _ORIGIN_ERROR.search(text):
        return "site_origin_error"

    blob = f"{company_name} {industry}"
    hosting_hits = sum(1 for phrase in _HOSTING_PHRASES if phrase in text)
    if hosting_hits >= 2 and _PRODUCT_BRAND.search(blob):
        return "wrong_site:different_business"
    return None


def fetch_homepage(website: str) -> HomepageSnapshot:
    """GET the homepage. 429 is returned as a status so the seed is kept."""
    raw = (website or "").strip()
    if not raw:
        return HomepageSnapshot(status=None, final_url="", error="http")
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        import httpx
    except ImportError:
        return HomepageSnapshot(status=None, final_url=raw, error="unknown")
    headers = {"User-Agent": _BROWSER_UA, "Accept": "text/html"}
    try:
        with httpx.Client(follow_redirects=True, timeout=12, headers=headers) as client:
            resp = client.get(raw)
    except httpx.HTTPError:
        return HomepageSnapshot(status=None, final_url=raw, error="http")
    except Exception as exc:
        log.warning("Homepage check failed open for %s: %s", raw, exc)
        return HomepageSnapshot(status=None, final_url=raw, error="unknown")
    return HomepageSnapshot(
        status=resp.status_code,
        final_url=str(resp.url),
        html=(resp.text or "")[:500_000],
    )


def filter_website_seeds(
    rows: list[dict[str, Any]],
    *,
    fetcher: Callable[[str], HomepageSnapshot] = fetch_homepage,
    max_workers: int = 6,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split seed rows into kept and rejected. Rejection uses pre_apollo_reason."""
    indexed = list(enumerate(rows))
    snapshots: dict[int, HomepageSnapshot] = {}

    def _one(item: tuple[int, dict[str, Any]]) -> tuple[int, HomepageSnapshot]:
        idx, row = item
        website = (row.get("website") or "").strip()
        if not website:
            return idx, HomepageSnapshot(status=None, final_url="", error="http")
        try:
            return idx, fetcher(website)
        except Exception as exc:
            log.warning("Homepage fetch failed open for %s: %s", website, exc)
            return idx, HomepageSnapshot(status=None, final_url=website, error="unknown")

    workers = max(1, min(max_workers, len(indexed) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, item) for item in indexed]
        for future in as_completed(futures):
            idx, snap = future.result()
            snapshots[idx] = snap

    http_failures = sum(
        1
        for idx, _row in indexed
        if snapshots[idx].error == "http" and snapshots[idx].status is None
    )
    if indexed and http_failures == len(indexed):
        log.error("Homepage check could not reach any seed; keeping all %d rows", len(indexed))
        return [dict(row) for row in rows], []

    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for idx, row in indexed:
        reason = homepage_problem(
            row.get("company_name") or "",
            row.get("website") or "",
            snapshots[idx],
            industry=row.get("industry") or "",
        )
        if reason:
            out = dict(row)
            out["pre_apollo_reason"] = reason
            rejected.append(out)
            log.info(
                "Homepage reject %s (%s): %s",
                row.get("company_name") or "",
                row.get("website") or "",
                reason,
            )
        else:
            kept.append(dict(row))
    return kept, rejected


def apply_website_seed_gate(
    kept: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    *,
    fetcher: Callable[[str], HomepageSnapshot] = fetch_homepage,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the homepage check and append its rejects to the pre-Apollo rejects."""
    web_kept, web_rejected = filter_website_seeds(kept, fetcher=fetcher)
    return web_kept, list(rejected) + web_rejected
