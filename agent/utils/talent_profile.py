"""
Talent T04: personal LinkedIn profile scrape on ICP survivors.

Reuses ``get_linkedin_person_profile`` (Apify). Posts stay off via
``APIFY_SKIP_LINKEDIN_POSTS=1``. See SPEC-talent-search.md §7 T04.
"""
from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from agent.utils.talent_people import (
    STAGE_T03_ICP_MATCHED,
    STAGE_T04_PROFILE_FAILED,
    STAGE_T04_PROFILES,
    STAGE_T05_APOLLO_CONTACTABLE,
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


def _is_opaque_member_slug(url: str) -> bool:
    norm = normalize_linkedin_person_url(url)
    if not norm:
        return False
    slug = norm.rstrip("/").rsplit("/", 1)[-1]
    return bool(slug) and slug.startswith(("ACw", "ACo", "AEMA", "AEM"))


def _linkedin_url_preferred(candidate: str, current: str) -> bool:
    """True when candidate is a better Apollo match key than current."""
    cand = normalize_linkedin_person_url(candidate)
    cur = normalize_linkedin_person_url(current)
    if not cand:
        return False
    if not cur:
        return True
    if cand == cur:
        return False
    # Prefer non-opaque vanity over HarvestAPI ACw member ids
    if _is_opaque_member_slug(cur) and not _is_opaque_member_slug(cand):
        return True
    return False


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
        "current_position",
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
    # Prefer vanity / public LinkedIn URL from the full profile scrape when seed
    # was an opaque HarvestAPI member id (ACw…) that Apollo cannot enrich well.
    prof_li = normalize_linkedin_person_url(_as_str(prof.get("linkedin_url")))
    if prof_li and _linkedin_url_preferred(prof_li, out.get("linkedin_url") or ""):
        out["linkedin_url"] = prof_li
        linkedin = prof_li

    ok = bool(linkedin) and profile_has_content(prof)
    scraped = scraped_at_utc or _now_utc()

    about = _as_str(prof.get("about"))
    experience = _as_str(prof.get("experience_summary"))
    education = _as_str(prof.get("education_summary"))
    current_position = _as_str(prof.get("current_position"))
    headline = _as_str(prof.get("headline"))
    location = _as_str(prof.get("location"))
    name = _as_str(prof.get("name"))

    if about:
        out["about"] = about
    else:
        out.setdefault("about", "")

    out["experience_summary"] = experience
    out["education_summary"] = education
    if current_position:
        out["current_position"] = current_position
    elif experience:
        # First segment of experience_summary is usually current role
        out["current_position"] = experience.split(";")[0].strip()[:300]
    else:
        out["current_position"] = _as_str(out.get("title") or out.get("headline") or "")

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

    from agent.utils.outreach_language import infer_outreach_language

    out["outreach_language"] = infer_outreach_language(
        about=out.get("about") or "",
        headline=out.get("headline") or "",
        experience_summary=out.get("experience_summary") or "",
        current_position=out.get("current_position") or "",
        languages=prof.get("languages") or out.get("languages"),
        location=out.get("location") or "",
    )

    raw_parts = [
        out.get("about") or "",
        out.get("experience_summary") or "",
        out.get("education_summary") or "",
        out.get("current_position") or "",
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


def write_passthrough_profiles(
    campaign_dir: Path,
    *,
    input_path: Path | None = None,
    limit: int | None = None,
) -> tuple[Path, Path, int, int]:
    """
    When ``apify_dm_profile`` is off: copy ICP rows to T04 without scraping.

    Apollo still needs T04_profiles.csv as input.
    """
    camp = Path(campaign_dir)
    stages = camp / "stages"
    stages.mkdir(parents=True, exist_ok=True)
    src = Path(input_path) if input_path else talent_stage_path(camp, STAGE_T03_ICP_MATCHED)
    if not src.is_file():
        raise FileNotFoundError(f"Talent profile input not found: {src}")
    rows = read_people_csv(src)
    if limit is not None:
        rows = rows[:limit]
    ok_rows = [merge_profile_onto_row(r, None) for r in rows]
    for r in ok_rows:
        # Passthrough: keep seed title as current_position; mark status ok for Apollo
        if not r.get("current_position"):
            r["current_position"] = r.get("title") or r.get("headline") or ""
        r["profile_status"] = PROFILE_OK
        r["profile_raw_chars"] = str(
            len(r.get("headline") or "") + len(r.get("current_position") or "")
        )
    ok_path = talent_stage_path(camp, STAGE_T04_PROFILES)
    failed_path = talent_stage_path(camp, STAGE_T04_PROFILE_FAILED)
    _write_profile_csv(ok_path, ok_rows)
    _write_profile_csv(failed_path, [])
    return ok_path, failed_path, len(ok_rows), 0


def run_talent_profile_stage(
    campaign_dir: Path,
    *,
    input_path: Path | None = None,
    fetch_profile: ProfileFetcher | None = None,
    limit: int | None = None,
    skip_posts: bool = True,
    scrape_profile: bool = False,
) -> tuple[Path, Path, int, int]:
    """
    Read T03 ICP matched (or ``input_path``), write T04 CSVs.

    Default ``scrape_profile=False``: vanity-resolve opaque LinkedIn URLs via
    treg SERP, then passthrough seed/ICP fields (no thin ``treg.linkedin.user.profile``).
    Full work history runs later on T05 contactables via
    ``enrich_contactable_work_history`` (anyapi full → Apify fallback).

    Set ``scrape_profile=True`` (or CLI ``--scrape``) for the legacy T04 scrape.
    """
    camp = Path(campaign_dir)
    stages = camp / "stages"
    stages.mkdir(parents=True, exist_ok=True)

    src = Path(input_path) if input_path else talent_stage_path(camp, STAGE_T03_ICP_MATCHED)
    if not src.is_file():
        raise FileNotFoundError(f"Talent profile input not found: {src}")

    prev_skip = os.environ.get("APIFY_SKIP_LINKEDIN_POSTS")
    prev_apify_fb = os.environ.get("TREG_SKIP_APIFY_FALLBACK")
    if skip_posts:
        os.environ["APIFY_SKIP_LINKEDIN_POSTS"] = "1"
    # If someone opts into T04 scrape, still defer Apify until post-Apollo.
    os.environ["TREG_SKIP_APIFY_FALLBACK"] = "1"

    try:
        rows = read_people_csv(src)
        from agent.utils.vanity_linkedin import resolve_opaque_rows

        allow_fallback = (os.environ.get("TALENT_VANITY_PEOPLE_SEARCH") or "").strip() in (
            "1",
            "true",
            "yes",
        )
        rows = resolve_opaque_rows(
            rows,
            limit=limit,
            allow_people_search_fallback=allow_fallback,
        )
        if scrape_profile:
            ok_rows, failed_rows = enrich_people_rows(
                rows,
                fetch_profile=fetch_profile,
                limit=limit,
            )
        else:
            sliced = list(rows[:limit] if limit is not None else rows)
            ok_rows = []
            for r in sliced:
                merged = merge_profile_onto_row(r, None)
                if not merged.get("current_position"):
                    merged["current_position"] = (
                        merged.get("title") or merged.get("headline") or ""
                    )
                merged["profile_status"] = PROFILE_OK
                merged["profile_raw_chars"] = str(
                    len(merged.get("headline") or "")
                    + len(merged.get("current_position") or "")
                    + len(merged.get("about") or "")
                )
                ok_rows.append(merged)
            failed_rows = []
    finally:
        if skip_posts:
            if prev_skip is None:
                os.environ.pop("APIFY_SKIP_LINKEDIN_POSTS", None)
            else:
                os.environ["APIFY_SKIP_LINKEDIN_POSTS"] = prev_skip
        if prev_apify_fb is None:
            os.environ.pop("TREG_SKIP_APIFY_FALLBACK", None)
        else:
            os.environ["TREG_SKIP_APIFY_FALLBACK"] = prev_apify_fb

    ok_path = talent_stage_path(camp, STAGE_T04_PROFILES)
    failed_path = talent_stage_path(camp, STAGE_T04_PROFILE_FAILED)
    _write_profile_csv(ok_path, ok_rows)
    _write_profile_csv(failed_path, failed_rows)
    return ok_path, failed_path, len(ok_rows), len(failed_rows)


def enrich_contactable_work_history(
    campaign_dir: Path,
    *,
    fetch_apify_profile: ProfileFetcher | None = None,
    only_missing: bool = True,
) -> tuple[int, int]:
    """
    Full profile enrich on T05 contactables (after Apollo email gate).

    Prefers ``anyapi.linkedin.user.profile.full`` via treg (~$0.003–0.0042);
    falls back to Apify ``apimaestro/linkedin-profile-detail`` when thin.
    Returns (enriched_count, still_missing_count).
    """
    from agent.integrations.apify import get_linkedin_person_profile_apify
    from agent.integrations.treg import get_linkedin_person_profile_full, treg_configured
    from agent.utils.logger import log

    camp = Path(campaign_dir)
    t05_path = talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE)
    t04_path = talent_stage_path(camp, STAGE_T04_PROFILES)
    if not t05_path.is_file():
        return 0, 0

    t05 = read_people_csv(t05_path)
    if not t05:
        return 0, 0

    if fetch_apify_profile is not None:
        fetcher = fetch_apify_profile
    elif treg_configured():
        fetcher = get_linkedin_person_profile_full
    else:
        fetcher = get_linkedin_person_profile_apify
    t04_by_id: dict[str, dict[str, str]] = {}
    if t04_path.is_file():
        for row in read_people_csv(t04_path):
            pid = (row.get("person_id") or "").strip()
            if pid:
                t04_by_id[pid] = dict(row)

    enriched_n = 0
    missing_n = 0
    updated: list[dict[str, str]] = []

    for row in t05:
        has_exp = bool((row.get("experience_summary") or "").strip())
        if only_missing and has_exp:
            updated.append(dict(row))
            continue

        linkedin = normalize_linkedin_person_url(_as_str(row.get("linkedin_url")))
        if not linkedin:
            updated.append(dict(row))
            missing_n += 1
            continue

        name = row.get("full_name") or row.get("person_id") or linkedin
        log.info("Work-history enrich (post-Apollo): %s", name)
        profile = fetcher(linkedin) or {}
        merged = merge_profile_onto_row(row, profile)
        # Preserve Apollo email / reject fields from the contactable row
        for k, v in row.items():
            if k.startswith("apollo") or k in (
                "email",
                "email_status",
                "reject_reason",
                "apollo_person_id",
                "apollo_reveal_status",
            ):
                if v:
                    merged[k] = _as_str(v)
        if (merged.get("experience_summary") or "").strip():
            enriched_n += 1
        else:
            missing_n += 1
        updated.append(merged)

        pid = (merged.get("person_id") or "").strip()
        if pid and pid in t04_by_id:
            for key in (
                "about",
                "current_position",
                "experience_summary",
                "education_summary",
                "outreach_language",
                "profile_raw_chars",
                "profile_scraped_at_utc",
                "profile_status",
                "headline",
                "linkedin_url",
            ):
                if merged.get(key):
                    t04_by_id[pid][key] = merged[key]

    _write_profile_csv(t05_path, updated)
    if t04_by_id:
        # Keep T04 row order stable if possible
        t04_rows = read_people_csv(t04_path) if t04_path.is_file() else []
        out_t04: list[dict[str, str]] = []
        for row in t04_rows:
            pid = (row.get("person_id") or "").strip()
            out_t04.append(t04_by_id.get(pid) or dict(row))
        _write_profile_csv(t04_path, out_t04)

    log.info(
        "Post-Apollo work history: enriched=%d still_missing=%d of %d contactable",
        enriched_n,
        missing_n,
        len(t05),
    )
    return enriched_n, missing_n


def _format_recent_posts(posts: list[dict[str, Any]], *, limit: int = 5) -> str:
    snippets: list[str] = []
    for post in posts[:limit]:
        if not isinstance(post, dict):
            continue
        text = str(post.get("text") or "").strip()
        if not text:
            continue
        text = re.sub(r"\s+", " ", text)
        snippets.append(text[:280])
    return " | ".join(snippets)


def enrich_contactable_posts(
    campaign_dir: Path,
    *,
    fetch_posts: ProfileFetcher | None = None,
    only_missing: bool = True,
    max_posts: int | None = None,
) -> tuple[int, int]:
    """
    Recent LinkedIn posts on T05 contactables when ``apify_dm_posts`` is on.

    Uses treg ``harvestapi.linkedin.user.posts`` when LINKEDIN_ENRICH_PROVIDER=treg,
    else Apify. Returns (enriched_count, empty_count).
    """
    from agent.integrations.apify import get_linkedin_profile_posts
    from agent.utils.logger import log

    camp = Path(campaign_dir)
    t05_path = talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE)
    t04_path = talent_stage_path(camp, STAGE_T04_PROFILES)
    if not t05_path.is_file():
        return 0, 0

    t05 = read_people_csv(t05_path)
    if not t05:
        return 0, 0

    t04_by_id: dict[str, dict[str, str]] = {}
    if t04_path.is_file():
        for row in read_people_csv(t04_path):
            pid = (row.get("person_id") or "").strip()
            if pid:
                t04_by_id[pid] = dict(row)

    enriched_n = 0
    empty_n = 0
    updated: list[dict[str, str]] = []

    prev_skip = os.environ.get("APIFY_SKIP_LINKEDIN_POSTS")
    # Module is on when this runs — force posts fetch even if T04 left skip=1.
    os.environ["APIFY_SKIP_LINKEDIN_POSTS"] = "0"
    try:
        for row in t05:
            has_posts = bool((row.get("linkedin_post_summary") or "").strip()) or bool(
                (row.get("linkedin_recent_posts") or "").strip()
            )
            if only_missing and has_posts:
                updated.append(dict(row))
                continue

            linkedin = normalize_linkedin_person_url(_as_str(row.get("linkedin_url")))
            if not linkedin:
                updated.append(dict(row))
                empty_n += 1
                continue

            name = row.get("full_name") or row.get("person_id") or linkedin
            log.info("LinkedIn posts enrich (post-Apollo): %s", name)
            if fetch_posts is not None:
                raw = fetch_posts(linkedin) or {}
            elif max_posts is not None:
                raw = get_linkedin_profile_posts(linkedin, max_posts=max_posts) or {}
            else:
                raw = get_linkedin_profile_posts(linkedin) or {}

            posts = raw.get("posts") if isinstance(raw, dict) else []
            if not isinstance(posts, list):
                posts = []
            interests = raw.get("interests") if isinstance(raw, dict) else []
            if not isinstance(interests, list):
                interests = []
            summary = ""
            if isinstance(raw, dict):
                summary = str(raw.get("summary") or raw.get("post_summary") or "").strip()

            merged = dict(row)
            merged["linkedin_post_summary"] = summary
            merged["linkedin_interests"] = "; ".join(
                str(x).strip() for x in interests if str(x).strip()
            )
            merged["linkedin_recent_posts"] = _format_recent_posts(
                [p for p in posts if isinstance(p, dict)]
            )
            if merged["linkedin_post_summary"] or merged["linkedin_recent_posts"]:
                enriched_n += 1
            else:
                empty_n += 1
            updated.append(merged)

            pid = (merged.get("person_id") or "").strip()
            if pid and pid in t04_by_id:
                for key in (
                    "linkedin_post_summary",
                    "linkedin_interests",
                    "linkedin_recent_posts",
                ):
                    if merged.get(key):
                        t04_by_id[pid][key] = merged[key]
    finally:
        if prev_skip is None:
            os.environ.pop("APIFY_SKIP_LINKEDIN_POSTS", None)
        else:
            os.environ["APIFY_SKIP_LINKEDIN_POSTS"] = prev_skip

    _write_profile_csv(t05_path, updated)
    if t04_by_id and t04_path.is_file():
        t04_rows = read_people_csv(t04_path)
        out_t04 = []
        for row in t04_rows:
            pid = (row.get("person_id") or "").strip()
            out_t04.append(t04_by_id.get(pid) or dict(row))
        _write_profile_csv(t04_path, out_t04)

    log.info(
        "Post-Apollo LinkedIn posts: enriched=%d empty=%d of %d contactable",
        enriched_n,
        empty_n,
        len(t05),
    )
    return enriched_n, empty_n


__all__ = [
    "PROFILE_FAILED",
    "PROFILE_OK",
    "enrich_contactable_posts",
    "enrich_contactable_work_history",
    "enrich_people_rows",
    "enrich_person_row",
    "merge_profile_onto_row",
    "profile_has_content",
    "run_talent_profile_stage",
    "write_passthrough_profiles",
]
