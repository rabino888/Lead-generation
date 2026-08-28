"""
ICP job title matching — used to filter Apollo contact candidates after search.
"""
from __future__ import annotations

import re

# Common executive abbreviations ↔ long forms
_TITLE_ALIASES: dict[str, list[str]] = {
    "ceo": ["chief executive officer", "ceo", "chief executive"],
    "cfo": ["chief financial officer", "cfo"],
    "coo": ["chief operating officer", "coo"],
    "cto": ["chief technology officer", "cto"],
    "cmo": ["chief marketing officer", "cmo"],
    "chro": ["chief human resources officer", "chro", "chief people officer"],
    "md": ["managing director"],
    "vp": ["vice president", "vp"],
}

# Words too generic to match on their own (would false-positive "Product Manager" etc.)
_STOPWORDS = frozenset({
    "of", "the", "and", "for", "a", "an", "at", "in", "to", "&",
    "director",  # alone matches inside "Managing Director" via full phrase; block solo "director" token rule below
    "manager",   # block solo — only match when ICP explicitly contains "X Manager"
    "head",
    "chief",
    "senior",
    "executive",
    "global",
    "regional",
    "group",
})


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def primary_title(raw: str) -> str:
    """
    Extract the primary job title from Apollo/LinkedIn fields.
    Long headline blobs (e.g. 'AI Product Manager ... 2x Founder (acquired) ...')
    must not be treated as the person's role for matching or seniority ranking.
    """
    if not raw:
        return ""
    t = raw.strip()
    if " | " in t:
        t = t.split(" | ", 1)[0].strip()
    if "(" in t and t.index("(") < 80:
        before = t[: t.index("(")].strip()
        if before:
            t = before
    if ". " in t and len(t) > 40:
        t = t.split(". ", 1)[0].strip()
    if len(t) > 80:
        t = t[:80].strip()
    return t


def _expand_icp_title(icp_title: str) -> set[str]:
    """Return normalized phrases that should match this ICP title."""
    base = _normalize(icp_title)
    phrases = {base}
    for abbrev, forms in _TITLE_ALIASES.items():
        if abbrev in base.split() or abbrev == base:
            phrases.update(_normalize(f) for f in forms)
        for form in forms:
            if form in base:
                phrases.add(base)
                phrases.add(_normalize(form))
    return phrases


def _significant_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", _normalize(text))
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def _finance_role_match(cand: str, icp_titles: list[str]) -> bool:
    """Match finance leadership variants (e.g. 'Director, Strategic Finance')."""
    if not any("finance" in _normalize(t) for t in icp_titles):
        return False
    c = _normalize(cand)
    if "finance" not in c:
        return False
    return any(
        k in c
        for k in (
            "director",
            "controller",
            "cfo",
            "head of",
            "vp ",
            "vice president",
            "chief financial",
        )
    )


def title_matches_icp(candidate_title: str, icp_titles: list[str]) -> bool:
    """
    Return True if candidate_title matches any ICP job title.

    Matching rules (first hit wins):
    1. Normalized exact equality
    2. ICP phrase is a whole-word substring of candidate (or vice versa for short ICP)
    3. All significant tokens from ICP title appear in candidate (for multi-word titles)
    """
    if not candidate_title or not icp_titles:
        return False

    cand = _normalize(primary_title(candidate_title))
    if _finance_role_match(cand, icp_titles):
        return True

    for icp_title in icp_titles:
        if not icp_title:
            continue

        icp_norm = _normalize(icp_title)
        if not icp_norm:
            continue

        # Exact / alias expansion
        for phrase in _expand_icp_title(icp_title):
            if cand == phrase:
                return True
            # Whole-phrase containment (e.g. "cfo" in "group cfo", "head of finance" in title)
            if len(phrase) >= 4 and re.search(rf"\b{re.escape(phrase)}\b", cand):
                return True
            if len(cand) >= 4 and re.search(rf"\b{re.escape(cand)}\b", phrase):
                return True

        # Multi-word ICP: all significant tokens must appear in candidate
        icp_tokens = _significant_tokens(icp_title)
        if len(icp_tokens) >= 2 and all(re.search(rf"\b{re.escape(tok)}\b", cand) for tok in icp_tokens):
            return True

        # Single distinctive ICP token (e.g. "founder", "cfo" as sole title)
        if len(icp_tokens) == 1:
            tok = icp_tokens[0]
            if tok in ("founder", "co-founder", "cofounder", "ceo", "cfo", "coo", "cto", "cmo", "chro"):
                if re.search(rf"\b{re.escape(tok)}\b", cand):
                    return True

        # Explicit "X Manager" in ICP — allow manager-level only when ICP says so
        if "manager" in icp_norm and re.search(rf"\b{re.escape(icp_norm)}\b", cand):
            return True

    return False


def seniorities_from_job_titles(job_titles: list[str]) -> list[str]:
    """
    Derive Apollo person_seniorities[] from ICP job_titles.
    Only includes seniority buckets implied by the configured titles.
    """
    combined = _normalize(" ".join(job_titles))
    buckets: list[str] = []

    def _add(name: str) -> None:
        if name not in buckets:
            buckets.append(name)

    if any(t in combined for t in ("owner", "proprietor")):
        _add("owner")
    if "founder" in combined or "co-founder" in combined or "cofounder" in combined:
        _add("founder")
    if any(t in combined for t in ("ceo", "cfo", "coo", "cto", "cmo", "chro", "chief ", " cpo")):
        _add("c_suite")
    if "partner" in combined:
        _add("partner")
    if "vp " in combined or "vice president" in combined:
        _add("vp")
    if "head of" in combined or combined.startswith("head "):
        _add("head")
    if "director" in combined:
        _add("director")
    # Only include manager seniority when ICP explicitly targets manager-level roles
    if re.search(r"\b\w+\s+manager\b", combined) or combined.endswith(" manager"):
        _add("manager")

    # Safe default for typical executive ICPs
    if not buckets:
        buckets = ["owner", "founder", "c_suite", "partner", "vp", "head", "director"]

    return buckets


def title_priority(candidate_title: str, icp_titles: list[str]) -> int:
    """
    Lower = better fit for reveal priority among ICP-matched candidates.
    Unmatched titles return 100.
    """
    if not title_matches_icp(candidate_title, icp_titles):
        return 100
    p = _normalize(primary_title(candidate_title))
    if any(x in p for x in ("ceo", "chief executive officer", "chief executive")):
        return 0
    if any(x in p for x in ("founder", "co-founder", "cofounder")):
        return 1
    if any(x in p for x in ("cfo", "chief financial officer")):
        return 2
    if any(x in p for x in ("head of finance", "finance director", "director of finance", "vp finance")):
        return 3
    if "strategic finance" in p or (", finance" in p and "director" in p):
        return 4
    if any(x in p for x in ("coo", "chief operating officer")):
        return 4
    if "managing director" in p:
        return 5
    if "financial controller" in p:
        return 6
    return 10
