"""
Talent T02: person dedupe by normalized LinkedIn URL (+ optional Sheets ContactDedup).

See SPEC-talent-search.md §7 T02.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Optional

from agent.utils.talent_people import (
    STAGE_T01_RAW_PEOPLE,
    STAGE_T02_DEDUPED,
    STAGE_T02_DEDUPE_REJECTED,
    T01_FIELDS,
    normalize_linkedin_person_url,
    read_people_csv,
    talent_stage_path,
    write_people_csv,
)

REJECT_MISSING = "missing_linkedin"
REJECT_DUP_LINKEDIN = "duplicate_linkedin"
REJECT_DUP_APOLLO = "duplicate_apollo_person_id"
REJECT_DUP_EMAIL = "duplicate_email"
REJECT_SHEETS = "sheets_contact_dedup"


def talent_contact_keys(row: dict[str, Any]) -> list[str]:
    """
    Identity keys for a person row (SPEC order: LinkedIn → apollo id → email).

    Sheets ContactDedup stores ``linkedin:…`` / ``email:…`` (lowercased).
    ``apollo:…`` is in-batch only.
    """
    keys: list[str] = []
    linkedin = normalize_linkedin_person_url(str(row.get("linkedin_url") or ""))
    if linkedin:
        keys.append(f"linkedin:{linkedin.lower()}")
    apollo_id = (row.get("apollo_person_id") or "").strip()
    if apollo_id:
        keys.append(f"apollo:{apollo_id.lower()}")
    email = (row.get("email") or "").strip().lower()
    if email:
        keys.append(f"email:{email}")
    return keys


def dedupe_talent_people(
    rows: list[dict[str, Any]],
    *,
    seen_contact_keys: Optional[set[str]] = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """
    In-batch dedupe by normalized LinkedIn URL (primary), then apollo id / email.

    When ``seen_contact_keys`` is provided (Sheets ContactDedup), rows whose
    ``linkedin:`` / ``email:`` key is already delivered are rejected.

    Returns (kept, rejected). Rejected rows include ``reject_reason``.
    Kept rows have ``linkedin_url`` rewritten to the canonical form.
    """
    kept: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    seen_li: set[str] = set()
    seen_apollo: set[str] = set()
    seen_email: set[str] = set()
    sheets = {str(k).strip().lower() for k in (seen_contact_keys or set()) if k}

    for raw in rows:
        row = {str(k): ("" if v is None else str(v)) for k, v in dict(raw).items()}
        linkedin = normalize_linkedin_person_url(row.get("linkedin_url") or "")
        if not linkedin:
            out = dict(row)
            out["reject_reason"] = REJECT_MISSING
            rejected.append(out)
            continue

        row["linkedin_url"] = linkedin
        li_key = linkedin.lower()

        if li_key in seen_li:
            out = dict(row)
            out["reject_reason"] = REJECT_DUP_LINKEDIN
            rejected.append(out)
            continue

        apollo_id = (row.get("apollo_person_id") or "").strip().lower()
        if apollo_id and apollo_id in seen_apollo:
            out = dict(row)
            out["reject_reason"] = REJECT_DUP_APOLLO
            rejected.append(out)
            continue

        email = (row.get("email") or "").strip().lower()
        if email and email in seen_email:
            out = dict(row)
            out["reject_reason"] = REJECT_DUP_EMAIL
            rejected.append(out)
            continue

        if sheets:
            sheet_hit = next(
                (
                    k
                    for k in talent_contact_keys(row)
                    if k.startswith(("linkedin:", "email:")) and k in sheets
                ),
                None,
            )
            if sheet_hit:
                out = dict(row)
                out["reject_reason"] = f"{REJECT_SHEETS}:{sheet_hit}"
                rejected.append(out)
                continue

        seen_li.add(li_key)
        if apollo_id:
            seen_apollo.add(apollo_id)
        if email:
            seen_email.add(email)
        kept.append(row)

    return kept, rejected


def load_sheets_seen_contacts(client_id: str, logger=None) -> set[str]:
    """Load ContactDedup keys for client; empty set on failure (treat all as new)."""
    _log = logger
    try:
        from agent.integrations.sheets import get_seen_contacts

        return get_seen_contacts(client_id)
    except Exception as e:
        if _log:
            _log.warning(
                "Talent dedupe: could not load ContactDedup (%s) — treating all as new",
                e,
            )
        return set()


def write_rejected_people_csv(path: Path, rows: Iterable[dict[str, str]]) -> Path:
    """Write rejected rows including ``reject_reason`` after T01 fields."""
    path.parent.mkdir(parents=True, exist_ok=True)
    materialised = [dict(r) for r in rows]
    fields = list(T01_FIELDS)
    if "reject_reason" not in fields:
        fields.append("reject_reason")
    for r in materialised:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in materialised:
            writer.writerow({k: r.get(k, "") for k in fields})
    return path


def persist_t02(
    campaign_dir: Path,
    kept: Iterable[dict[str, str]],
    rejected: Iterable[dict[str, str]],
) -> tuple[Path, Path]:
    """Write T02_deduped.csv and T02_dedupe_rejected.csv under campaign stages/."""
    stages = Path(campaign_dir) / "stages"
    kept_list = list(kept)
    rejected_list = list(rejected)
    kept_path = write_people_csv(stages / STAGE_T02_DEDUPED, kept_list)
    rejected_path = write_rejected_people_csv(stages / STAGE_T02_DEDUPE_REJECTED, rejected_list)
    return kept_path, rejected_path


def run_talent_dedupe(
    campaign_dir: Path,
    *,
    input_path: Path | None = None,
    seen_contact_keys: Optional[set[str]] = None,
) -> tuple[Path, Path, int, int]:
    """
    Read T01 (or ``input_path``), dedupe, write T02 artifacts.

    Returns (kept_path, rejected_path, n_kept, n_rejected).
    """
    src = Path(input_path) if input_path else talent_stage_path(campaign_dir, STAGE_T01_RAW_PEOPLE)
    if not src.is_file():
        raise FileNotFoundError(
            f"T01 input missing: {src}\nPass --input PATH or run stage_talent_seed first."
        )
    rows = read_people_csv(src)
    kept, rejected = dedupe_talent_people(rows, seen_contact_keys=seen_contact_keys)
    kept_path, rejected_path = persist_t02(campaign_dir, kept, rejected)
    return kept_path, rejected_path, len(kept), len(rejected)
