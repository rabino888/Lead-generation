"""
Google Maps → company seed discovery (Apify compass/crawler-google-places).

Preferred auto-seed path for company_outreach ICPs: finds businesses by
keyword + location, not job postings.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

from agent.utils.linkedin_seed_urls import resolve_linkedin_geo, seed_keywords_from_icp

_MAX_QUERIES = 4


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def maps_location_from_icp(icp: dict[str, Any]) -> str:
    """Human location string for Google Maps (city preferred)."""
    try:
        location, _geo_id = resolve_linkedin_geo(icp)
        return location
    except ValueError:
        geo = icp.get("geo") if isinstance(icp.get("geo"), dict) else {}
        primary = (geo.get("primary_location") or icp.get("location") or "").strip()
        contacts = icp.get("contact_locations") or []
        if isinstance(contacts, str):
            contacts = [contacts]
        return primary or (contacts[0] if contacts else "")


def maps_queries_from_icp(
    icp: dict[str, Any],
    *,
    max_queries: int = _MAX_QUERIES,
) -> list[dict[str, str]]:
    """
    Build Maps search queries from ICP keywords + geo.

    Prefer sector keywords (inmobiliaria, …) over executive titles.
    """
    location = maps_location_from_icp(icp)
    if not location:
        raise ValueError(
            "Cannot auto-build Google Maps queries: ICP has no geo / contact_locations."
        )
    keywords = seed_keywords_from_icp(icp, max_keywords=max_queries)
    if not keywords:
        raise ValueError(
            "Cannot auto-build Google Maps queries: need include_keywords, industries, "
            "or non-executive job_titles on the ICP."
        )
    # Drop pure geo tokens from keywords (already in location)
    loc_tokens = set(_norm(location).replace(",", " ").split())
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for kw in keywords:
        key = _norm(kw)
        if key in seen or key in loc_tokens:
            continue
        if any(t in loc_tokens for t in key.split() if len(t) > 3) and len(key.split()) == 1:
            # skip bare "Málaga" / "Costa del Sol" as the only keyword
            if key in loc_tokens or key.replace("á", "a") in loc_tokens:
                continue
        seen.add(key)
        out.append({"keyword": kw, "location": location})
        if len(out) >= max_queries:
            break
    if not out:
        out.append({"keyword": keywords[0], "location": location})
    return out


def can_auto_seed_maps_from_icp(icp: Optional[dict[str, Any]]) -> bool:
    if not isinstance(icp, dict) or not icp:
        return False
    try:
        return bool(maps_queries_from_icp(icp))
    except ValueError:
        return False


def preview_auto_google_maps_seed(icp: dict[str, Any], *, seed_count: int = 80) -> dict[str, Any]:
    try:
        queries = maps_queries_from_icp(icp)
        location = maps_location_from_icp(icp)
        return {
            "ok": True,
            "mode": "auto_google_maps",
            "query_count": len(queries),
            "queries": queries,
            "location": location,
            "seed_count": seed_count,
            "message": (
                f"No seeds.csv yet — Start will run {len(queries)} Google Maps search(es) "
                f"via Apify (paid) for companies near {location}, then continue the gated run."
            ),
        }
    except ValueError as exc:
        return {
            "ok": False,
            "mode": "missing",
            "query_count": 0,
            "queries": [],
            "message": str(exc),
        }


def persist_auto_google_maps_seed_sources(
    campaign_dir: Any,
    campaign_id: str,
    icp: dict[str, Any],
    *,
    seed_count: int = 80,
) -> dict[str, Any]:
    """Write/merge google_maps into seed_sources.json from ICP-built queries."""
    from pathlib import Path

    path = Path(campaign_dir)
    queries = maps_queries_from_icp(icp)
    location = maps_location_from_icp(icp)
    entry = {
        "id": "google_maps",
        "type": "google_maps",
        "name": "Auto Google Maps (from ICP)",
        "why_chosen": (
            "Dashboard gated run: company discovery by keyword + location "
            "(not LinkedIn jobs)."
        ),
        "enabled": True,
        "tier": "primary",
        "config": {
            "max_results": int(seed_count),
            "geo_segment": location,
            "queries": queries,
            "actor": "compass/crawler-google-places",
            "auto_from_icp": True,
            "firmographics": True,
        },
    }
    cfg_path = path / "seed_sources.json"
    cfg: dict[str, Any] = {"schema_version": "1", "campaign_id": campaign_id, "sources": []}
    if cfg_path.is_file():
        try:
            loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg = loaded
        except (OSError, json.JSONDecodeError):
            pass
    sources = [s for s in (cfg.get("sources") or []) if isinstance(s, dict)]
    out_sources: list[dict] = []
    replaced = False
    for s in sources:
        sid = (s.get("id") or s.get("type") or "").strip()
        if sid == "google_maps":
            out_sources.append(entry)
            replaced = True
        elif sid == "linkedin_jobs" and s.get("config", {}).get("auto_from_icp"):
            # Disable auto jobs when Maps is the discovery path
            disabled = dict(s)
            disabled["enabled"] = False
            disabled["why_chosen"] = (
                (disabled.get("why_chosen") or "")
                + " Disabled: company discovery uses google_maps, not job postings."
            ).strip()
            out_sources.append(disabled)
        else:
            out_sources.append(s)
    if not replaced:
        out_sources.insert(0, entry)
    cfg["campaign_id"] = campaign_id
    cfg["sources"] = out_sources
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry


def root_domain(url: str) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = f"https://{url}"
    return urlparse(url).netloc.lower().removeprefix("www.")
