"""
Talent optional module: enrich the campaign-relevant current employer.

When a person lists several concurrent roles, pick the company that best
matches the campaign ICP (include_keywords / job_titles), then fetch LinkedIn
company profile details (description, industry, size). Soft-fails when no
employer or scrape fails.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from agent.utils.logger import log
from agent.utils.talent_people import (
    STAGE_T05_APOLLO_CONTACTABLE,
    read_people_csv,
    talent_stage_path,
)
from agent.utils.talent_profile import _write_profile_csv

CompanyEnricher = Callable[..., dict]

_PRESENT_RE = re.compile(
    r"\b(present|current|actualidad|hoy|heute|aujourd['’]?hui)\b",
    re.I,
)
_PAST_END_RE = re.compile(
    r"\b(20\d{2}|19\d{2})\s*[-–—]\s*(20\d{2}|19\d{2})\b",
)
_AT_SPLIT = re.compile(r"\s+(?:@|at|en|bei|chez)\s+", re.I)


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm_company(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _load_icp_dict(campaign_dir: Path) -> dict[str, Any]:
    path = Path(campaign_dir) / "icp.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _icp_match_terms(icp: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Positive + negative keywords for employer scoring."""
    include: list[str] = []
    for key in ("include_keywords", "keywords"):
        raw = icp.get(key) or []
        if isinstance(raw, list):
            include.extend(str(x).strip() for x in raw if str(x).strip())
    titles = icp.get("job_titles") or []
    if isinstance(titles, list):
        include.extend(str(x).strip() for x in titles if str(x).strip())
    industries = icp.get("industries") or []
    if isinstance(industries, list):
        include.extend(str(x).strip() for x in industries if str(x).strip())

    exclude: list[str] = []
    for key in ("exclude_keywords", "excluded_keywords"):
        raw = icp.get(key) or []
        if isinstance(raw, list):
            exclude.extend(str(x).strip() for x in raw if str(x).strip())
    pm = icp.get("person_match") if isinstance(icp.get("person_match"), dict) else {}
    for key in ("exclude_keywords", "exclude_titles"):
        raw = pm.get(key) or []
        if isinstance(raw, list):
            exclude.extend(str(x).strip() for x in raw if str(x).strip())

    # Dedupe case-insensitively, keep first casing
    def _uniq(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            low = item.lower()
            if low in seen:
                continue
            seen.add(low)
            out.append(item)
        return out

    return _uniq(include), _uniq(exclude)


def _score_blob(blob: str, include: list[str], exclude: list[str]) -> int:
    text = (blob or "").lower()
    if not text:
        return 0
    score = 0
    for kw in include:
        low = kw.lower()
        if low and low in text:
            score += 2 if len(low) >= 8 else 1
    for kw in exclude:
        low = kw.lower()
        if low and low in text:
            score -= 3
    return score


def _company_from_position(position: str) -> tuple[str, str]:
    """Return (title, company) from 'Title @ Company' / 'Title at Company'."""
    raw = _as_str(position)
    if not raw:
        return "", ""
    parts = _AT_SPLIT.split(raw, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip()[:200], parts[1].strip()[:200]
    return raw[:200], ""


def _parse_experience_segment(segment: str) -> tuple[str, str, str]:
    """
    Parse 'Title · Company · Duration' (or fewer parts).

    Returns (title, company, duration).
    """
    parts = [p.strip() for p in (segment or "").split("·") if p.strip()]
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        title, company = _company_from_position(parts[0])
        return title, company, ""
    if len(parts) == 2:
        # Title · Company  OR  Company · Duration
        if _PRESENT_RE.search(parts[1]) or _PAST_END_RE.search(parts[1]):
            return "", parts[0], parts[1]
        return parts[0], parts[1], ""
    # Title · Company · Duration (possibly more)
    title, company, duration = parts[0], parts[1], parts[-1]
    return title, company, duration


def _looks_current(duration: str, *, index: int) -> bool:
    dur = duration or ""
    if _PRESENT_RE.search(dur):
        return True
    if _PAST_END_RE.search(dur) and not _PRESENT_RE.search(dur):
        return False
    # LinkedIn lists current roles first; treat first two undated as current
    return index < 2


def employer_candidates(row: Mapping[str, Any]) -> list[dict[str, str]]:
    """
    Distinct current-employer candidates from seed + profile text.

    Each item: {name, title, source, linkedin_url}.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(name: str, title: str, source: str, linkedin_url: str = "") -> None:
        company = _as_str(name)
        if not company:
            return
        key = _norm_company(company)
        if key in seen or len(key) < 2:
            return
        seen.add(key)
        out.append(
            {
                "name": company[:200],
                "title": _as_str(title)[:200],
                "source": source,
                "linkedin_url": _as_str(linkedin_url),
            }
        )

    seed_co = _as_str(row.get("company_name"))
    seed_li = _as_str(row.get("company_linkedin_url"))
    seed_title = _as_str(row.get("title") or row.get("headline") or "")
    _add(seed_co, seed_title, "seed", seed_li)

    pos_title, pos_co = _company_from_position(_as_str(row.get("current_position")))
    _add(pos_co, pos_title or seed_title, "current_position")

    summary = _as_str(row.get("experience_summary"))
    if summary:
        for idx, segment in enumerate(summary.split(";")):
            title, company, duration = _parse_experience_segment(segment)
            if not company:
                continue
            if not _looks_current(duration, index=idx):
                continue
            _add(company, title, f"experience[{idx}]")

    return out


def select_campaign_employer(
    row: Mapping[str, Any],
    icp: Mapping[str, Any] | None = None,
) -> Optional[dict[str, Any]]:
    """
    Pick the campaign-relevant current employer.

    Prefers the candidate whose role+company text best matches ICP keywords.
    Falls back to seed company, then current_position, then first experience.
    """
    candidates = employer_candidates(row)
    if not candidates:
        return None

    include, exclude = _icp_match_terms(icp or {})
    scored: list[tuple[int, int, dict[str, str]]] = []
    for cand in candidates:
        # Seed company_name is often stale vs concurrent roles — score the
        # company string alone. Role text only counts when tied to that employer
        # (current_position / experience segment).
        if cand.get("source") == "seed":
            blob = cand.get("name") or ""
        else:
            blob = f"{cand.get('title') or ''} {cand.get('name') or ''}"
        company_hits = _score_blob(cand.get("name") or "", include, exclude)
        role_hits = _score_blob(cand.get("title") or "", include, exclude)
        score = _score_blob(blob, include, exclude)
        # Prefer employers where the *company* also looks on-ICP when roles tie
        score = score * 10 + company_hits * 3 + role_hits
        source_boost = 1 if cand.get("source") == "seed" else 0
        scored.append((score, source_boost, cand))

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    best_score, _, best = scored[0]
    reason = "icp_keyword_match" if best_score > 0 else "fallback_primary"
    if len(candidates) == 1:
        reason = "single_employer"
    return {
        "name": best["name"],
        "title": best.get("title") or "",
        "linkedin_url": best.get("linkedin_url") or "",
        "source": best.get("source") or "",
        "score": best_score,
        "reason": reason,
        "candidate_count": len(candidates),
    }


def enrich_contactable_employer_company(
    campaign_dir: Path,
    *,
    enrich_company: CompanyEnricher | None = None,
    only_missing: bool = True,
    fetch_open_jobs: bool = False,
) -> tuple[int, int]:
    """
    Enrich campaign-relevant employer on T05 contactables.

    Writes company_name / company_linkedin_url (selected) plus
    company_linkedin_description, company_industry, company_size.
    Returns (enriched_count, skipped_count).
    """
    from agent.integrations.apify import enrich_company_linkedin

    camp = Path(campaign_dir)
    t05_path = talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE)
    if not t05_path.is_file():
        return 0, 0

    rows = read_people_csv(t05_path)
    if not rows:
        return 0, 0

    icp = _load_icp_dict(camp)
    fetcher = enrich_company or enrich_company_linkedin
    enriched_n = 0
    skipped_n = 0
    updated: list[dict[str, str]] = []
    # Cache by normalized company name to avoid duplicate paid scrapes
    cache: dict[str, dict[str, Any]] = {}

    for row in rows:
        out = dict(row)
        if only_missing and (out.get("company_linkedin_description") or "").strip():
            updated.append(out)
            continue

        picked = select_campaign_employer(out, icp)
        if not picked or not picked.get("name"):
            skipped_n += 1
            updated.append(out)
            continue

        name = str(picked["name"])
        li = str(picked.get("linkedin_url") or out.get("company_linkedin_url") or "")
        cache_key = _norm_company(name) or li.lower()
        log.info(
            "Employer company enrich: %s → %s (%s, score=%s)",
            out.get("full_name") or out.get("person_id"),
            name,
            picked.get("reason"),
            picked.get("score"),
        )

        if cache_key not in cache:
            try:
                cache[cache_key] = fetcher(
                    name,
                    website=None,
                    company_linkedin_url=li or None,
                    fetch_open_jobs=fetch_open_jobs,
                ) or {}
            except Exception as exc:
                log.warning("Employer company enrich failed for %s: %s", name, exc)
                cache[cache_key] = {}

        data = cache[cache_key]
        out["company_name"] = name
        if data.get("linkedin_url"):
            out["company_linkedin_url"] = str(data["linkedin_url"])
        elif li:
            out["company_linkedin_url"] = li
        if data.get("description"):
            out["company_linkedin_description"] = str(data["description"])[:2000]
        if data.get("industry"):
            out["company_industry"] = str(data["industry"])[:200]
        if data.get("employee_count") is not None and str(data.get("employee_count")).strip():
            out["company_size"] = str(data["employee_count"])
        if data.get("website"):
            out["website"] = str(data["website"]).strip()
        out["employer_match_reason"] = str(picked.get("reason") or "")
        out["employer_match_score"] = str(picked.get("score") or 0)

        if (out.get("company_linkedin_description") or out.get("company_industry")
                or out.get("company_linkedin_url")):
            enriched_n += 1
        else:
            # Still recorded the selected name even if scrape empty
            enriched_n += 1
        updated.append(out)

    _write_profile_csv(t05_path, updated)
    log.info(
        "Employer company enrich: enriched=%d skipped=%d of %d contactable",
        enriched_n,
        skipped_n,
        len(rows),
    )
    return enriched_n, skipped_n
