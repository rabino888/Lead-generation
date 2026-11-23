"""
Humanized / brand-friendly company names for outreach columns.

Keeps the full legal/Apollo name in ``company_name`` and derives a shorter
form (e.g. "Noda AI" → "Noda", "Tribeca Living Real Estate" → "Tribeca")
for sheets and CSV exports.
"""
from __future__ import annotations

import re

# Trailing tokens commonly glued onto brand names (legal forms + descriptors).
_SUFFIX_TOKENS = frozenset(
    {
        "ai",
        "inc",
        "incorporated",
        "ltd",
        "limited",
        "llc",
        "llp",
        "plc",
        "corp",
        "corporation",
        "co",
        "company",
        "companies",
        "gmbh",
        "ag",
        "kg",
        "bv",
        "nv",
        "oy",
        "ab",
        "as",
        "sa",
        "sas",
        "sarl",
        "sl",
        "slu",
        "srl",
        "spa",
        "pty",
        "pvt",
        "private",
        "group",
        "holding",
        "holdings",
        "technologies",
        "technology",
        "tech",
        "labs",
        "lab",
        "solutions",
        "solution",
        "services",
        "service",
        "consulting",
        "consultancy",
        "consultant",
        "consultants",
        "consultora",
        "consultor",
        "partners",
        "partner",
        "ventures",
        "venture",
        "capital",
        "international",
        "global",
        "worldwide",
        "digital",
        "software",
        "systems",
        "system",
        "networks",
        "network",
        "media",
        "studio",
        "studios",
        "agency",
        # Real-estate / property-ops descriptors
        "realty",
        "realestate",
        "estate",
        "estates",
        "property",
        "properties",
        "propiedades",
        "inmobiliaria",
        "inmobiliario",
        "inmobiliarias",
        "homes",
        "home",
        "living",
        "rentals",
        "rental",
        "alquiler",
        "alquileres",
        "management",
        "managers",
        "manager",
        "gestion",
        "gestión",
        "administracion",
        "administración",
        "brokers",
        "broker",
        "agents",
        "agent",
        "advisors",
        "advisor",
        "advisory",
        "care",
        "spain",
        "españa",
        "espana",
        "uk",
        "usa",
        "us",
        "europe",
        "eu",
    }
)

# Multi-token trailing phrases (matched after single-token stripping passes).
_SUFFIX_PHRASES: tuple[tuple[str, ...], ...] = (
    ("real", "estate"),
    ("real", "estates"),
    ("property", "management"),
    ("property", "managers"),
    ("property", "manager"),
    ("property", "care"),
    ("holiday", "rentals"),
    ("holiday", "rental"),
    ("vacation", "rentals"),
    ("vacation", "rental"),
    ("estate", "agents"),
    ("estate", "agent"),
    ("estate", "agency"),
    ("real", "estate", "agency"),
    ("real", "estate", "agents"),
    ("costa", "del", "sol"),
)

# Trailing place names (only stripped when a brand token remains).
_GEO_TRAILING = frozenset(
    {
        "marbella",
        "malaga",
        "málaga",
        "estepona",
        "benahavis",
        "benahavís",
        "mijas",
        "fuengirola",
        "andalucia",
        "andalucía",
        "madrid",
        "barcelona",
        "valencia",
        "sevilla",
        "london",
        "paris",
        "dubai",
        "miami",
        "new",  # only with "york" via phrase
        "york",
    }
)

_PUNCT_EDGE = re.compile(r"^[\s\"'`“”‘’(\[]+|[\s\"'`“”‘’)\],.;:\-–—]+$")
# Page chrome scraped as a company name: careers CTAs and copyright lines.
_PAGE_PREFIX = re.compile(
    r"^(?:únete al equipo|únete a|unete al equipo|unete a|empleo en|trabaja con nosotros en|trabaja en)\s+",
    re.I,
)
_PAGE_SUFFIX = re.compile(
    r"[\s.|:–—-]+todos los derechos(?:\s+reservados)?.*$",
    re.I,
)
_COUNTRY_TAG = re.compile(r",\s*(?:ES|UK|US)\s*$", re.I)
_MULTI_SPACE = re.compile(r"\s+")
_AT_SPLIT = re.compile(r"\s+(?:@|at|en)\s+", re.I)
_DASH_WRAP = re.compile(r"\s*[-–—]\s*[^-–—]{2,40}\s*[-–—]\s*")
_PAREN = re.compile(r"\([^)]*\)|\[[^\]]*\]")


def _norm_token(token: str) -> str:
    return re.sub(r"[^a-z0-9àáâãäåæçèéêëìíîïñòóôõöøùúûüýÿ]", "", token.lower())


def _split_alias(raw: str) -> str:
    """Prefer the left side of 'Brand @ Full Legal Name' style aliases."""
    parts = _AT_SPLIT.split(raw, maxsplit=1)
    if len(parts) != 2:
        return raw
    left, right = parts[0].strip(), parts[1].strip()
    if not left:
        return right or raw
    if not right:
        return left
    # Prefer shorter left brand when both sides share a stem
    left_l, right_l = left.lower(), right.lower()
    if left_l in right_l or right_l.startswith(left_l.split()[0]):
        return left
    return left if len(left) <= len(right) else right


def humanize_company_name(name: str | None) -> str:
    """
    Short brand form of a company name for outreach.

    Handles aliases (``Homy Marbella @ Homy Marbella Real Estate`` → ``Homy``),
    industry descriptors (``Tribeca Living Real Estate`` → ``Tribeca``), and
    trailing legal/geo tokens. Returns the original trimmed name when stripping
    would leave nothing useful.
    """
    raw = (name or "").strip()
    if not raw:
        return ""

    cleaned = _MULTI_SPACE.sub(" ", raw)
    cleaned = _PAGE_PREFIX.sub("", cleaned)
    cleaned = _PAGE_SUFFIX.sub("", cleaned)
    cleaned = _COUNTRY_TAG.sub("", cleaned)
    cleaned = _split_alias(cleaned)
    cleaned = _PAREN.sub(" ", cleaned)
    cleaned = _DASH_WRAP.sub(" ", cleaned)
    cleaned = _MULTI_SPACE.sub(" ", cleaned).strip(" -–—")

    tokens = [_PUNCT_EDGE.sub("", t) for t in cleaned.split(" ")]
    tokens = [t for t in tokens if t]
    if not tokens:
        return raw

    def _pop_phrase() -> bool:
        nonlocal tokens
        norms = [_norm_token(t) for t in tokens]
        for phrase in sorted(_SUFFIX_PHRASES, key=len, reverse=True):
            n = len(phrase)
            if len(norms) > n and tuple(norms[-n:]) == phrase:
                tokens = tokens[:-n]
                return True
        return False

    def _pop_suffix_token() -> bool:
        nonlocal tokens
        if len(tokens) <= 1:
            return False
        last = _norm_token(tokens[-1])
        # Keep franchise-style "The Agency" — don't leave a bare article.
        if len(tokens) == 2 and _norm_token(tokens[0]) in {"the", "a", "an"}:
            return False
        if last in _SUFFIX_TOKENS:
            tokens.pop()
            return True
        if last in {"and", "of", "y", "e"} and len(tokens) > 2:
            tokens.pop()
            return True
        if tokens[-1] in {"&"} and len(tokens) > 2:
            tokens.pop()
            return True
        return False

    def _pop_geo() -> bool:
        nonlocal tokens
        if len(tokens) <= 1:
            return False
        last = _norm_token(tokens[-1])
        # Don't leave a useless stub like "Buy" from "Buy Marbella Property".
        if len(tokens) == 2 and len(_norm_token(tokens[0])) < 4:
            return False
        # "New York" as trailing geo
        if (
            len(tokens) > 2
            and _norm_token(tokens[-2]) == "new"
            and last == "york"
        ):
            tokens = tokens[:-2]
            return True
        if last in _GEO_TRAILING and last not in {"new"}:
            tokens.pop()
            return True
        return False

    # Repeat until stable so "Real Estate Marbella" / "Living Real Estate" peel fully.
    changed = True
    while changed and len(tokens) > 1:
        changed = _pop_phrase() or _pop_suffix_token() or _pop_geo()

    # Leading article — keep "The Agency" (franchise-style) when only that remains.
    while len(tokens) > 1 and tokens[0].lower() in {"the", "a", "an"}:
        if len(tokens) == 2 and tokens[1].lower() == "agency":
            break
        tokens.pop(0)

    result = " ".join(tokens).strip()
    return result or raw
