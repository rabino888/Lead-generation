"""
Talent-search person rows: LinkedIn URL normalization + T01 CSV helpers.

See SPEC-talent-search.md §6.2.
"""
from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse, urlunparse

STAGE_T01_RAW_PEOPLE = "T01_raw_people.csv"
STAGE_T02_DEDUPED = "T02_deduped.csv"
STAGE_T02_DEDUPE_REJECTED = "T02_dedupe_rejected.csv"
STAGE_T03_ICP_MATCHED = "T03_icp_matched.csv"
STAGE_T03_ICP_REJECTED = "T03_icp_rejected.csv"
STAGE_T04_PROFILES = "T04_profiles.csv"
STAGE_T04_PROFILE_FAILED = "T04_profile_failed.csv"
STAGE_T05_APOLLO_CONTACTABLE = "T05_apollo_contactable.csv"
STAGE_T05_APOLLO_REJECTED = "T05_apollo_rejected.csv"
STAGE_T06_PHONE_ENRICHED = "T06_phone_enriched.csv"

TALENT_STAGE_ARTIFACTS: tuple[str, ...] = (
    STAGE_T01_RAW_PEOPLE,
    STAGE_T02_DEDUPED,
    STAGE_T02_DEDUPE_REJECTED,
    STAGE_T03_ICP_MATCHED,
    STAGE_T03_ICP_REJECTED,
    STAGE_T04_PROFILES,
    STAGE_T04_PROFILE_FAILED,
    STAGE_T05_APOLLO_CONTACTABLE,
    STAGE_T05_APOLLO_REJECTED,
    STAGE_T06_PHONE_ENRICHED,
)

T01_FIELDS = [
    "person_id",
    "linkedin_url",
    "full_name",
    "first_name",
    "last_name",
    "headline",
    "title",
    "location",
    "company_name",
    "company_linkedin_url",
    "source_url",
    "seed_source_id",
    "notes",
]

# SPEC §6.2 — appended after profile scrape (T04)
T04_PROFILE_FIELDS = [
    "about",
    "experience_summary",
    "education_summary",
    "profile_raw_chars",
    "profile_scraped_at_utc",
    "profile_status",
]

_IN_PATH = re.compile(r"^/in/([^/?#]+)", re.I)


def normalize_linkedin_person_url(url: str) -> str:
    """Canonical https://www.linkedin.com/in/{slug} (no query, no trailing slash)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("linkedin.com/") or raw.startswith("www.linkedin.com/"):
        raw = "https://" + raw
    if "://" not in raw and raw.startswith("/in/"):
        raw = "https://www.linkedin.com" + raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host and "linkedin.com" not in host:
        return ""
    path = parsed.path or ""
    m = _IN_PATH.match(path)
    if not m:
        # Accept bare slug in path like /in/foo/
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0].lower() == "in":
            slug = parts[1]
        elif len(parts) == 1 and parts[0].lower() != "in":
            # sometimes operators paste only the slug
            slug = parts[0]
            return f"https://www.linkedin.com/in/{slug}"
        else:
            return ""
    else:
        slug = m.group(1)
    slug = slug.strip().strip("/")
    if not slug:
        return ""
    return f"https://www.linkedin.com/in/{slug}"


def person_id_from_linkedin(linkedin_url: str, *, prefix: str = "p") -> str:
    norm = normalize_linkedin_person_url(linkedin_url)
    if not norm:
        return ""
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "", prefix or "p")[:16] or "p"
    return f"{safe_prefix}-{digest}"


def split_name(full_name: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", (full_name or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def person_row_from_mapping(
    raw: dict[str, Any],
    *,
    seed_source_id: str = "",
    campaign_prefix: str = "p",
) -> Optional[dict[str, str]]:
    """Build a T01 row; None if no parseable /in/ URL."""
    url_keys = (
        "linkedin_url",
        "linkedin",
        "person_linkedin",
        "profile_url",
        "profileUrl",
        "url",
        "linkedinProfileUrl",
    )
    linkedin = ""
    for k in url_keys:
        linkedin = normalize_linkedin_person_url(str(raw.get(k) or ""))
        if linkedin:
            break
    if not linkedin:
        return None

    full = (raw.get("full_name") or raw.get("name") or raw.get("fullName") or "").strip()
    first = (raw.get("first_name") or raw.get("firstName") or "").strip()
    last = (raw.get("last_name") or raw.get("lastName") or "").strip()
    if not full and (first or last):
        full = f"{first} {last}".strip()
    if full and not (first or last):
        first, last = split_name(full)

    pid = (raw.get("person_id") or "").strip() or person_id_from_linkedin(
        linkedin, prefix=campaign_prefix
    )
    return {
        "person_id": pid,
        "linkedin_url": linkedin,
        "full_name": full,
        "first_name": first,
        "last_name": last,
        "headline": (raw.get("headline") or "").strip(),
        "title": (raw.get("title") or raw.get("job_title") or "").strip(),
        "location": (raw.get("location") or raw.get("geo") or "").strip(),
        "company_name": (raw.get("company_name") or raw.get("company") or "").strip(),
        "company_linkedin_url": (
            str(raw.get("company_linkedin_url") or raw.get("companyLinkedinUrl") or "").strip()
        ),
        "source_url": (raw.get("source_url") or raw.get("search_url") or linkedin).strip(),
        "seed_source_id": seed_source_id or (raw.get("seed_source_id") or "").strip(),
        "notes": (raw.get("notes") or "").strip(),
    }


def write_people_csv(path: Path, rows: Iterable[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    materialised = [r for r in rows if r and (r.get("linkedin_url") or "").strip()]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=T01_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in materialised:
            writer.writerow({k: r.get(k, "") for k in T01_FIELDS})
    return path


def read_people_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def talent_stage_path(campaign_dir: Path, filename: str) -> Path:
    return Path(campaign_dir) / "stages" / filename
