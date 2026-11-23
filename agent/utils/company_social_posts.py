"""
Company social post enrichment (Facebook / Instagram) via treg.

Soft-fail: missing URLs or API errors never raise — fields stay empty.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

from agent.utils.logger import log

_IG_HANDLE = re.compile(r"instagram\.com/([A-Za-z0-9._]+)", re.I)


def instagram_handle_from_url(url: str) -> str:
    m = _IG_HANDLE.search(url or "")
    if not m:
        return ""
    handle = m.group(1).strip().strip("/")
    if handle.lower() in {"p", "reel", "reels", "stories", "explore", "accounts"}:
        return ""
    return handle


def format_recent_posts(posts: list[dict[str, Any]], *, limit: int = 5) -> str:
    """One line per post, for the five latest posts that have text."""
    lines: list[str] = []
    for post in posts or []:
        if len(lines) >= limit:
            break
        if not isinstance(post, dict):
            continue
        text = (
            post.get("text")
            or post.get("caption")
            or post.get("message")
            or post.get("content")
            or ""
        )
        text = re.sub(r"\s+", " ", str(text).strip())
        if not text:
            continue
        when = str(post.get("posted_at") or post.get("date") or "").strip()
        snippet = text[:500]
        number = len(lines) + 1
        if when:
            lines.append(f"{number}. {when} — {snippet}")
        else:
            lines.append(f"{number}. {snippet}")
    return "\n".join(lines)


def _summarize_social_posts(posts: list[dict[str, Any]], *, limit: int = 5) -> str:
    return format_recent_posts(posts, limit=limit)


def _posts_from_treg_payload(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    out = data.get("output")
    if isinstance(out, dict):
        posts = out.get("posts")
        if isinstance(posts, list):
            return [p for p in posts if isinstance(p, dict)]
        nested = out.get("data")
        if isinstance(nested, dict):
            items = nested.get("items") or nested.get("posts")
            if isinstance(items, list):
                return [p for p in items if isinstance(p, dict)]
        if isinstance(nested, list):
            return [p for p in nested if isinstance(p, dict)]
    posts = data.get("posts")
    if isinstance(posts, list):
        return [p for p in posts if isinstance(p, dict)]
    return []


def fetch_facebook_posts_summary(page_url: str) -> str:
    """Recent Facebook page posts summary, or '' on miss/error."""
    url = (page_url or "").strip()
    if not url:
        return ""
    try:
        from agent.integrations.treg import call_catalog, treg_configured

        if not treg_configured():
            return ""
        data, _ = call_catalog(
            "treg.facebook.user.posts",
            method="POST",
            body={"url": url},
            idempotency_key=f"fb-posts:{url}",
            timeout=90.0,
        )
        from agent.utils.inbound_payload import map_inbound

        inbound = map_inbound(data, "post", source="treg.facebook.user.posts")
        return _summarize_social_posts(inbound["records"])
    except Exception as exc:
        log.info("Facebook posts skipped for %s: %s", url, exc)
        return ""


def fetch_instagram_posts_summary(profile_url: str) -> str:
    """Recent Instagram posts summary, or '' on miss/error."""
    handle = instagram_handle_from_url(profile_url)
    if not handle:
        return ""
    try:
        from agent.integrations.treg import call_catalog, treg_configured

        if not treg_configured():
            return ""
        data, _ = call_catalog(
            "treg.instagram.user.posts",
            method="POST",
            body={"username": handle},
            idempotency_key=f"ig-posts:{handle}",
            timeout=90.0,
        )
        from agent.utils.inbound_payload import map_inbound

        inbound = map_inbound(data, "post", source="treg.instagram.user.posts")
        return _summarize_social_posts(inbound["records"])
    except Exception as exc:
        log.info("Instagram posts skipped for @%s: %s", handle, exc)
        return ""


def enrich_website_social_posts(
    analysis: Any,
    *,
    facebook: bool = False,
    instagram: bool = False,
) -> Any:
    """
    Fill facebook_post_summary / instagram_post_summary on WebsiteAnalysis.

    No-ops when module off, URL missing, or API fails.
    """
    if analysis is None:
        return analysis
    if facebook and getattr(analysis, "social_facebook", None):
        if not (getattr(analysis, "facebook_post_summary", None) or "").strip():
            summary = fetch_facebook_posts_summary(analysis.social_facebook or "")
            if summary:
                analysis.facebook_post_summary = summary
    if instagram and getattr(analysis, "social_instagram", None):
        if not (getattr(analysis, "instagram_post_summary", None) or "").strip():
            summary = fetch_instagram_posts_summary(analysis.social_instagram or "")
            if summary:
                analysis.instagram_post_summary = summary
    return analysis


__all__ = [
    "enrich_website_social_posts",
    "fetch_facebook_posts_summary",
    "fetch_instagram_posts_summary",
    "instagram_handle_from_url",
]
