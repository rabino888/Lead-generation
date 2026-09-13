"""
Local brief → ICP field heuristics (no paid LLM / vendor APIs).

Used when creating a campaign with an optional free-text brief so the ICP
questionnaire opens pre-filled. Operators can still edit before save.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Common country / region phrases → contact_locations value
_GEO_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(united states|u\.?s\.?a\.?|u\.?s\.?)\b", re.I), "United States"),
    (re.compile(r"\bunited kingdom\b|\bu\.?k\.?\b|\bbritain\b|\bengland\b", re.I), "United Kingdom"),
    (re.compile(r"\bspain\b|\bespaña\b|\bespana\b", re.I), "Spain"),
    (re.compile(r"\bgermany\b|\bdeutschland\b", re.I), "Germany"),
    (re.compile(r"\bfrance\b", re.I), "France"),
    (re.compile(r"\bnetherlands\b|\bholland\b", re.I), "Netherlands"),
    (re.compile(r"\bcanada\b", re.I), "Canada"),
    (re.compile(r"\baustralia\b", re.I), "Australia"),
    (re.compile(r"\bportugal\b", re.I), "Portugal"),
    (re.compile(r"\bitaly\b|\bitalia\b", re.I), "Italy"),
    (re.compile(r"\bireland\b", re.I), "Ireland"),
    (re.compile(r"\bsweden\b", re.I), "Sweden"),
    (re.compile(r"\bnorway\b", re.I), "Norway"),
    (re.compile(r"\bdenmark\b", re.I), "Denmark"),
    (re.compile(r"\bfinland\b", re.I), "Finland"),
    (re.compile(r"\bpoland\b", re.I), "Poland"),
    (re.compile(r"\bmexico\b", re.I), "Mexico"),
    (re.compile(r"\bbrazil\b", re.I), "Brazil"),
    (re.compile(r"\bindia\b", re.I), "India"),
    (re.compile(r"\bsingapore\b", re.I), "Singapore"),
    (re.compile(r"\beu\b|\beurope\b|\beu-?rope\b", re.I), "Europe"),
]

# Title tokens / phrases (order: longer first where relevant)
_TITLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bchief operating officers?\b|\bcoos?\b", re.I), "COO"),
    (re.compile(r"\bchief technology officers?\b|\bctos?\b", re.I), "CTO"),
    (re.compile(r"\bchief executive officers?\b|\bceos?\b", re.I), "CEO"),
    (re.compile(r"\bchief marketing officers?\b|\bcmos?\b", re.I), "CMO"),
    (re.compile(r"\bchief financial officers?\b|\bcfos?\b", re.I), "CFO"),
    (re.compile(r"\bchief product officers?\b|\bcpos?\b", re.I), "CPO"),
    (re.compile(r"\bchief information officers?\b|\bcios?\b", re.I), "CIO"),
    (re.compile(r"\bchief revenue officers?\b|\bcros?\b", re.I), "CRO"),
    (re.compile(r"\bchief people officers?\b", re.I), "Chief People Officer"),
    (re.compile(r"\bfounders?\b|\bco-?founders?\b", re.I), "Founder"),
    (re.compile(r"\bmarketing directors?\b|\bdirectors? of marketing\b", re.I), "Marketing Director"),
    (re.compile(r"\bsales directors?\b|\bdirectors? of sales\b", re.I), "Sales Director"),
    (re.compile(r"\bengineering directors?\b|\bdirectors? of engineering\b", re.I), "Engineering Director"),
    (re.compile(r"\bvp(?:s)?\s+of\s+marketing\b|\bvice presidents? of marketing\b", re.I), "VP Marketing"),
    (re.compile(r"\bvp(?:s)?\s+of\s+sales\b|\bvice presidents? of sales\b", re.I), "VP Sales"),
    (re.compile(r"\bvp(?:s)?\s+of\s+engineering\b|\bvice presidents? of engineering\b", re.I), "VP Engineering"),
    (re.compile(r"\bheads? of marketing\b", re.I), "Head of Marketing"),
    (re.compile(r"\bheads? of sales\b", re.I), "Head of Sales"),
    (re.compile(r"\bheads? of engineering\b", re.I), "Head of Engineering"),
    (re.compile(r"\bheads? of growth\b", re.I), "Head of Growth"),
    (re.compile(r"\bheads? of talent\b|\btalent acquisition managers?\b|\bta managers?\b", re.I), "Head of Talent"),
    (re.compile(r"\bgeneral managers?\b|\bgms?\b", re.I), "General Manager"),
    (re.compile(r"\bmanaging directors?\b|\bmds?\b", re.I), "Managing Director"),
    (re.compile(r"\bowners?\b", re.I), "Owner"),
    (re.compile(r"\bpresidents?\b", re.I), "President"),
]

_SIZE_BETWEEN = re.compile(
    r"\bbetween\s+(\d{1,6})\s+and\s+(\d{1,6})\s*(?:employees?|people|staff|fte)?\b",
    re.I,
)
_SIZE_RANGE = re.compile(
    r"\b(\d{1,6})\s*[-–—to]+\s*(\d{1,6})\s*(?:employees?|people|staff|fte)\b",
    re.I,
)
_SIZE_UNDER = re.compile(
    r"\b(?:under|fewer than|less than|up to|max(?:imum)?)\s+(\d{1,6})\s*(?:employees?|people|staff|fte)?\b",
    re.I,
)
_SIZE_OVER = re.compile(
    r"\b(?:over|more than|at least|min(?:imum)?)\s+(\d{1,6})\s*(?:employees?|people|staff|fte)?\b",
    re.I,
)

# "of marketing companies" / "marketing agencies" → include keywords
_INDUSTRY_OF = re.compile(
    r"\bof\s+((?:[\w/-]+\s+){0,3}(?:compan(?:y|ies)|agenc(?:y|ies)|firms?|startups?|businesses))\b",
    re.I,
)
_INDUSTRY_FOR = re.compile(
    r"\b(?:at|in|for)\s+((?:[\w/-]+\s+){0,3}(?:compan(?:y|ies)|agenc(?:y|ies)|firms?|startups?))\b",
    re.I,
)


def _uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def _extract_titles(text: str) -> list[str]:
    found: list[str] = []
    for pattern, label in _TITLE_PATTERNS:
        if pattern.search(text):
            found.append(label)
    return _uniq(found)


def _extract_locations(text: str) -> list[str]:
    found: list[str] = []
    for pattern, label in _GEO_ALIASES:
        if pattern.search(text):
            found.append(label)
    return _uniq(found)


def _extract_size(text: str) -> tuple[Optional[int], Optional[int]]:
    m = _SIZE_BETWEEN.search(text) or _SIZE_RANGE.search(text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (min(a, b), max(a, b))
    m = _SIZE_UNDER.search(text)
    if m:
        return (None, int(m.group(1)))
    m = _SIZE_OVER.search(text)
    if m:
        return (int(m.group(1)), None)
    return (None, None)


def _extract_keywords(text: str) -> list[str]:
    keywords: list[str] = []
    for pattern in (_INDUSTRY_OF, _INDUSTRY_FOR):
        m = pattern.search(text)
        if not m:
            continue
        phrase = re.sub(r"\s+", " ", m.group(1).strip())
        # Drop trailing company/agency noun; keep the descriptor (e.g. marketing)
        core = re.sub(
            r"\s+(compan(?:y|ies)|agenc(?:y|ies)|firms?|startups?|businesses)\s*$",
            "",
            phrase,
            flags=re.I,
        ).strip()
        if core and len(core) >= 3:
            keywords.append(core)
        elif phrase:
            keywords.append(phrase)
    # Bare "marketing companies" without needing "of"
    bare = re.search(
        r"\b((?:b2b|b2c|saas|software|fintech|healthcare|marketing|advertising|"
        r"agency|recruiting|staffing|logistics|manufacturing)(?:\s+\w+){0,2})\s+"
        r"(?:compan(?:y|ies)|agenc(?:y|ies)|firms?)\b",
        text,
        re.I,
    )
    if bare:
        keywords.append(bare.group(1).strip())
    return _uniq(keywords)


def _extract_industries(text: str) -> list[str]:
    """Industry labels for icp.industries (not the same as include_keywords)."""
    return _extract_keywords(text)


def parse_brief_to_icp(
    brief: str,
    *,
    campaign_id: str = "",
    campaign_type: str = "company_outreach",
) -> dict[str, Any]:
    """Return a partial icp.json dict from free-text brief. Empty brief → empty stub fields."""
    text = (brief or "").strip()
    titles = _extract_titles(text) if text else []
    locations = _extract_locations(text) if text else []
    size_min, size_max = _extract_size(text) if text else (None, None)
    industries = _extract_industries(text) if text else []

    primary = locations[0] if locations else ""
    icp: dict[str, Any] = {
        "schema_version": "1",
        "campaign_id": campaign_id,
        "job_titles": titles,
        "contact_locations": locations,
        "industries": industries,
        "excluded_industries": [],
        "company_size_min": size_min,
        "company_size_max": size_max,
        "include_keywords": [],
        "exclude_keywords": [],
        "excluded_name_patterns": "",
        "excluded_domains": [],
        "website_analysis_mode": "full",
        "brief_source": text[:2000] if text else "",
        "geo": {
            "primary_location": primary or None,
            "country_codes": {},
            "location_hints": [x.lower() for x in locations],
            "segments": list(locations),
        },
    }

    if campaign_type == "talent_search":
        icp["person_match"] = {
            "skills": industries,
            "exclude_keywords": [],
            "exclude_titles": [],
            "min_years_experience": None,
            "max_years_experience": None,
            "headline_allow": None,
        }
        icp.pop("website_analysis_mode", None)
        icp.pop("company_size_min", None)
        icp.pop("company_size_max", None)
        icp.pop("industries", None)
        icp.pop("excluded_industries", None)
        icp.pop("geo", None)

    return icp


def merge_brief_into_icp(
    existing: dict[str, Any],
    brief: str,
    *,
    campaign_id: str = "",
    campaign_type: str = "company_outreach",
    only_fill_empty: bool = True,
) -> dict[str, Any]:
    """Merge heuristic fields into an existing ICP. By default only fills empty slots."""
    parsed = parse_brief_to_icp(
        brief, campaign_id=campaign_id or existing.get("campaign_id") or "", campaign_type=campaign_type
    )
    out = dict(existing or {})
    out.setdefault("campaign_id", campaign_id or parsed.get("campaign_id"))

    def _empty(val: Any) -> bool:
        return val is None or val == "" or val == [] or val == {}

    for key in (
        "job_titles",
        "contact_locations",
        "industries",
        "excluded_industries",
        "company_size_min",
        "company_size_max",
        "include_keywords",
        "exclude_keywords",
        "excluded_name_patterns",
        "website_analysis_mode",
        "person_match",
        "brief_source",
        "geo",
    ):
        if key not in parsed:
            continue
        if only_fill_empty and not _empty(out.get(key)):
            continue
        if parsed[key] is None and key.startswith("company_size"):
            continue
        if parsed[key] == [] and key in ("job_titles", "contact_locations", "include_keywords"):
            continue
        out[key] = parsed[key]
    return out
