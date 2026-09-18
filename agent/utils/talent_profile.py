"""
Talent T04: personal LinkedIn profile scrape on ICP survivors.

Reuses ``get_linkedin_person_profile`` (Apify). Posts stay off via
``APIFY_SKIP_LINKEDIN_POSTS=1``. See SPEC-talent-search.md §7 T04.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from agent.utils.talent_people import (
    STAGE_T03_ICP_MATCHED,
    STAGE_T04_PROFILE_FAILED,
    STAGE_T04_PROFILES,
    T01_FIELDS,
    T04_PROFILE_FIELDS,
    normalize_linkedin_person_url,
    read_people_csv,
    split_name,
    talent_stage_path,
)

PROFILE_OK = "ok"
PROFILE_FAILED = "failed"

ProfileFetcher = Callable[[Optional[str]], dict]


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def profile_has_content(profile: Mapping[str, Any] | None) -> bool:
    """True when Apify returned at least one useful person field."""
    if not profile:
        return False
    for key in (
        "about",
        "headline",
        "name",
        "location",
        "experience_summary",
        "education_summary",
    ):
        if _as_str(profile.get(key)):
            return True
    return False


def merge_profile_onto_row(
    row: Mapping[str, Any],
    profile: Mapping[str, Any] | None,
    *,
    scraped_at_utc: str | None = None,
) -> dict[str, str]:
    """
    Copy T01 identity + merge profile fields; set ``profile_status``.

    Empty / useless profile → ``failed`` (row still written for QA).
    """
    out: dict[str, str] = {}
    for k in T01_FIELDS:
        out[k] = _as_str(row.get(k))
    for k, v in row.items():
        key = str(k)
        if key in out or key in T04_PROFILE_FIELDS:
            continue
        if key in ("reject_reason",):
            continue
        out[key] = _as_str(v)

    linkedin = normalize_linkedin_person_url(out.get("linkedin_url") or "")
    if linkedin:
        out["linkedin_url"] = linkedin

    prof = dict(profile or {})
    ok = bool(linkedin) and profile_has_content(prof)
    scraped = scraped_at_utc or _now_utc()

    about = _as_str(prof.get("about"))
    experience = _as_str(prof.get("experience_summary"))
    education = _as_str(prof.get("education_summary"))
    headline = _as_str(prof.get("headline"))
    location = _as_str(prof.get("location"))
    name = _as_str(prof.get("name"))

    if about:
        out["about"] = about
    else:
        out.setdefault("about", "")

    out["experience_summary"] = experience
    out["education_summary"] = education

    if headline:
        out["headline"] = headline
    if location:
        out["location"] = location
    if name and not out.get("full_name"):
        out["full_name"] = name
        first, last = split_name(name)
        if first and not out.get("first_name"):
            out["first_name"] = first
        if last and not out.get("last_name"):
            out["last_name"] = last

    raw_parts = [
        out.get("about") or "",
        out.get("experience_summary") or "",
        out.get("education_summary") or "",
        out.get("headline") or "",
    ]
    out["profile_raw_chars"] = str(sum(len(p) for p in raw_parts)) if ok else "0"
    out["profile_scraped_at_utc"] = scraped if (ok or linkedin) else ""
    out["profile_status"] = PROFILE_OK if ok else PROFILE_FAILED
    return out


def enrich_person_row(
    row: Mapping[str, Any],
    *,
    fetch_profile: ProfileFetcher | None = None,
) -> dict[str, str]:
    """Scrape one person (or fail) and merge profile fields onto the row."""
    from agent.integrations.apify import get_linkedin_person_profile

    fetcher = fetch_profile or get_linkedin_person_profile
    linkedin = normalize_linkedin_person_url(_as_str(row.get("linkedin_url")))
    if not linkedin:
        return merge_profile_onto_row(row, None)

    profile = fetcher(linkedin) or {}
    return merge_profile_onto_row(row, profile)


def enrich_people_rows(
    rows: list[Mapping[str, Any]],
    *,
    fetch_profile: ProfileFetcher | None = None,
    limit: int | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """
    Profile each row. Returns (ok_rows, failed_rows).

    Both lists are fully merged person dicts with ``profile_status``.
    """
    ok: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    sliced = list(rows[:limit] if limit is not None else rows)
    for row in sliced:
        enriched = enrich_person_row(row, fetch_profile=fetch_profile)
        if enriched.get("profile_status") == PROFILE_OK:
            ok.append(enriched)
        else:
            failed.append(enriched)
    return ok, failed


def _write_profile_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    base = list(T01_FIELDS) + list(T04_PROFILE_FIELDS)
    if not rows:
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=base, extrasaction="ignore")
            w.writeheader()
        return path
    extra = [k for k in rows[0].keys() if k not in base]
    fieldnames = base + extra
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    return path


def run_talent_profile_stage(
    campaign_dir: Path,
    *,
    input_path: Path | None = None,
    fetch_profile: ProfileFetcher | None = None,
    limit: int | None = None,
    skip_posts: bool = True,
) -> tuple[Path, Path, int, int]:
    """
    Read T03 ICP matched (or ``input_path``), scrape profiles, write T04 CSVs.

    Sets ``APIFY_SKIP_LINKEDIN_POSTS=1`` for the duration when ``skip_posts``.
    Returns (ok_path, failed_path, ok_count, failed_count).
    """
    camp = Path(campaign_dir)
    stages = camp / "stages"
    stages.mkdir(parents=True, exist_ok=True)

    src = Path(input_path) if input_path else talent_stage_path(camp, STAGE_T03_ICP_MATCHED)
    if not src.is_file():
        raise FileNotFoundError(f"Talent profile input not found: {src}")

    prev_skip = os.environ.get("APIFY_SKIP_LINKEDIN_POSTS")
    if skip_posts:
        os.environ["APIFY_SKIP_LINKEDIN_POSTS"] = "1"

    try:
        rows = read_people_csv(src)
        ok_rows, failed_rows = enrich_people_rows(
            rows,
            fetch_profile=fetch_profile,
            limit=limit,
        )
    finally:
        if skip_posts:
            if prev_skip is None:
                os.environ.pop("APIFY_SKIP_LINKEDIN_POSTS", None)
            else:
                os.environ["APIFY_SKIP_LINKEDIN_POSTS"] = prev_skip

    ok_path = talent_stage_path(camp, STAGE_T04_PROFILES)
    failed_path = talent_stage_path(camp, STAGE_T04_PROFILE_FAILED)
    _write_profile_csv(ok_path, ok_rows)
    _write_profile_csv(failed_path, failed_rows)
    return ok_path, failed_path, len(ok_rows), len(failed_rows)


__all__ = [
    "PROFILE_FAILED",
    "PROFILE_OK",
    "enrich_people_rows",
    "enrich_person_row",
    "merge_profile_onto_row",
    "profile_has_content",
    "run_talent_profile_stage",
]
