"""
Infer primary outreach language for a lead.

Priority:
  1. LinkedIn ``languages`` section (native / first listed)
  2. langdetect on about + headline + experience text
  3. Empty string when text is too short / inconclusive

Returns ISO 639-1 codes (en, es, de, …) for sheet/CSV columns.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# LinkedIn language names → ISO 639-1
_LANG_NAME_TO_CODE: dict[str, str] = {
    "english": "en",
    "spanish": "es",
    "español": "es",
    "espanol": "es",
    "castilian": "es",
    "castellano": "es",
    "french": "fr",
    "français": "fr",
    "francais": "fr",
    "german": "de",
    "deutsch": "de",
    "dutch": "nl",
    "nederlands": "nl",
    "flemish": "nl",
    "portuguese": "pt",
    "português": "pt",
    "portugues": "pt",
    "italian": "it",
    "italiano": "it",
    "swedish": "sv",
    "svenska": "sv",
    "norwegian": "no",
    "norsk": "no",
    "danish": "da",
    "dansk": "da",
    "finnish": "fi",
    "suomi": "fi",
    "polish": "pl",
    "polski": "pl",
    "russian": "ru",
    "русский": "ru",
    "arabic": "ar",
    "chinese": "zh",
    "mandarin": "zh",
    "japanese": "ja",
    "korean": "ko",
    "catalan": "ca",
    "català": "ca",
}

_NATIVE_HINT = re.compile(r"\b(native|mother\s*tongue|nativo|langue\s*maternelle)\b", re.I)
_CODE_RE = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.I)


def _normalize_lang_token(raw: str) -> str:
    text = (raw or "").strip().lower()
    if not text:
        return ""
    # "Spanish (Native)" / "English - Professional"
    text = re.split(r"[(\[/\-–—|,]", text, maxsplit=1)[0].strip()
    if _CODE_RE.match(text):
        return text[:2].lower()
    return _LANG_NAME_TO_CODE.get(text, "")


def languages_from_profile_field(languages: Any) -> list[str]:
    """Parse LinkedIn languages list → ordered ISO codes (native first when marked)."""
    if not languages:
        return []
    if isinstance(languages, str):
        parts = [p.strip() for p in re.split(r"[;,|/]", languages) if p.strip()]
        codes = [_normalize_lang_token(p) for p in parts]
        return [c for c in codes if c]

    if not isinstance(languages, list):
        return []

    native: list[str] = []
    others: list[str] = []
    for entry in languages:
        if isinstance(entry, str):
            code = _normalize_lang_token(entry)
            blob = entry
            level = ""
        elif isinstance(entry, dict):
            name = (
                entry.get("name")
                or entry.get("language")
                or entry.get("title")
                or ""
            )
            level = str(
                entry.get("proficiency")
                or entry.get("level")
                or entry.get("proficiencyLevel")
                or ""
            )
            code = _normalize_lang_token(str(name))
            blob = f"{name} {level}"
        else:
            continue
        if not code:
            continue
        if _NATIVE_HINT.search(blob) or "native" in level.lower():
            if code not in native:
                native.append(code)
        else:
            if code not in others and code not in native:
                others.append(code)
    return native + others


# langdetect often tags English job-title soup + a Spanish preposition as Catalan.
_CA_FALSE_FRIENDS = frozenset({"en", "es", "fr", "pt", "it"})
_EN_WORD = re.compile(
    r"\b(the|and|with|for|experience|luxury|advisor|hospitality|present|manager|"
    r"director|sales|real\s*estate|hostess|guest|international|clients?|resort|"
    r"beach|consultant|senior|junior|years?)\b",
    re.I,
)
_ES_WORD = re.compile(
    r"\b(los|las|una|años?|experiencia|inmobiliaria|asesor|consultor|ventas|"
    r"compraventa|viviendas?|gestión|clientes?|terapeuta|responsable|"
    r"comercial|división|obra)\b",
    re.I,
)


def _resolve_catalan_false_positive(blob: str, ranked: list) -> str:
    """Catalan is rarely the right outreach language for Marbella lead lists."""
    for alt in ranked[1:]:
        alt_code = str(alt.lang).lower()[:2]
        if alt_code in _CA_FALSE_FRIENDS and float(alt.prob) >= 0.05:
            return alt_code
    en_hits = len(_EN_WORD.findall(blob))
    es_hits = len(_ES_WORD.findall(blob))
    if en_hits >= 2 and en_hits > es_hits:
        return "en"
    if es_hits >= 2 and es_hits > en_hits:
        return "es"
    return ""


def detect_language_from_text(*parts: Optional[str]) -> str:
    """langdetect on concatenated profile prose; empty if inconclusive."""
    # Prefer the first long field (usually About) — natural prose beats title soup.
    preferred = next(
        (p.strip() for p in parts if isinstance(p, str) and len(p.strip()) >= 80),
        "",
    )
    blob = preferred or " ".join(
        p.strip() for p in parts if isinstance(p, str) and p.strip()
    )
    blob = re.sub(r"\s+", " ", blob).strip()
    # Need enough signal — short titles alone are noisy (EN↔CA/ES confusions)
    if len(blob) < 80:
        return ""
    try:
        from langdetect import DetectorFactory, detect_langs

        DetectorFactory.seed = 0
        ranked = detect_langs(blob)
    except Exception:
        return ""
    if not ranked:
        return ""
    top = ranked[0]
    code = str(top.lang).lower()[:2]
    prob = float(top.prob)
    if code == "ca":
        return _resolve_catalan_false_positive(blob, ranked)
    if prob < 0.80:
        return ""
    return code


def infer_outreach_language(
    *,
    about: str = "",
    headline: str = "",
    experience_summary: str = "",
    current_position: str = "",
    languages: Any = None,
    location: str = "",
) -> str:
    """
    Primary language for outreach copy.

    LinkedIn languages (native-first) beat text detection. Location is not used —
    Marbella/Costa del Sol leads are often English-speaking expats.
    """
    from_profile = languages_from_profile_field(languages)
    if from_profile:
        return from_profile[0]

    detected = detect_language_from_text(
        about, headline, experience_summary, current_position
    )
    return detected


def infer_outreach_language_from_row(row: dict[str, Any]) -> str:
    """Convenience for CSV rows."""
    existing = (row.get("outreach_language") or row.get("primary_language") or "").strip()
    if existing:
        code = _normalize_lang_token(existing) or existing.lower()[:2]
        if code:
            return code
    return infer_outreach_language(
        about=str(row.get("about") or ""),
        headline=str(row.get("headline") or ""),
        experience_summary=str(row.get("experience_summary") or ""),
        current_position=str(row.get("current_position") or ""),
        languages=row.get("languages") or row.get("profile_languages"),
        location=str(row.get("location") or ""),
    )


__all__ = [
    "detect_language_from_text",
    "infer_outreach_language",
    "infer_outreach_language_from_row",
    "languages_from_profile_field",
]
