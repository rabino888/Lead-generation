"""
Talent T06: Apollo phone reveal adapter (reuses company request/import path).

See SPEC-talent-search.md §7 T06. Does not invent phone numbers — requests
async reveals and writes empty/pending person-oriented phone columns; webhook
import merges later via scripts/import_apollo_phone_webhooks.py.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from agent.utils.talent_people import (
    STAGE_T05_APOLLO_CONTACTABLE,
    STAGE_T06_PHONE_ENRICHED,
    T01_FIELDS,
    read_people_csv,
    talent_stage_path,
)
from agent.webhooks.apollo_phone import resolve_apollo_phone_webhook_url

# Person-oriented deliverable columns (SPEC §6.2) — phone empty until import.
T06_PHONE_FIELDS = [
    "phone",
    "mobile_phone",
    "phone_request_status",
]

# Preferred column order for the talent deliverable slice.
T06_DELIVERABLE_FIELDS = [
    "person_id",
    "full_name",
    "linkedin_url",
    "email",
    "phone",
    "mobile_phone",
    "title",
    "location",
    "company_name",
]

PHONE_STATUS_PENDING = "pending"
PHONE_STATUS_REQUESTED = "requested"
PHONE_STATUS_DRY_RUN = "dry_run"
PHONE_STATUS_SKIPPED = "skipped"

RequestFn = Callable[..., tuple[Path, int]]


def phone_prereqs_ok(
    *,
    api_key: str | None = None,
    webhook_url: str | None = None,
) -> tuple[bool, str]:
    """Return (ok, error_message). Prefer hard error when phone is explicitly on."""
    key = api_key if api_key is not None else (os.environ.get("APOLLO_API_KEY") or "").strip()
    hook = (
        webhook_url
        if webhook_url is not None
        else resolve_apollo_phone_webhook_url()
    )
    if not key:
        return False, "APOLLO_API_KEY is not configured (required when modules.apollo_phone is on)"
    if not hook:
        return (
            False,
            "Apollo phone webhook not configured — set APOLLO_PHONE_WEBHOOK_URL "
            "or PUBLIC_BASE_URL + APOLLO_PHONE_WEBHOOK_SECRET "
            "(required when modules.apollo_phone is on)",
        )
    return True, ""


def prepare_t06_row(row: Mapping[str, Any]) -> dict[str, str]:
    """Copy a T05 contactable row; ensure person phone columns exist (empty)."""
    out: dict[str, str] = {}
    for key, value in row.items():
        out[str(key)] = str(value if value is not None else "")
    for key in T01_FIELDS:
        out.setdefault(key, "")
    # Never invent numbers — only ensure columns exist.
    if not (out.get("phone") or "").strip():
        out["phone"] = ""
    if not (out.get("mobile_phone") or "").strip():
        out["mobile_phone"] = ""
    existing = (out.get("phone_request_status") or "").strip()
    if not existing:
        out["phone_request_status"] = PHONE_STATUS_PENDING
    return out


def prepare_t06_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [prepare_t06_row(r) for r in rows]


def _t06_fieldnames(rows: list[dict[str, str]]) -> list[str]:
    fieldnames: list[str] = []
    seen: set[str] = set()
    preferred = list(T06_DELIVERABLE_FIELDS) + list(T06_PHONE_FIELDS)
    seed_keys: list[str] = []
    if rows:
        seed_keys = list(rows[0].keys())
    for key in preferred + seed_keys + list(T01_FIELDS) + T06_PHONE_FIELDS:
        if key not in seen:
            fieldnames.append(key)
            seen.add(key)
    return fieldnames


def write_t06_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = _t06_fieldnames(rows)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    return path


def mark_phone_request_status(
    rows: list[dict[str, str]],
    *,
    status: str,
    emails_requested: Optional[set[str]] = None,
) -> None:
    """Update phone_request_status for rows that were (or would be) requested."""
    wanted = {e.strip().lower() for e in (emails_requested or set()) if e}
    for row in rows:
        if not wanted:
            row["phone_request_status"] = status
            continue
        email = (row.get("email") or "").strip().lower()
        if email in wanted:
            row["phone_request_status"] = status


def run_talent_phone_stage(
    campaign_dir: Path,
    *,
    input_path: Path | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    skip_request: bool = False,
    request_fn: RequestFn | None = None,
    api_key: str | None = None,
    webhook_url: str | None = None,
) -> tuple[Path, int, int]:
    """
    Read T05 contactable → write T06 with pending phone columns → request reveals.

    Returns (t06_path, row_count, requests_count).
    Phones stay empty until ``import_apollo_phone_webhooks.py`` merges webhook payloads.
    """
    camp = Path(campaign_dir)
    stages = camp / "stages"
    stages.mkdir(parents=True, exist_ok=True)

    src = Path(input_path) if input_path else talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE)
    if not src.is_file():
        raise FileNotFoundError(f"Talent phone input not found: {src}")

    rows = prepare_t06_rows(read_people_csv(src))
    out_path = talent_stage_path(camp, STAGE_T06_PHONE_ENRICHED)
    write_t06_csv(out_path, rows)

    if skip_request:
        for row in rows:
            row["phone_request_status"] = PHONE_STATUS_SKIPPED
        write_t06_csv(out_path, rows)
        return out_path, len(rows), 0

    if not dry_run:
        ok, err = phone_prereqs_ok(api_key=api_key, webhook_url=webhook_url)
        if not ok:
            raise RuntimeError(err)

    fn = request_fn
    if fn is None:
        from scripts.request_apollo_phone_reveals import request_phone_reveals_for_csv

        fn = request_phone_reveals_for_csv

    resolved_key = api_key if api_key is not None else os.environ.get("APOLLO_API_KEY")
    resolved_hook = (
        webhook_url
        if webhook_url is not None
        else resolve_apollo_phone_webhook_url()
    )
    if dry_run:
        resolved_key = resolved_key or "dry-run"
        resolved_hook = resolved_hook or "https://example.invalid/apollo-phone-dry-run"

    log_path, n_req = fn(
        out_path,
        limit=limit,
        only_missing_phone=True,
        include_previously_requested=False,
        dry_run=dry_run,
        api_key=resolved_key,
        webhook_url=resolved_hook,
        sleep=0.0 if dry_run else 1.0,
    )

    status = PHONE_STATUS_DRY_RUN if dry_run else PHONE_STATUS_REQUESTED
    # Re-read request log emails to mark only eligible contacts when possible.
    requested_emails: set[str] = set()
    if log_path.is_file():
        import json

        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            email = (event.get("email") or "").strip().lower()
            if email and event.get("status") in ("requested", "dry_run"):
                requested_emails.add(email)

    if requested_emails:
        mark_phone_request_status(rows, status=status, emails_requested=requested_emails)
    elif n_req == 0:
        # Nothing to request (already had phones / empty input) — leave pending.
        pass
    else:
        mark_phone_request_status(rows, status=status)

    write_t06_csv(out_path, rows)
    return out_path, len(rows), n_req


__all__ = [
    "PHONE_STATUS_DRY_RUN",
    "PHONE_STATUS_PENDING",
    "PHONE_STATUS_REQUESTED",
    "PHONE_STATUS_SKIPPED",
    "T06_DELIVERABLE_FIELDS",
    "T06_PHONE_FIELDS",
    "phone_prereqs_ok",
    "prepare_t06_row",
    "prepare_t06_rows",
    "run_talent_phone_stage",
    "write_t06_csv",
]
