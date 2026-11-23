"""
Meta Ad Library check via treg.

Records whether the company is running ads. A miss is "no", not a rejection.
Errors leave the fields blank so the post-run check can see the lookup failed.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

from agent.utils.logger import log

_TEXT_KEYS = (
    "body",
    "text",
    "title",
    "message",
    "caption",
    "link_title",
    "ad_creative_link_titles",
    "ad_creative_bodies",
)


def _domain(website: str) -> str:
    raw = (website or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    host = urlparse(raw).netloc.lower().removeprefix("www.")
    return host.split(":")[0]


def _ad_snippet(ad: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in _TEXT_KEYS:
        value = ad.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, list):
            parts.extend(str(item).strip() for item in value if str(item).strip())
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return text[:220]


def summarize_meta_ads(data: Any) -> Optional[dict[str, Any]]:
    """Map a treg ads payload to active / count / summary. None if the shape is missing."""
    if not isinstance(data, dict):
        return None
    out = data.get("output")
    if isinstance(out, list):
        ads = [row for row in out if isinstance(row, dict)]
        count = len(ads)
    elif isinstance(out, dict):
        raw_ads = out.get("ads")
        ads = [row for row in raw_ads if isinstance(row, dict)] if isinstance(raw_ads, list) else []
        raw_count = out.get("count")
        try:
            count = int(raw_count) if raw_count is not None else len(ads)
        except (TypeError, ValueError):
            count = len(ads)
        count = max(count, len(ads))
    else:
        return None
    snippets = []
    seen: set[str] = set()
    for ad in ads:
        snippet = _ad_snippet(ad)
        key = snippet.lower()
        if not snippet or key in seen:
            continue
        seen.add(key)
        snippets.append(snippet)
        if len(snippets) >= 5:
            break
    return {
        "meta_ads_active": "yes" if count else "no",
        "meta_ads_count": count,
        "meta_ads_summary": " | ".join(snippets),
    }


def fetch_meta_ads(*, company_name: str, website: str, facebook_url: str = "") -> Optional[dict[str, Any]]:
    """Look up active Meta ads for one company. None on a skipped or failed call."""
    domain = _domain(website)
    name = (company_name or "").strip()
    page = (facebook_url or "").strip()
    body: dict[str, str] = {}
    if name:
        body["company_name"] = name
    if domain:
        body["company_domain"] = domain
    if page:
        body["facebook_url"] = page
    if not body:
        return None
    try:
        from agent.integrations.treg import call_catalog, treg_configured

        if not treg_configured():
            return None
        data, _cost = call_catalog(
            "treg.meta-ads.library.advertiser",
            method="POST",
            body=body,
            headers={"X-Treg-Route-Max-Cost": "0.02"},
            idempotency_key=f"meta-ads:{domain or name}",
            timeout=90.0,
        )
    except Exception as exc:
        log.info("Meta ads skipped for %s: %s", name or domain, exc)
        return None
    summary = summarize_meta_ads(data)
    if summary is None:
        log.info("Meta ads reply for %s had no ads list", name or domain)
    else:
        log.info(
            "Meta ads for %s: %s (%s)",
            name or domain,
            summary["meta_ads_active"],
            summary["meta_ads_count"],
        )
    return summary
