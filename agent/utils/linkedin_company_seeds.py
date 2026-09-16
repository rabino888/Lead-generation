"""
Deterministic LinkedIn *company* search seeding (never jobs).

1. Build company-search URLs + query params from ICP (keywords + geo).
2. Scrape via harvestapi/linkedin-company-search (Full mode → official website).

Jobs search is intentionally not used: it finds whoever is hiring for a keyword,
including recruiters, and its companyWebsite field is often wrong vs company About.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import quote_plus, urlparse

from agent.utils.linkedin_seed_urls import (
    resolve_linkedin_geo,
    seed_keywords_from_icp,
)

DEFAULT_ACTOR = "harvestapi/linkedin-company-search"
_MAX_QUERIES = 6


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def build_linkedin_company_search_urls_from_icp(
    icp: dict[str, Any],
    *,
    max_urls: int = _MAX_QUERIES,
) -> list[str]:
    """
    Deterministic LinkedIn company-search URLs from ICP.

    Format: /search/results/companies/?keywords=…&geoUrn=["geoId"]
    """
    keywords = seed_keywords_from_icp(icp, max_keywords=max_urls)
    if not keywords:
        raise ValueError(
            "Cannot build LinkedIn company-search URLs: need include_keywords, "
            "industries, or non-executive job_titles on the ICP."
        )
    location, geo_id = resolve_linkedin_geo(icp)
    urls: list[str] = []
    for kw in keywords:
        parts = [f"keywords={quote_plus(kw)}"]
        if geo_id:
            # LinkedIn company search uses geoUrn as a JSON array of geoIds
            parts.append(f"geoUrn={quote_plus(json.dumps([geo_id]))}")
        elif location:
            parts.append(f"companyHqGeo={quote_plus(location)}")
        raw = "https://www.linkedin.com/search/results/companies/?" + "&".join(parts)
        urls.append(raw)
    return urls


def build_company_search_queries_from_icp(
    icp: dict[str, Any],
    *,
    max_queries: int = _MAX_QUERIES,
) -> list[dict[str, Any]]:
    """Actor-ready queries: searchQuery + locations (+ audit URL)."""
    keywords = seed_keywords_from_icp(icp, max_keywords=max_queries)
    if not keywords:
        raise ValueError(
            "Cannot build LinkedIn company-search queries: need include_keywords, "
            "industries, or non-executive job_titles on the ICP."
        )
    location, geo_id = resolve_linkedin_geo(icp)
    urls = build_linkedin_company_search_urls_from_icp(icp, max_urls=max_queries)
    out: list[dict[str, Any]] = []
    for i, kw in enumerate(keywords):
        out.append(
            {
                "searchQuery": kw,
                "locations": [location] if location else [],
                "geo_id": geo_id,
                "url": urls[i] if i < len(urls) else "",
            }
        )
    return out


def can_auto_seed_linkedin_companies_from_icp(icp: Optional[dict[str, Any]]) -> bool:
    if not isinstance(icp, dict) or not icp:
        return False
    try:
        return bool(build_company_search_queries_from_icp(icp))
    except ValueError:
        return False


def preview_auto_linkedin_companies_seed(
    icp: dict[str, Any], *, seed_count: int = 80
) -> dict[str, Any]:
    try:
        queries = build_company_search_queries_from_icp(icp)
        urls = [q.get("url") or "" for q in queries]
        location, geo_id = resolve_linkedin_geo(icp)
        return {
            "ok": True,
            "mode": "auto_linkedin_companies",
            "query_count": len(queries),
            "queries": queries,
            "urls": urls,
            "location": location,
            "geo_id": geo_id,
            "seed_count": seed_count,
            "message": (
                f"No seeds.csv yet — Start will build {len(queries)} LinkedIn company-search "
                f"URL(s) from this ICP and scrape via Apify (paid, not jobs), then continue."
            ),
        }
    except ValueError as exc:
        return {
            "ok": False,
            "mode": "missing",
            "query_count": 0,
            "queries": [],
            "urls": [],
            "message": str(exc),
        }


def persist_auto_linkedin_company_seed_sources(
    campaign_dir: Any,
    campaign_id: str,
    icp: dict[str, Any],
    *,
    seed_count: int = 80,
) -> dict[str, Any]:
    from pathlib import Path

    path = Path(campaign_dir)
    queries = build_company_search_queries_from_icp(icp)
    urls = [q.get("url") or "" for q in queries if q.get("url")]
    location, geo_id = resolve_linkedin_geo(icp)
    entry = {
        "id": "linkedin_companies",
        "type": "linkedin_companies",
        "name": "Auto LinkedIn companies (from ICP)",
        "why_chosen": (
            "Dashboard gated run: company-search URLs built from ICP keywords + geo "
            "(never LinkedIn jobs)."
        ),
        "enabled": True,
        "tier": "primary",
        "config": {
            "max_results": int(seed_count),
            "geo_segment": location,
            "queries": queries,
            "urls": urls,
            "actor": DEFAULT_ACTOR,
            "scraper_mode": "full",
            "auto_from_icp": True,
            "geo_id": geo_id,
            "firmographics": False,
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
        if sid == "linkedin_companies":
            out_sources.append(entry)
            replaced = True
        elif sid == "linkedin_jobs":
            disabled = dict(s)
            disabled["enabled"] = False
            disabled["why_chosen"] = (
                "Blocked: seeding uses linkedin_companies (company search), never jobs."
            )
            out_sources.append(disabled)
        else:
            out_sources.append(s)
    if not replaced:
        out_sources.insert(0, entry)
    cfg["campaign_id"] = campaign_id
    cfg["sources"] = out_sources
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry


def scrape_linkedin_companies(
    queries: list[dict[str, Any]],
    *,
    max_results: int = 80,
    actor_id: str = DEFAULT_ACTOR,
    scraper_mode: str = "full",
) -> list[dict]:
    """Run harvestapi company search once per query; return merged company dicts."""
    from apify_client import ApifyClient

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN required")

    client = ApifyClient(token)
    per_query = max(5, int(max_results) // max(1, len(queries)))
    seen: set[str] = set()
    items: list[dict] = []

    for q in queries:
        search_query = (q.get("searchQuery") or q.get("keyword") or "").strip()
        if not search_query:
            continue
        locations = list(q.get("locations") or [])
        run_input: dict[str, Any] = {
            "scraperMode": scraper_mode if scraper_mode in ("short", "full") else "full",
            "searchQuery": search_query,
            "maxItems": per_query,
            "startPage": 1,
        }
        if locations:
            run_input["locations"] = locations
        run = client.actor(actor_id).call(run_input=run_input)
        dataset_id = (
            run.default_dataset_id
            if hasattr(run, "default_dataset_id")
            else run.get("defaultDatasetId")
        )
        for item in client.dataset(dataset_id).iterate_items():
            if not isinstance(item, dict):
                continue
            key = (
                (item.get("linkedinUrl") or item.get("url") or item.get("universalName") or "")
                .strip()
                .lower()
            )
            if not key:
                key = _norm(item.get("name") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(item)
            if len(items) >= max_results:
                return items
    return items


def _normalize_website(url: str) -> str:
    if not url:
        return ""
    url = str(url).strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    parsed = urlparse(url)
    if not parsed.netloc:
        return ""
    return f"https://{parsed.netloc.lower()}"


def _location_from_company(item: dict) -> str:
    locs = item.get("locations") or []
    if isinstance(locs, list):
        for loc in locs:
            if not isinstance(loc, dict):
                continue
            if loc.get("headquarter"):
                parsed = loc.get("parsed") if isinstance(loc.get("parsed"), dict) else {}
                text = (parsed.get("text") or "").strip()
                if text:
                    return text
                city = (loc.get("city") or "").strip()
                country = (loc.get("country") or "").strip()
                if city and country:
                    return f"{city}, {country}"
                if city:
                    return city
        for loc in locs:
            if not isinstance(loc, dict):
                continue
            parsed = loc.get("parsed") if isinstance(loc.get("parsed"), dict) else {}
            text = (parsed.get("text") or "").strip()
            if text:
                return text
    hq = item.get("headquarter") or item.get("hq") or ""
    if isinstance(hq, dict):
        return (hq.get("city") or hq.get("text") or "").strip()
    return str(hq or item.get("location") or "").strip()


def _industry_from_company(item: dict) -> str:
    industries = item.get("industries") or item.get("industry") or ""
    if isinstance(industries, list):
        names = []
        for i in industries:
            if isinstance(i, dict):
                names.append(str(i.get("name") or ""))
            else:
                names.append(str(i))
        return ", ".join(n for n in names if n)
    return str(industries or "")


def companies_to_seed_rows(
    companies: list[dict],
    *,
    geo_segment: str,
    prefix: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for i, item in enumerate(companies, start=1):
        name = (item.get("name") or item.get("companyName") or "").strip()
        website = _normalize_website(item.get("website") or item.get("websiteUrl") or "")
        if not name or not website:
            continue
        linkedin = (
            item.get("linkedinUrl")
            or item.get("url")
            or ""
        ).split("?")[0].rstrip("/")
        emp = item.get("employeeCount")
        if emp is None:
            er = item.get("employeeCountRange") or {}
            if isinstance(er, dict) and er.get("start") is not None:
                emp = er.get("start")
        rows.append(
            {
                "seed_id": f"{prefix}-{i:04d}",
                "company_name": name,
                "website": website,
                "location": _location_from_company(item),
                "geo_segment": geo_segment,
                "source_url": linkedin,
                "status": "pending",
                "notes": "LinkedIn company search",
                "industry": _industry_from_company(item),
                "company_size": str(emp) if emp is not None else "",
                "company_linkedin_url": linkedin,
            }
        )
    return rows
