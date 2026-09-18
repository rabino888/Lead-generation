"""
Talent T01: LinkedIn people seeding (CSV ingest + Apify HarvestAPI people search).

Default actor: harvestapi/linkedin-profile-search (override via
APIFY_LINKEDIN_PEOPLE_SEARCH_ACTOR or seed_sources config.actor only).
Prefer Short / search-card fields for seed cost — full profiles are T04.
"""
from __future__ import annotations

import csv
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlparse

from agent.utils.talent_people import (
    STAGE_T01_RAW_PEOPLE,
    person_row_from_mapping,
    talent_stage_path,
    write_people_csv,
)

log = logging.getLogger("leadgen.linkedin_people_seeds")

DEFAULT_PEOPLE_ACTOR = "harvestapi/linkedin-profile-search"
DEFAULT_PROFILE_SCRAPER_MODE = "Short"


def resolve_people_actor(actor_id: str = "") -> str:
    """config/CLI actor → env override → locked HarvestAPI default."""
    explicit = (actor_id or "").strip()
    if explicit:
        return explicit
    env = (os.environ.get("APIFY_LINKEDIN_PEOPLE_SEARCH_ACTOR") or "").strip()
    if env:
        return env
    return DEFAULT_PEOPLE_ACTOR


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            s = str(item or "").strip()
            if s:
                out.append(s)
        return out
    s = str(value).strip()
    return [s] if s else []


def filters_from_people_search_urls(urls: list[str]) -> dict[str, str]:
    """
    Extract HarvestAPI-friendly filters from LinkedIn people-search URLs.

    Supports ``keywords=`` → searchQuery. Returns empty dict if nothing usable.
    """
    query_bits: list[str] = []
    for raw in urls:
        u = (raw or "").strip()
        if not u or "linkedin.com" not in u.lower():
            continue
        try:
            parsed = urlparse(u)
        except Exception:
            continue
        path = (parsed.path or "").lower()
        if "/search/results/people" not in path and "/search/results/people/" not in path:
            # Accept /search/results/people without trailing nuance
            if "search/results/people" not in path:
                continue
        qs = parse_qs(parsed.query or "")
        for key in ("keywords", "keyword", "title"):
            for val in qs.get(key) or []:
                text = unquote_plus(str(val or "")).strip()
                if text and text not in query_bits:
                    query_bits.append(text)
    if not query_bits:
        return {}
    return {"query": " ".join(query_bits)}


def build_people_search_input(
    *,
    query: str = "",
    title: str | list[str] = "",
    location: str | list[str] = "",
    max_items: int = 100,
    profile_scraper_mode: str = DEFAULT_PROFILE_SCRAPER_MODE,
    urls: list[str] | None = None,
) -> dict[str, Any]:
    """
    HarvestAPI linkedin-profile-search input.

    Fields: searchQuery, currentJobTitles, locations, maxItems, profileScraperMode.
    Default mode Short keeps seed cost on search cards (not full T04 profiles).

    LinkedIn people-search URLs may supply ``keywords`` when query/title/location
    are empty. Profile ``/in/`` URLs are not valid seed-search input.
    """
    search_query = (query or "").strip()
    titles = _as_str_list(title)
    locations = _as_str_list(location)
    clean_urls = [u.strip() for u in (urls or []) if (u or "").strip()]
    if not search_query and not titles and not locations and clean_urls:
        extracted = filters_from_people_search_urls(clean_urls)
        search_query = (extracted.get("query") or "").strip()
    if not search_query and not titles and not locations:
        raise ValueError(
            "people search input needs query, title, or location "
            "(or a LinkedIn /search/results/people URL with keywords=…)"
        )

    mode = (profile_scraper_mode or DEFAULT_PROFILE_SCRAPER_MODE).strip()
    if mode.lower() == "short":
        mode = "Short"
    elif mode.lower() == "full":
        mode = "Full"
    elif mode not in ("Short", "Full", "Full + email search"):
        mode = DEFAULT_PROFILE_SCRAPER_MODE

    run_input: dict[str, Any] = {
        "profileScraperMode": mode,
        "maxItems": max(1, int(max_items)),
        "startPage": 1,
    }
    if search_query:
        run_input["searchQuery"] = search_query
    if titles:
        run_input["currentJobTitles"] = titles
    if locations:
        run_input["locations"] = locations
    # Pass people-search URLs for actors that honor them (incl. HarvestAPI).
    if clean_urls:
        run_input["startUrls"] = [{"url": u} for u in clean_urls]
        run_input["urls"] = clean_urls
    return run_input


def load_people_from_csv(csv_path: Path, *, seed_source_id: str, campaign_id: str) -> list[dict[str, str]]:
    if not csv_path.is_file():
        raise FileNotFoundError(f"People CSV not found: {csv_path}")
    prefix = re_prefix(campaign_id)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        for raw in csv.DictReader(f):
            row = person_row_from_mapping(
                dict(raw),
                seed_source_id=seed_source_id,
                campaign_prefix=prefix,
            )
            if not row:
                continue
            li = row["linkedin_url"]
            if li in seen:
                continue
            seen.add(li)
            out.append(row)
    return out


def re_prefix(campaign_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "", (campaign_id or "p")[:12]) or "p"


def _location_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parsed = value.get("parsed") if isinstance(value.get("parsed"), dict) else {}
        return (
            str(value.get("linkedinText") or "").strip()
            or str(parsed.get("text") or "").strip()
            or str(value.get("text") or "").strip()
            or str(value.get("country") or "").strip()
        )
    return str(value).strip()


def _current_company(item: dict[str, Any]) -> tuple[str, str]:
    positions = item.get("currentPosition") or item.get("currentPositions") or []
    if isinstance(positions, dict):
        positions = [positions]
    if isinstance(positions, list):
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            name = str(pos.get("companyName") or pos.get("name") or "").strip()
            url = str(
                pos.get("companyLinkedinUrl") or pos.get("companyUrl") or ""
            ).strip()
            if name or url:
                return name, url
    experience = item.get("experience") or []
    if isinstance(experience, list) and experience:
        first = experience[0]
        if isinstance(first, dict):
            return (
                str(first.get("companyName") or "").strip(),
                str(first.get("companyLinkedinUrl") or "").strip(),
            )
    return (
        str(item.get("companyName") or item.get("company") or "").strip(),
        str(item.get("companyUrl") or item.get("companyLinkedinUrl") or "").strip(),
    )


def items_from_apify_people(
    items: list[dict[str, Any]], *, seed_source_id: str, campaign_id: str
) -> list[dict[str, str]]:
    """Map HarvestAPI (or similar) people-search items → T01 rows."""
    prefix = re_prefix(campaign_id)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        nested = item.get("element") if isinstance(item.get("element"), dict) else item
        candidates = [
            nested.get("linkedinUrl"),
            nested.get("profileUrl"),
            nested.get("linkedin_url"),
            nested.get("url"),
        ]
        public_id = str(nested.get("publicIdentifier") or "").strip()
        if public_id and not public_id.startswith("http"):
            candidates.append(f"https://www.linkedin.com/in/{public_id}")
        linkedin_raw = ""
        for cand in candidates:
            s = str(cand or "").strip()
            if "linkedin.com/in/" in s.lower() or s.lower().startswith("/in/"):
                linkedin_raw = s
                break
        if not linkedin_raw:
            continue

        company_name, company_li = _current_company(nested)
        row = person_row_from_mapping(
            {
                "linkedin_url": linkedin_raw,
                "full_name": nested.get("fullName") or nested.get("name") or "",
                "first_name": nested.get("firstName") or "",
                "last_name": nested.get("lastName") or "",
                "headline": nested.get("headline") or nested.get("occupation") or "",
                "title": nested.get("title")
                or nested.get("jobTitle")
                or nested.get("occupation")
                or "",
                "location": _location_text(
                    nested.get("location")
                    or nested.get("geoLocationName")
                    or nested.get("addressWithCountry")
                ),
                "company_name": company_name,
                "company_linkedin_url": company_li,
                "source_url": nested.get("inputUrl") or nested.get("searchUrl") or "",
            },
            seed_source_id=seed_source_id,
            campaign_prefix=prefix,
        )
        if not row:
            continue
        if row["linkedin_url"] in seen:
            continue
        seen.add(row["linkedin_url"])
        out.append(row)
    return out


def scrape_linkedin_people(
    *,
    max_results: int,
    actor_id: str = "",
    query: str = "",
    title: str | list[str] = "",
    location: str | list[str] = "",
    profile_scraper_mode: str = DEFAULT_PROFILE_SCRAPER_MODE,
    urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Run HarvestAPI people-search actor (query/title/location/URL → Short cards)."""
    resolved_actor = resolve_people_actor(actor_id)
    clean_urls = [u.strip() for u in (urls or []) if (u or "").strip()]
    run_input = build_people_search_input(
        query=query,
        title=title,
        location=location,
        max_items=max_results,
        profile_scraper_mode=profile_scraper_mode,
        urls=clean_urls or None,
    )

    from apify_client import ApifyClient

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN required for linkedin_people seeding")

    client = ApifyClient(token)
    log.info(
        "Apify people search actor=%s mode=%s max=%s query=%r titles=%s locs=%s urls=%s",
        resolved_actor,
        run_input.get("profileScraperMode"),
        max_results,
        run_input.get("searchQuery") or "",
        run_input.get("currentJobTitles") or [],
        run_input.get("locations") or [],
        len(clean_urls),
    )
    run = client.actor(resolved_actor).call(run_input=run_input)
    dataset_id = (
        run.default_dataset_id
        if hasattr(run, "default_dataset_id")
        else run.get("defaultDatasetId")
    )
    items: list[dict[str, Any]] = []
    for item in client.dataset(dataset_id).iterate_items():
        if isinstance(item, dict):
            items.append(item)
            if len(items) >= max_results:
                break
    return items


def persist_t01(campaign_dir: Path, rows: list[dict[str, str]]) -> Path:
    out = talent_stage_path(campaign_dir, STAGE_T01_RAW_PEOPLE)
    write_people_csv(out, rows)
    return out
