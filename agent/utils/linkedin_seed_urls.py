"""
Build LinkedIn jobs search URLs from a campaign ICP for Apify seeding.

Used by the dashboard gated run when no seeds.csv / stage seeds exist.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import quote_plus

from agent.utils.linkedin_jobs_seeds import normalize_linkedin_jobs_url

# LinkedIn Jobs geoId by normalized location token (country / common aliases).
_GEO: dict[str, tuple[str, str]] = {
    "united states": ("United States", "103644278"),
    "usa": ("United States", "103644278"),
    "us": ("United States", "103644278"),
    "spain": ("Spain", "105646813"),
    "españa": ("Spain", "105646813"),
    "espana": ("Spain", "105646813"),
    "united kingdom": ("United Kingdom", "101165590"),
    "uk": ("United Kingdom", "101165590"),
    "england": ("United Kingdom", "101165590"),
    "france": ("France", "105015875"),
    "germany": ("Germany", "101282230"),
    "italy": ("Italy", "103350119"),
    "netherlands": ("Netherlands", "102890719"),
    "portugal": ("Portugal", "100364837"),
    "mexico": ("Mexico", "103323778"),
    "brazil": ("Brazil", "106057199"),
    "canada": ("Canada", "101174742"),
    "australia": ("Australia", "101452733"),
    "india": ("India", "102713980"),
}

# Decision-maker titles are poor LinkedIn *jobs* keywords for company discovery.
_SKIP_TITLE = re.compile(
    r"^(founder|co-?founder|owner|propietario|propietaria|fundador|fundadora|"
    r"ceo|chief executive|director general|directora general|managing director|"
    r"president|presidente|presidenta|partner|socio|socia)$",
    re.I,
)

_DEFAULT_TPR = "r2592000"  # past month
_MAX_URLS = 8


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _as_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if x is not None and str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return []


def resolve_linkedin_geo(icp: dict[str, Any]) -> tuple[str, str]:
    """
    Return (location_label, geoId) for LinkedIn jobs URLs.

    Prefer a city/segment hint in the location text when present (e.g. Málaga),
    with country-level geoId.
    """
    geo = icp.get("geo") if isinstance(icp.get("geo"), dict) else {}
    primary = (geo.get("primary_location") or icp.get("location") or "").strip()
    contacts = _as_list(icp.get("contact_locations"))
    segments = _as_list(geo.get("segments"))
    hints = _as_list(geo.get("location_hints"))

    country_label = ""
    geo_id = ""
    for candidate in [primary, *contacts]:
        hit = _GEO.get(_norm(candidate))
        if hit:
            country_label, geo_id = hit
            break
    if not geo_id:
        for hint in hints:
            # match leading country tokens inside hints
            for key, pair in _GEO.items():
                if key in _norm(hint) or _norm(hint) == key:
                    country_label, geo_id = pair
                    break
            if geo_id:
                break

    city = ""
    for seg in segments:
        low = _norm(seg)
        if low in ("us", "usa", "spain", "default"):
            continue
        city = seg.strip()
        break
    if not city:
        for hint in hints:
            h = _norm(hint)
            if h in _GEO or h in ("spain", "españa", "espana", "usa", "united states"):
                continue
            # Prefer recognizable city-like hints
            if any(x in h for x in ("málaga", "malaga", "madrid", "barcelona", "marbella", "valencia")):
                city = hint.strip().title() if hint.islower() else hint.strip()
                # Normalize common accents for LinkedIn location string
                if _norm(city) in ("malaga", "málaga"):
                    city = "Málaga"
                break

    if not geo_id:
        # Last resort: location text only (Apify/LinkedIn may still resolve)
        label = city or primary or (contacts[0] if contacts else "")
        if not label:
            raise ValueError(
                "Cannot auto-build LinkedIn seed URLs: ICP has no geo / contact_locations."
            )
        return label, ""

    if city and country_label:
        location = f"{city}, {country_label}"
    else:
        location = country_label or city or primary
    return location, geo_id


def seed_keywords_from_icp(icp: dict[str, Any], *, max_keywords: int = _MAX_URLS) -> list[str]:
    """
    Keywords for LinkedIn jobs search → company discovery.

    Prefer include_keywords / industries over C-level job titles.
    """
    seen: set[str] = set()
    out: list[str] = []

    def add(term: str) -> None:
        t = (term or "").strip()
        if not t:
            return
        key = _norm(t)
        if key in seen or len(key) < 3:
            return
        seen.add(key)
        out.append(t)

    for kw in _as_list(icp.get("include_keywords")):
        add(kw)
        if len(out) >= max_keywords:
            return out

    for ind in _as_list(icp.get("industries")):
        add(ind)
        if len(out) >= max_keywords:
            return out

    for title in _as_list(icp.get("job_titles")):
        if _SKIP_TITLE.match(title.strip()):
            continue
        add(title)
        if len(out) >= max_keywords:
            return out

    return out


def build_linkedin_jobs_urls_from_icp(
    icp: dict[str, Any],
    *,
    max_urls: int = _MAX_URLS,
    past_month: bool = True,
) -> list[str]:
    """Build normalized LinkedIn /jobs/search/ URLs from ICP fields."""
    keywords = seed_keywords_from_icp(icp, max_keywords=max_urls)
    if not keywords:
        raise ValueError(
            "Cannot auto-build LinkedIn seed URLs: need include_keywords, industries, "
            "or non-executive job_titles on the ICP."
        )
    location, geo_id = resolve_linkedin_geo(icp)
    urls: list[str] = []
    for kw in keywords:
        parts = [f"keywords={quote_plus(kw)}", f"location={quote_plus(location)}"]
        if geo_id:
            parts.append(f"geoId={geo_id}")
        if past_month:
            parts.append(f"f_TPR={_DEFAULT_TPR}")
        raw = "https://www.linkedin.com/jobs/search/?" + "&".join(parts)
        urls.append(normalize_linkedin_jobs_url(raw))
    return urls


def can_auto_seed_from_icp(icp: Optional[dict[str, Any]]) -> bool:
    if not isinstance(icp, dict) or not icp:
        return False
    try:
        return bool(build_linkedin_jobs_urls_from_icp(icp))
    except ValueError:
        return False


def preview_auto_linkedin_seed(icp: dict[str, Any], *, seed_count: int = 80) -> dict[str, Any]:
    """Dashboard-facing preview of what Start would scrape."""
    try:
        urls = build_linkedin_jobs_urls_from_icp(icp)
        location, geo_id = resolve_linkedin_geo(icp)
        return {
            "ok": True,
            "mode": "auto_linkedin_jobs",
            "url_count": len(urls),
            "urls": urls,
            "location": location,
            "geo_id": geo_id,
            "seed_count": seed_count,
            "message": (
                f"No seeds.csv yet — Start will build {len(urls)} LinkedIn jobs URL(s) "
                f"from this ICP and scrape via Apify (paid), then continue the gated run."
            ),
        }
    except ValueError as exc:
        return {
            "ok": False,
            "mode": "missing",
            "url_count": 0,
            "urls": [],
            "message": str(exc),
        }


def persist_auto_linkedin_seed_sources(
    campaign_dir: Any,
    campaign_id: str,
    icp: dict[str, Any],
    *,
    seed_count: int = 80,
) -> dict[str, Any]:
    """Write/merge linkedin_jobs into seed_sources.json from ICP-built URLs."""
    from pathlib import Path

    path = Path(campaign_dir)
    urls = build_linkedin_jobs_urls_from_icp(icp)
    location, geo_id = resolve_linkedin_geo(icp)
    entry = {
        "id": "linkedin_jobs",
        "type": "linkedin_jobs",
        "name": "Auto LinkedIn jobs (from ICP)",
        "why_chosen": (
            "Dashboard gated run: URLs generated from ICP include_keywords / industries / geo."
        ),
        "enabled": True,
        "tier": "primary",
        "config": {
            "count": int(seed_count),
            "geo_segment": location,
            "urls": urls,
            "actor": "curious_coder/linkedin-jobs-scraper",
            "auto_from_icp": True,
            "geo_id": geo_id,
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
    # Replace existing linkedin_jobs / keep other sources (disable competing csv if empty)
    out_sources: list[dict] = []
    replaced = False
    for s in sources:
        sid = (s.get("id") or s.get("type") or "").strip()
        if sid == "linkedin_jobs":
            out_sources.append(entry)
            replaced = True
        else:
            out_sources.append(s)
    if not replaced:
        out_sources.insert(0, entry)
    cfg["campaign_id"] = campaign_id
    cfg["sources"] = out_sources
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry
