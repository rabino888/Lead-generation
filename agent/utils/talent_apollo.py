"""
Talent T05: Apollo people/match by LinkedIn URL → personal email gate (D3).

See SPEC-talent-search.md §7 T05.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from urllib.parse import urlparse

from agent.integrations.apollo import enrich_person_by_linkedin
from agent.models import Contact
from agent.utils.talent_people import (
    STAGE_T04_PROFILE_FAILED,
    STAGE_T04_PROFILES,
    STAGE_T05_APOLLO_CONTACTABLE,
    STAGE_T05_APOLLO_REJECTED,
    T01_FIELDS,
    normalize_linkedin_person_url,
    read_people_csv,
    split_name,
    talent_stage_path,
)

REJECT_MISSING_LINKEDIN = "missing_linkedin"
REJECT_PROFILE_NOT_OK = "profile_status_not_ok"
REJECT_MATCH_FAILED = "apollo_match_failed"
REJECT_NO_EMAIL = "no_personal_email"

T05_EXTRA_FIELDS = [
    "apollo_person_id",
    "email",
    "email_status",
    "apollo_reveal_status",
    "reject_reason",
]

EnrichFn = Callable[..., Optional[Contact]]


def _row_copy(row: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in T01_FIELDS:
        out[k] = str(row.get(k, "") or "")
    for k, v in row.items():
        key = str(k)
        if key in out or key in ("reject_reason",):
            continue
        out[key] = str(v if v is not None else "")
    return out


def _profile_ok(row: Mapping[str, Any]) -> bool:
    status = (row.get("profile_status") or "").strip().lower()
    # Empty status: allow (tests / --input without T04); failed/skipped blocked.
    if not status:
        return True
    return status == "ok"


def _names_for_row(row: Mapping[str, Any]) -> tuple[str, str]:
    first = (row.get("first_name") or "").strip()
    last = (row.get("last_name") or "").strip()
    if first or last:
        return first, last
    return split_name((row.get("full_name") or "").strip())


def _domain_from_url(url: str) -> str:
    try:
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
        host = (parsed.netloc or parsed.path or "").replace("www.", "").strip("/")
        return host
    except Exception:
        return ""


def _domain_for_row(row: Mapping[str, Any]) -> str:
    for key in ("company_website", "website", "company_domain", "domain"):
        raw = (row.get(key) or "").strip()
        if not raw:
            continue
        if "@" in raw and "://" not in raw:
            continue
        domain = _domain_from_url(raw)
        if domain:
            return domain
    return ""


def enrich_talent_person(
    row: Mapping[str, Any],
    *,
    enrich_fn: EnrichFn | None = None,
) -> tuple[Optional[Contact], str]:
    """
    Match/reveal one person. Returns (contact_with_email, "") or (None, reject_reason).
    """
    linkedin = normalize_linkedin_person_url(str(row.get("linkedin_url") or ""))
    if not linkedin:
        return None, REJECT_MISSING_LINKEDIN

    fn = enrich_fn or enrich_person_by_linkedin
    first, last = _names_for_row(row)
    org = (row.get("company_name") or "").strip()
    domain = _domain_for_row(row)

    try:
        contact = fn(
            linkedin,
            reveal_personal_emails=True,
            first_name=first,
            last_name=last,
            organization_name=org,
            domain=domain,
        )
    except TypeError:
        # Minimal mock / older signature: linkedin URL only
        contact = fn(linkedin)  # type: ignore[misc, call-arg]
    except Exception:
        return None, REJECT_MATCH_FAILED

    if not contact:
        return None, REJECT_MATCH_FAILED
    email = (contact.email or "").strip()
    if not email:
        return None, REJECT_NO_EMAIL
    return contact, ""


def apply_talent_apollo(
    rows: list[Mapping[str, Any]],
    *,
    include_failed_profiles: bool = False,
    enrich_fn: EnrichFn | None = None,
    max_leads: int | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """
    Split profile rows into contactable / rejected (D3: personal email required).

    Prefer ``profile_status=ok``; failed/skipped go to rejected unless
    ``include_failed_profiles``.
    """
    contactable: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    attempted = 0

    for row in rows:
        if max_leads is not None and attempted >= max_leads:
            break

        out = _row_copy(row)
        linkedin = normalize_linkedin_person_url(str(row.get("linkedin_url") or ""))
        if linkedin:
            out["linkedin_url"] = linkedin

        if not include_failed_profiles and not _profile_ok(row):
            out["apollo_person_id"] = ""
            out["email"] = ""
            out["email_status"] = ""
            out["apollo_reveal_status"] = "skipped"
            out["reject_reason"] = REJECT_PROFILE_NOT_OK
            rejected.append(out)
            continue

        attempted += 1
        contact, reason = enrich_talent_person(row, enrich_fn=enrich_fn)
        if contact and not reason:
            out["apollo_person_id"] = (contact.apollo_person_id or "").strip()
            out["email"] = (contact.email or "").strip()
            out["email_status"] = (contact.email_status or "").strip()
            if contact.linkedin_url:
                norm = normalize_linkedin_person_url(contact.linkedin_url)
                if norm:
                    out["linkedin_url"] = norm
            if contact.name and not (out.get("full_name") or "").strip():
                out["full_name"] = contact.name
            if contact.title and not (out.get("title") or "").strip():
                out["title"] = contact.title
            if contact.location and not (out.get("location") or "").strip():
                out["location"] = contact.location
            out["apollo_reveal_status"] = "revealed"
            out["reject_reason"] = ""
            contactable.append(out)
        else:
            out["apollo_person_id"] = (
                (contact.apollo_person_id or "").strip() if contact else ""
            )
            out["email"] = ""
            out["email_status"] = (contact.email_status or "").strip() if contact else ""
            out["apollo_reveal_status"] = "failed"
            out["reject_reason"] = reason or REJECT_NO_EMAIL
            rejected.append(out)

    return contactable, rejected


def _write_stage_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        fieldnames = list(T01_FIELDS) + T05_EXTRA_FIELDS
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
        return path
    # Preserve input order; ensure T05 columns present
    fieldnames: list[str] = []
    seen: set[str] = set()
    for key in list(rows[0].keys()) + T05_EXTRA_FIELDS:
        if key not in seen:
            fieldnames.append(key)
            seen.add(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    return path


def _merge_profile_rows(primary: list[dict[str, str]], extra: list[dict[str, str]]) -> list[dict[str, str]]:
    """Prefer primary (ok) rows when LinkedIn URLs collide."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in list(primary) + list(extra):
        li = normalize_linkedin_person_url(row.get("linkedin_url") or "")
        key = li or (row.get("person_id") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(row)
    return out


def run_talent_apollo_stage(
    campaign_dir: Path,
    *,
    input_path: Path | None = None,
    include_failed_profiles: bool = False,
    enrich_fn: EnrichFn | None = None,
    max_leads: int | None = None,
) -> tuple[Path, Path, int, int]:
    """
    Read T04 profiles (or ``input_path``), write T05 contactable + rejected.

    With ``include_failed_profiles`` and no custom input, also loads
    ``T04_profile_failed.csv`` so the operator force path can Apollo those rows.

    Returns (contactable_path, rejected_path, contactable_count, rejected_count).
    """
    camp = Path(campaign_dir)
    stages = camp / "stages"
    stages.mkdir(parents=True, exist_ok=True)

    if input_path:
        src = Path(input_path)
        if not src.is_file():
            raise FileNotFoundError(f"Talent Apollo input not found: {src}")
        rows = read_people_csv(src)
    else:
        ok_src = talent_stage_path(camp, STAGE_T04_PROFILES)
        if not ok_src.is_file():
            raise FileNotFoundError(f"Talent Apollo input not found: {ok_src}")
        rows = read_people_csv(ok_src)
        if include_failed_profiles:
            fail_src = talent_stage_path(camp, STAGE_T04_PROFILE_FAILED)
            if fail_src.is_file():
                rows = _merge_profile_rows(rows, read_people_csv(fail_src))

    contactable, rejected = apply_talent_apollo(
        rows,
        include_failed_profiles=include_failed_profiles,
        enrich_fn=enrich_fn,
        max_leads=max_leads,
    )

    ok_path = talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE)
    rej_path = talent_stage_path(camp, STAGE_T05_APOLLO_REJECTED)
    _write_stage_csv(ok_path, contactable)
    _write_stage_csv(rej_path, rejected)
    return ok_path, rej_path, len(contactable), len(rejected)


__all__ = [
    "REJECT_MATCH_FAILED",
    "REJECT_MISSING_LINKEDIN",
    "REJECT_NO_EMAIL",
    "REJECT_PROFILE_NOT_OK",
    "apply_talent_apollo",
    "enrich_talent_person",
    "run_talent_apollo_stage",
]
