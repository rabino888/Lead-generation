"""
Company location vs ICP geo — used by icp_match when require_location_match.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

# Expand country / region tokens so "Palma de Mallorca" matches a Spain ICP
# even when the string does not contain the word "Spain".
_COUNTRY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "spain": (
        "spain", "españa", "espana", "es",
        "andalusia", "andalucia", "andalucía",
        "catalonia", "catalunya", "cataluña",
        "madrid", "barcelona", "valencia", "valencian",
        "málaga", "malaga", "marbella", "mijas", "fuengirola",
        "estepona", "torremolinos", "benalmádena", "benalmadena",
        "costa del sol", "mallorca", "palma", "balearic", "baleares",
        "ibiza", "sevilla", "seville", "granada", "alicante", "girona",
        "zaragoza", "bilbao", "san sebastián", "san sebastian",
        "cantabria", "galicia", "murcia", "aragon", "aragón",
    ),
    "united states": (
        "united states", "usa", "us", "america",
        "california", "new york", "texas", "florida",
    ),
    "united kingdom": (
        "united kingdom", "uk", "england", "scotland", "wales", "london",
    ),
    "france": ("france", "fr", "paris", "lyon", "marseille"),
    "germany": ("germany", "de", "deutschland", "berlin", "munich", "münchen"),
    "portugal": ("portugal", "pt", "lisbon", "lisboa", "porto"),
    "italy": ("italy", "italia", "it", "milan", "rome", "roma"),
    "netherlands": ("netherlands", "nl", "holland", "amsterdam"),
    "mexico": ("mexico", "méxico", "mx"),
    "brazil": ("brazil", "brasil", "br"),
    "canada": ("canada", "ca", "toronto", "vancouver"),
    "australia": ("australia", "au", "sydney", "melbourne"),
    "india": ("india", "in", "mumbai", "bangalore", "delhi"),
}

_TLD_COUNTRY: dict[str, str] = {
    "es": "spain",
    "uk": "united kingdom",
    "co.uk": "united kingdom",
    "fr": "france",
    "de": "germany",
    "pt": "portugal",
    "it": "italy",
    "nl": "netherlands",
    "mx": "mexico",
    "br": "brazil",
    "ca": "canada",
    "au": "australia",
    "in": "india",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _root_domain(url: str) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = f"https://{url}"
    return urlparse(url).netloc.lower().removeprefix("www.")


def _tld_country(website: str) -> str:
    domain = _root_domain(website)
    if not domain:
        return ""
    parts = domain.split(".")
    if len(parts) >= 2:
        last2 = ".".join(parts[-2:])
        if last2 in _TLD_COUNTRY:
            return _TLD_COUNTRY[last2]
        if parts[-1] in _TLD_COUNTRY:
            return _TLD_COUNTRY[parts[-1]]
    return ""


def _expand_hint(hint: str) -> set[str]:
    h = _norm(hint)
    out = {h} if h else set()
    for country, aliases in _COUNTRY_EXPANSIONS.items():
        if h == country or h in aliases:
            out.add(country)
            out.update(aliases)
    return out


def location_matches_icp(
    company_location: str,
    *,
    website: str = "",
    primary_location: Optional[str] = None,
    location_hints: Optional[list[str]] = None,
    contact_locations: Optional[list[str]] = None,
) -> bool:
    """
    True when company location (or country TLD) matches ICP geo.

    Uses company location text only — not the seed scrape's geo_segment.
    """
    hints: set[str] = set()
    for raw in [primary_location or "", *(location_hints or []), *(contact_locations or [])]:
        hints |= _expand_hint(raw)
    if not hints:
        return True  # nothing to enforce

    loc = _norm(company_location)
    if loc and any(h and h in loc for h in hints):
        return True

    # Country TLD as last resort when location string is empty/opaque
    tld_country = _tld_country(website)
    if tld_country and tld_country in hints:
        return True

    return False
