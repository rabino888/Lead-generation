"""Fetch a company URL and pull text for the plan. No paid crawler."""
from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urlparse

from agent.utils.client_intake import html_to_text, truncate_brief

Fetch = Callable[[str], tuple[bytes, str]]

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_RE = re.compile(
    r"<meta[^>]+(?:name|property)=[\"'](?:description|og:description|og:site_name)[\"'][^>]*>",
    re.I,
)
_CONTENT_RE = re.compile(r"content=[\"'](.*?)[\"']", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("company URL is required")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("company URL must be http(s) with a host")
    return raw


def host_of(url: str) -> str:
    host = urlparse(url).netloc.lower().split("@")[-1]
    if host.startswith("www."):
        host = host[4:]
    return host.split(":")[0]


def domain_label(url: str) -> str:
    host = host_of(url)
    parts = [part for part in host.split(".") if part]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else "client"


def _meta_values(html: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for tag in _META_RE.findall(html or ""):
        kind = ""
        for key in ("og:site_name", "og:description", "description"):
            if key in tag.lower():
                kind = key
                break
        match = _CONTENT_RE.search(tag)
        if kind and match:
            found[kind] = _TAG_RE.sub(" ", match.group(1))
    return found


def _title(html: str) -> str:
    match = _TITLE_RE.search(html or "")
    if not match:
        return ""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", match.group(1))).strip()


def company_name_from(html: str, url: str) -> str:
    meta = _meta_values(html)
    name = (meta.get("og:site_name") or "").strip()
    if name:
        return name[:120]
    title = _title(html)
    if title:
        chunk = re.split(r"\s+[|\-–—:]\s+", title, maxsplit=1)[0].strip()
        if chunk:
            return chunk[:120]
    return domain_label(url).replace("-", " ").title()


def default_fetch(url: str) -> tuple[bytes, str]:
    from agent.utils.client_intake import fetch_url_bytes

    return fetch_url_bytes(url)


def research_url(url: str, *, fetch: Fetch | None = None, notes: str = "") -> dict[str, Any]:
    """Return a research record. ``fetch`` is injectable so tests stay offline."""
    normalized = normalize_url(url)
    getter = fetch or default_fetch
    body, content_type = getter(normalized)
    html = body.decode("utf-8", errors="replace")
    text = html_to_text(html) if "<" in html[:2000] else html
    meta = _meta_values(html)
    description = (meta.get("og:description") or meta.get("description") or "").strip()
    combined = "\n\n".join(part for part in (description, text, notes) if (part or "").strip())
    return {
        "url": normalized,
        "host": host_of(normalized),
        "company_name": company_name_from(html, normalized),
        "title": _title(html),
        "description": description[:500],
        "text": truncate_brief(combined),
        "content_type": content_type,
        "operator_notes": (notes or "").strip(),
    }
