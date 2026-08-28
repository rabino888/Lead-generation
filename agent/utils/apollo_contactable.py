"""
Guardrails for stages/04_apollo_contactable.csv — never wipe Apollo person fields.

Email-only rows (email present but no name / LinkedIn / apollo_person_id) break
LinkedIn person enrichment. Merges preserve existing person columns when new rows are thin.
"""
from __future__ import annotations

import csv
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from agent.utils.apollo_contact_gate import apollo_requires_dm_linkedin

APOLLO_PERSON_COLUMNS = (
    "decision_maker_apollo_person_id",
    "decision_maker_name",
    "decision_maker_title",
    "decision_maker_linkedin",
    "decision_maker_location",
    "decision_maker_email_status",
    "decision_maker_direct_phone",
    "decision_maker_mobile_phone",
)


def merge_apollo_person_fields(
    existing: dict[str, Any],
    new: dict[str, Any],
) -> dict[str, Any]:
    """Keep non-empty Apollo person fields from `existing` when `new` omits them."""
    merged = dict(new)
    for col in APOLLO_PERSON_COLUMNS:
        old = str(existing.get(col) or "").strip()
        new_val = str(merged.get(col) or "").strip()
        if old and not new_val:
            merged[col] = existing[col]
    old_email = str(existing.get("decision_maker_email") or "").strip()
    new_email = str(merged.get("decision_maker_email") or "").strip()
    if old_email and not new_email:
        merged["decision_maker_email"] = existing["decision_maker_email"]
    return merged


def is_missing_dm_linkedin_row(row: dict[str, Any]) -> bool:
    """True when email present but Apollo DM LinkedIn URL missing (default policy)."""
    if not apollo_requires_dm_linkedin():
        return False
    email = str(row.get("decision_maker_email") or "").strip()
    linkedin = str(row.get("decision_maker_linkedin") or "").strip()
    return bool(email) and not linkedin


def is_email_only_row(row: dict[str, Any]) -> bool:
    """True when a row has email but no Apollo person identity fields."""
    email = str(row.get("decision_maker_email") or "").strip()
    if not email:
        return False
    name = str(row.get("decision_maker_name") or "").strip()
    linkedin = str(row.get("decision_maker_linkedin") or "").strip()
    apollo_id = str(row.get("decision_maker_apollo_person_id") or "").strip()
    return not name and not linkedin and not apollo_id


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def write_apollo_contactable_csv(
    campaign: CampaignConfig,
    rows: Iterable[dict[str, Any]],
    *,
    strict: bool = True,
) -> Path:
    """
    Write 04_apollo_contactable.csv merging with existing rows by email.

    - Preserves Apollo person fields when new data is thinner than existing.
    - Keeps prior contactable rows not present in this write batch.
    - Raises if `strict` and any row is email-only (no name / LI / apollo id).
    """
    ensure_stages_dir(campaign)
    path = stage_path(campaign, "apollo_contactable")
    new_rows = list(rows)

    if strict:
        bad_email_only = [r for r in new_rows if is_email_only_row(r)]
        bad_no_li = [r for r in new_rows if is_missing_dm_linkedin_row(r)]
        if bad_email_only:
            sample = ", ".join(str(r.get("company_name") or "?") for r in bad_email_only[:5])
            raise ValueError(
                f"Refusing to write {len(bad_email_only)} email-only row(s) to 04_apollo_contactable.csv "
                f"(missing name, LinkedIn URL, and apollo_person_id). Examples: {sample}."
            )
        if bad_no_li:
            sample = ", ".join(str(r.get("company_name") or "?") for r in bad_no_li[:5])
            raise ValueError(
                f"Refusing to write {len(bad_no_li)} row(s) without decision_maker_linkedin. "
                f"Apollo must return linkedin_url — do not use Google-search backup. Examples: {sample}."
            )

    existing = _read_csv(path)
    by_email: dict[str, dict[str, Any]] = {}
    fieldnames: list[str] = []

    for row in existing:
        email = str(row.get("decision_maker_email") or "").strip().lower()
        if email:
            by_email[email] = dict(row)
        for k in row:
            if k not in fieldnames:
                fieldnames.append(k)

    for row in new_rows:
        email = str(row.get("decision_maker_email") or "").strip().lower()
        if not email:
            continue
        prior = by_email.get(email)
        merged = merge_apollo_person_fields(prior or {}, row) if prior else dict(row)
        by_email[email] = merged
        for k in merged:
            if k not in fieldnames:
                fieldnames.append(k)

    out_rows = list(by_email.values())
    if not out_rows:
        if path.exists():
            path.unlink()
        return path

    if path.exists():
        bak = path.with_suffix(
            path.suffix + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy2(path, bak)

    for col in (
        "lead_id",
        "company_name",
        "website",
        "decision_maker_email",
        "decision_maker_name",
        "decision_maker_linkedin",
        "run_id",
    ):
        if col not in fieldnames:
            fieldnames.insert(0, col)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in out_rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    return path
