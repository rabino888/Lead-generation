"""
Deterministic post-enrichment keyword-overlap scorer (no LLM).

Scores how well scraped website + LinkedIn text overlaps sender.services
and sender.target_pain_points. Full LLM pain-point qualification is future work.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping

from agent.models import Lead, SenderProfile

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-']{1,}", re.I)
_STOP = frozenset(
    "a an the and or of to for in on with by from as is are was were be been "
    "this that these those it its our your their we you they at into over "
    "about than then so if not no yes per via across".split()
)


@dataclass
class KeywordScoreResult:
    lead_score: int
    matched_terms: list[str] = field(default_factory=list)
    score_evidence: list[str] = field(default_factory=list)
    score_reasons: list[str] = field(default_factory=list)
    score_method: str = "keyword_overlap"


def normalize_text(text: str) -> str:
    """Lowercase, strip accents, collapse non-alnum to spaces."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"[^a-z0-9]+", " ", ascii_text)
    return " ".join(ascii_text.split())


def tokenize(text: str, *, min_len: int = 2) -> list[str]:
    tokens = _TOKEN_RE.findall(normalize_text(text))
    return [t for t in tokens if t not in _STOP and len(t) >= min_len]


def extract_sender_terms(
    sender: SenderProfile,
    *,
    include_core_offer: bool = True,
) -> dict[str, list[str]]:
    return {
        "services": [p.strip() for p in (sender.services or []) if p and p.strip()],
        "pain_points": [
            p.strip() for p in (sender.target_pain_points or []) if p and p.strip()
        ],
        "core_offer": (
            [sender.core_offer.strip()]
            if include_core_offer and sender.core_offer and sender.core_offer.strip()
            else []
        ),
    }


def extract_hiring_corpus(lead: Lead | Mapping[str, Any]) -> str:
    """Open roles + hiring signals for pitch-intelligence scoring."""
    parts: list[str] = []
    if isinstance(lead, Mapping):
        for key in (
            "website_hiring_signals",
            "website_open_roles",
            "company_open_jobs_summary",
            "company_is_hiring",
            "website_is_hiring",
        ):
            val = lead.get(key)
            if val:
                parts.append(str(val))
        return "\n".join(p for p in parts if p)

    wa = lead.website_analysis
    if wa:
        parts.extend(wa.website_hiring_signals or [])
        parts.extend(wa.website_open_roles or [])
        if wa.website_is_hiring:
            parts.append(str(wa.website_is_hiring))
    if lead.company_open_jobs_summary:
        parts.append(lead.company_open_jobs_summary)
    if lead.company_is_hiring:
        parts.append(str(lead.company_is_hiring))
    return "\n".join(p for p in parts if p)


def _lead_is_actively_hiring(lead: Lead | Mapping[str, Any]) -> bool:
    if isinstance(lead, Mapping):
        for key in ("website_is_hiring", "company_is_hiring"):
            if str(lead.get(key) or "").lower() == "yes":
                return True
        roles = lead.get("website_open_roles") or ""
        if roles and str(roles).strip():
            return True
        return bool((lead.get("company_open_jobs_summary") or "").strip())

    wa = lead.website_analysis
    if wa and str(wa.website_is_hiring or "").lower() == "yes":
        return True
    if wa and wa.website_open_roles:
        return True
    if str(lead.company_is_hiring or "").lower() == "yes":
        return True
    return bool(lead.company_open_jobs_summary)


def extract_lead_corpus(lead: Lead | Mapping[str, Any]) -> str:
    parts: list[str] = []
    if isinstance(lead, Mapping):
        for key in (
            "company_description",
            "website_summary",
            "linkedin_about",
            "linkedin_headline",
            "linkedin_post_summary",
            "company_linkedin_description",
            "website_hiring_signals",
            "website_open_roles",
            "company_open_jobs_summary",
        ):
            if lead.get(key):
                parts.append(str(lead[key]))
        for post in lead.get("linkedin_posts") or []:
            if isinstance(post, dict):
                parts.append(str(post.get("text") or post.get("commentary") or ""))
            else:
                parts.append(str(post))
        return "\n".join(p for p in parts if p)

    if lead.website_analysis:
        wa = lead.website_analysis
        if wa.website_summary:
            parts.append(wa.website_summary)
        parts.extend(wa.services_offered or [])
        parts.extend(wa.website_hiring_signals or [])
        parts.extend(wa.website_open_roles or [])
    if lead.company_linkedin_description:
        parts.append(lead.company_linkedin_description)
    if lead.company_open_jobs_summary:
        parts.append(lead.company_open_jobs_summary)
    dm = lead.decision_maker
    if dm:
        if dm.linkedin_about:
            parts.append(dm.linkedin_about)
        if dm.linkedin_headline:
            parts.append(dm.linkedin_headline)
        if dm.linkedin_post_summary:
            parts.append(dm.linkedin_post_summary)
        for post in dm.linkedin_posts or []:
            if isinstance(post, dict):
                parts.append(str(post.get("text") or post.get("commentary") or ""))
            else:
                parts.append(str(post))
        parts.extend(dm.linkedin_interests or [])
    if lead.industry:
        parts.append(lead.industry)
    return "\n".join(p for p in parts if p)


def _snippet_around(corpus: str, phrase: str, width: int = 90) -> str | None:
    lower = normalize_text(corpus)
    key = normalize_text(phrase)
    idx = lower.find(key)
    if idx < 0:
        tokens = [t for t in tokenize(phrase, min_len=4)]
        for tok in tokens:
            idx = lower.find(tok)
            if idx >= 0:
                key = tok
                break
        else:
            return None
    # Map approx into original corpus via normalized index ratio
    raw = corpus.replace("\n", " ")
    start = max(0, min(len(raw), int(idx * len(raw) / max(len(lower), 1)) - width // 2))
    end = min(len(raw), start + width + len(key))
    snippet = raw[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(raw):
        snippet = snippet + "…"
    return snippet


def _phrase_weight(phrase: str) -> list[str]:
    return [t for t in tokenize(phrase, min_len=4)]


def _score_phrases(
    corpus: str,
    phrases: list[str],
    *,
    label: str,
) -> tuple[float, list[str], list[str], list[str]]:
    """Return (category_score_0_100, matched, evidence, reasons)."""
    if not phrases:
        return 0.0, [], [], []

    corpus_n = normalize_text(corpus)
    corpus_tokens = set(tokenize(corpus, min_len=3))
    total_w = 0.0
    earned_w = 0.0
    matched: list[str] = []
    evidence: list[str] = []
    reasons: list[str] = []

    for phrase in phrases:
        tokens = _phrase_weight(phrase)
        weight = float(len(tokens) or 1)
        total_w += weight
        phrase_n = normalize_text(phrase)
        if phrase_n and phrase_n in corpus_n:
            earned_w += weight
            matched.append(phrase)
            snip = _snippet_around(corpus, phrase)
            if snip:
                evidence.append(snip)
            reasons.append(f"{label} phrase hit: {phrase[:60]}")
            continue
        if not tokens:
            continue
        hits = sum(1 for t in tokens if t in corpus_tokens)
        if hits * 2 >= len(tokens):
            frac = hits / len(tokens)
            earned_w += weight * frac
            matched.append(phrase)
            snip = _snippet_around(corpus, tokens[0])
            if snip:
                evidence.append(snip)
            reasons.append(f"{label} token overlap {hits}/{len(tokens)}: {phrase[:40]}")

    score = 100.0 * earned_w / total_w if total_w else 0.0
    return score, matched, evidence, reasons


def compute_keyword_score(
    lead: Lead | Mapping[str, Any],
    sender: SenderProfile,
    *,
    include_core_offer: bool = True,
    hiring_first: bool = False,
) -> KeywordScoreResult:
    corpus = extract_lead_corpus(lead)
    hiring_corpus = extract_hiring_corpus(lead) if hiring_first else ""
    if hiring_first and hiring_corpus.strip():
        corpus = f"{hiring_corpus}\n{hiring_corpus}\n{corpus}"

    if not corpus.strip():
        return KeywordScoreResult(
            lead_score=0,
            score_reasons=["No website or LinkedIn text available for keyword overlap"],
        )

    terms = extract_sender_terms(sender, include_core_offer=include_core_offer)
    svc_score, svc_m, svc_e, svc_r = _score_phrases(
        corpus, terms["services"], label="service"
    )
    pain_score, pain_m, pain_e, pain_r = _score_phrases(
        corpus, terms["pain_points"], label="pain-point"
    )
    offer_score, offer_m, offer_e, offer_r = _score_phrases(
        corpus, terms["core_offer"], label="core-offer"
    )

    weights: list[tuple[float, float]] = []
    if terms["services"]:
        weights.append((0.45, svc_score))
    if terms["pain_points"]:
        weights.append((0.45, pain_score))
    if terms["core_offer"]:
        weights.append((0.10, offer_score))

    if hiring_first and hiring_corpus.strip():
        pitch_phrases = terms["services"] + terms["pain_points"]
        align_score, align_m, align_e, align_r = _score_phrases(
            hiring_corpus, pitch_phrases, label="hiring-align"
        )
        weights.append((0.30, align_score))
        svc_m = svc_m + align_m
        svc_e = svc_e + align_e
        svc_r = svc_r + align_r

    if not weights:
        return KeywordScoreResult(
            lead_score=0,
            score_reasons=["no_sender_phrases"],
        )

    w_sum = sum(w for w, _ in weights)
    blended = sum((w / w_sum) * s for w, s in weights)
    score = int(round(max(0.0, min(100.0, blended))))

    if hiring_first and _lead_is_actively_hiring(lead):
        score = min(100, score + 8)
        svc_r = svc_r + ["hiring_first active-hiring bonus +8"]

    matched = svc_m + pain_m + offer_m
    # de-dupe preserve order
    seen: set[str] = set()
    matched_unique = []
    for m in matched:
        if m not in seen:
            seen.add(m)
            matched_unique.append(m)

    evidence = (svc_e + pain_e + offer_e)[:8]
    reasons = svc_r + pain_r + offer_r
    if not reasons:
        reasons = [f"keyword_overlap score={score} (weak token overlap)"]

    return KeywordScoreResult(
        lead_score=score,
        matched_terms=matched_unique,
        score_evidence=evidence,
        score_reasons=reasons[:12],
    )


def score_lead(
    lead: Lead,
    sender: SenderProfile,
    *,
    include_core_offer: bool = True,
    hiring_first: bool = False,
) -> Lead:
    """Score lead in-place and return it. Method: keyword_overlap."""
    result = compute_keyword_score(
        lead,
        sender,
        include_core_offer=include_core_offer,
        hiring_first=hiring_first,
    )
    lead.lead_score = result.lead_score
    lead.icp_match_score = result.lead_score
    lead.score_method = result.score_method
    lead.matched_terms = list(result.matched_terms)
    lead.score_evidence = list(result.score_evidence)
    lead.score_reasons = list(result.score_reasons)
    # Leave pain_points for future LLM qualification / human QA
    note = (
        f"keyword_overlap score={result.lead_score}; "
        f"matched={len(result.matched_terms)}"
    )
    if result.score_reasons:
        note += "; " + "; ".join(result.score_reasons[:4])
    if lead.qualification_notes:
        lead.qualification_notes = f"{lead.qualification_notes} | {note}"
    else:
        lead.qualification_notes = note
    return lead


def score_leads(
    leads: list[Lead],
    sender: SenderProfile,
    *,
    hiring_first: bool = False,
) -> list[Lead]:
    return [
        score_lead(lead, sender, hiring_first=hiring_first) for lead in leads
    ]
