"""
Resolve opaque LinkedIn member URLs (/in/ACw…) to vanity /in/slug URLs.

HarvestAPI Short search cards often lack publicIdentifier. Apollo people/match
and profile scrapers work far better with vanity URLs. Primary resolver:
``treg.google.serp.organic`` (~$0.0004–0.0009). Optional LLM disambiguation
when SERP returns multiple /in/ hits — tokens billed via CostTracker.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping, Optional
from urllib.parse import unquote

from agent.utils.logger import log
from agent.utils.talent_people import normalize_linkedin_person_url

SERP_ENDPOINT = "treg.google.serp.organic"
ANYAPI_SEARCH_ENDPOINT = "anyapi.linkedin.search_profiles_thin"

_OPAQUE_PREFIXES = ("ACw", "ACo", "AEMA", "AEM")


def is_opaque_linkedin_member_url(url: str) -> bool:
    norm = normalize_linkedin_person_url(url)
    if not norm:
        return False
    slug = unquote(norm.rstrip("/").rsplit("/", 1)[-1])
    return bool(slug) and slug.startswith(_OPAQUE_PREFIXES)


def _serp_query(row: Mapping[str, Any]) -> str:
    name = (row.get("full_name") or "").strip()
    if not name:
        first = (row.get("first_name") or "").strip()
        last = (row.get("last_name") or "").strip()
        name = f"{first} {last}".strip()
    company = (row.get("company_name") or "").strip()
    location = (row.get("location") or "").strip()
    # Prefer city-ish first token of location for SERP precision
    loc_hint = ""
    if location:
        loc_hint = location.split(",")[0].strip()
    parts = [f'site:linkedin.com/in "{name}"']
    if company:
        parts.append(f'"{company}"')
    if loc_hint:
        parts.append(loc_hint)
    return " ".join(parts)


def _extract_serp_links(payload: Any) -> list[dict[str, str]]:
    """Normalize treg/anyapi SERP shapes → [{link, title, snippet}, ...]."""
    out: list[dict[str, str]] = []
    if not isinstance(payload, dict):
        return out
    # Routed: {output: {results: [...]}} or {output: {data: {results}}}
    root = payload.get("output") if isinstance(payload.get("output"), dict) else payload
    results = (
        root.get("results")
        if isinstance(root, dict)
        else None
    )
    if results is None and isinstance(root, dict):
        data = root.get("data")
        if isinstance(data, dict):
            results = data.get("results") or data.get("organic") or data.get("items")
    if results is None and isinstance(payload.get("raw"), dict):
        raw_out = (payload["raw"].get("output") or {}) if isinstance(payload["raw"], dict) else {}
        if isinstance(raw_out, dict):
            data = raw_out.get("data") if isinstance(raw_out.get("data"), dict) else raw_out
            if isinstance(data, dict):
                results = data.get("results") or data.get("items")
    if not isinstance(results, list):
        return out
    for item in results:
        if not isinstance(item, dict):
            continue
        link = (
            item.get("link")
            or item.get("url")
            or item.get("href")
            or ""
        )
        link = str(link).strip()
        if "linkedin.com/in/" not in link.lower():
            continue
        out.append(
            {
                "link": link,
                "title": str(item.get("title") or ""),
                "snippet": str(item.get("snippet") or item.get("description") or ""),
            }
        )
    return out


def _name_tokens(row: Mapping[str, Any]) -> set[str]:
    name = (row.get("full_name") or "").strip().lower()
    if not name:
        name = f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip().lower()
    return {t for t in re.split(r"[^\w]+", name) if len(t) > 1}


def _score_candidate(row: Mapping[str, Any], cand: Mapping[str, str]) -> float:
    """Cheap deterministic score; higher is better."""
    blob = f"{cand.get('title') or ''} {cand.get('snippet') or ''} {cand.get('link') or ''}".lower()
    score = 0.0
    for tok in _name_tokens(row):
        if tok in blob:
            score += 2.0
    company = (row.get("company_name") or "").strip().lower()
    if company and company in blob:
        score += 3.0
    title = (row.get("title") or row.get("headline") or "").strip().lower()
    if title:
        for tok in re.split(r"[^\w]+", title):
            if len(tok) > 3 and tok in blob:
                score += 0.5
    loc = (row.get("location") or "").split(",")[0].strip().lower()
    if loc and loc in blob:
        score += 1.0
    # Prefer vanity over opaque in SERP (rare)
    link = cand.get("link") or ""
    if not is_opaque_linkedin_member_url(link):
        score += 1.0
    return score


def _pick_with_llm(
    row: Mapping[str, Any],
    candidates: list[dict[str, str]],
) -> Optional[str]:
    """
    Disambiguate SERP hits with a tiny Gemini/OpenAI call. Tokens → CostTracker.
    Returns normalized vanity URL or None.
    """
    if not candidates:
        return None

    # Drop candidates that don't share any name token with the person
    tokens = _name_tokens(row)
    filtered: list[dict[str, str]] = []
    for cand in candidates:
        blob = f"{cand.get('title') or ''} {cand.get('snippet') or ''} {cand.get('link') or ''}".lower()
        # Require at least one distinctive name token (len>2) in title/snippet/slug
        if tokens and any(t in blob for t in tokens if len(t) > 2):
            filtered.append(cand)
    if not filtered:
        return None
    candidates = filtered

    if len(candidates) == 1:
        return normalize_linkedin_person_url(candidates[0]["link"])

    # Skip LLM unless explicitly enabled (keeps smoke cheap by default)
    if (os.environ.get("TALENT_VANITY_LLM_PICK") or "").strip() not in ("1", "true", "yes"):
        best = max(candidates, key=lambda c: _score_candidate(row, c))
        if _score_candidate(row, best) < 4.0:
            # Weak match — refuse rather than attach a wrong vanity URL
            return None
        return normalize_linkedin_person_url(best["link"])

    try:
        from agent.utils.llm_client import complete_json  # type: ignore
    except Exception:
        complete_json = None  # type: ignore

    if complete_json is None:
        # Fallback: try website providers helper if present
        try:
            from agent.integrations import llm as llm_mod  # type: ignore

            complete_json = getattr(llm_mod, "complete_json", None)
        except Exception:
            complete_json = None

    prompt = {
        "person": {
            "full_name": row.get("full_name"),
            "title": row.get("title") or row.get("headline"),
            "company_name": row.get("company_name"),
            "location": row.get("location"),
        },
        "candidates": candidates[:5],
        "task": "Pick the single LinkedIn /in/ URL that is this person. Reply JSON {\"url\": \"...\"} or {\"url\": null}.",
    }
    if not callable(complete_json):
        best = max(candidates, key=lambda c: _score_candidate(row, c))
        return normalize_linkedin_person_url(best["link"])

    try:
        result = complete_json(
            system="You match people to LinkedIn profile URLs. Return only JSON.",
            user=json.dumps(prompt, ensure_ascii=False),
            max_tokens=80,
        )
        # Prefer token accounting when helper returns usage
        usage = getattr(result, "usage_tokens", None) or (
            result.get("_tokens") if isinstance(result, dict) else None
        )
        if usage:
            try:
                from agent.utils.cost_tracker import get_cost_tracker

                tracker = get_cost_tracker()
                if tracker:
                    tracker.add_usage(llm_tokens=int(usage))
            except Exception:
                pass
        data = result if isinstance(result, dict) else {}
        url = normalize_linkedin_person_url(str(data.get("url") or ""))
        if url and not is_opaque_linkedin_member_url(url):
            return url
    except Exception as exc:
        log.debug("vanity LLM pick failed: %s", exc)

    best = max(candidates, key=lambda c: _score_candidate(row, c))
    return normalize_linkedin_person_url(best["link"])


def resolve_vanity_via_serp(
    row: Mapping[str, Any],
    *,
    endpoint_id: str = SERP_ENDPOINT,
) -> tuple[str, float]:
    """
    Call treg SERP; return (vanity_url_or_empty, usd_charged).
    Cost is also recorded on CostTracker via treg.call_catalog.
    """
    from agent.integrations.treg import call_catalog, treg_configured

    if not treg_configured():
        return "", 0.0
    q = _serp_query(row)
    if '""' in q or q.strip() == "site:linkedin.com/in":
        return "", 0.0
    try:
        data, usd = call_catalog(
            endpoint_id,
            method="POST",
            body={"q": q},
            timeout=60.0,
        )
    except Exception as exc:
        log.warning("vanity SERP failed for %s: %s", row.get("full_name"), exc)
        return "", 0.0

    cands = _extract_serp_links(data)
    if not cands:
        return "", float(usd or 0)
    picked = _pick_with_llm(row, cands)
    if picked and not is_opaque_linkedin_member_url(picked):
        log.info(
            "vanity resolve: %s → %s (serp $%.4f)",
            row.get("full_name"),
            picked,
            usd,
        )
        return picked, float(usd or 0)
    return "", float(usd or 0)


def resolve_vanity_via_people_search(
    row: Mapping[str, Any],
) -> tuple[str, float]:
    """
    Fallback: AnyAPI LinkedIn thin search (returns vanity URLs, ~$0.036).
    Use only when SERP misses.
    """
    from agent.integrations.treg import call_catalog, treg_configured

    if not treg_configured():
        return "", 0.0
    name = (row.get("full_name") or "").strip()
    if not name:
        return "", 0.0
    company = (row.get("company_name") or "").strip()
    query = f"{name} {company}".strip()
    try:
        data, usd = call_catalog(
            ANYAPI_SEARCH_ENDPOINT,
            method="POST",
            body={"query": query},
            timeout=90.0,
        )
    except Exception as exc:
        log.warning("vanity people-search failed for %s: %s", name, exc)
        return "", 0.0

    items: list[Any] = []
    if isinstance(data, dict):
        out = data.get("output") if isinstance(data.get("output"), dict) else data
        if isinstance(out, dict):
            nested = out.get("data") if isinstance(out.get("data"), dict) else out
            if isinstance(nested, dict):
                items = list(nested.get("items") or [])
    cands: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("linkedinUrl") or "").strip()
        if "linkedin.com/in/" not in url.lower():
            handle = str(item.get("handle") or "").strip()
            if handle and not handle.startswith("http"):
                url = f"https://www.linkedin.com/in/{handle}"
        if "linkedin.com/in/" not in url.lower():
            continue
        cands.append(
            {
                "link": url,
                "title": str(item.get("name") or item.get("headline") or ""),
                "snippet": str(item.get("headline") or item.get("location") or ""),
            }
        )
    if not cands:
        return "", float(usd or 0)
    picked = _pick_with_llm(row, cands)
    if picked and not is_opaque_linkedin_member_url(picked):
        log.info(
            "vanity resolve (people search): %s → %s ($%.4f)",
            name,
            picked,
            usd,
        )
        return picked, float(usd or 0)
    return "", float(usd or 0)


def resolve_row_linkedin_vanity(
    row: Mapping[str, Any],
    *,
    allow_people_search_fallback: bool = False,
) -> dict[str, str]:
    """
    Return a shallow copy of ``row`` with ``linkedin_url`` upgraded when opaque.

    Non-opaque URLs are left unchanged (no paid call).
    """
    out = {str(k): str(v if v is not None else "") for k, v in row.items()}
    current = normalize_linkedin_person_url(out.get("linkedin_url") or "")
    if current:
        out["linkedin_url"] = current
    if current and not is_opaque_linkedin_member_url(current):
        out["vanity_resolve_status"] = "already_vanity"
        return out
    if not (out.get("full_name") or out.get("first_name") or out.get("last_name")):
        out["vanity_resolve_status"] = "skipped_no_name"
        return out

    vanity, _usd = resolve_vanity_via_serp(out)
    if not vanity and allow_people_search_fallback:
        vanity, _usd = resolve_vanity_via_people_search(out)
    if vanity:
        out["linkedin_url_opaque"] = current
        out["linkedin_url"] = vanity
        out["source_url"] = out.get("source_url") or current
        out["vanity_resolve_status"] = "resolved"
    else:
        out["vanity_resolve_status"] = "unresolved"
    return out


def resolve_opaque_rows(
    rows: list[Mapping[str, Any]],
    *,
    limit: int | None = None,
    allow_people_search_fallback: bool = False,
) -> list[dict[str, str]]:
    """Resolve vanity for each row (only opaque URLs trigger paid calls)."""
    sliced = list(rows[:limit] if limit is not None else rows)
    return [
        resolve_row_linkedin_vanity(
            r, allow_people_search_fallback=allow_people_search_fallback
        )
        for r in sliced
    ]


__all__ = [
    "ANYAPI_SEARCH_ENDPOINT",
    "SERP_ENDPOINT",
    "is_opaque_linkedin_member_url",
    "resolve_opaque_rows",
    "resolve_row_linkedin_vanity",
    "resolve_vanity_via_people_search",
    "resolve_vanity_via_serp",
]
