"""
Request Apollo phone reveal for contacts in a lead CSV.

Apollo phone reveal is asynchronous: phone numbers are delivered later to
APOLLO_PHONE_WEBHOOK_URL. This script logs every request so webhook payloads can
be matched back to CSV rows.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.webhooks.apollo_phone import resolve_apollo_phone_webhook_url

APOLLO_BASE = "https://api.apollo.io/api/v1"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to lead CSV")
    parser.add_argument("--limit", type=int, default=None, help="Max contacts to request")
    parser.add_argument("--only-missing-phone", action="store_true", default=True)
    parser.add_argument("--include-previously-requested", action="store_true")
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between Apollo requests")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv(".env", override=True)
    csv_path = Path(args.csv)
    # Pre-count for operator messaging (request_phone_reveals_for_csv re-reads).
    preview = _eligible_contacts(_read_csv(csv_path), args.only_missing_phone)
    log_path = csv_path.with_name(f"{csv_path.stem}.phone_requests.jsonl")
    if not args.include_previously_requested:
        previous_keys = _previously_requested_keys(log_path)
        preview = [c for c in preview if _contact_key(c) not in previous_keys]
    if args.limit:
        preview = preview[: args.limit]
    print(f"Requesting phone reveal for {len(preview)} contacts")
    print(f"Request log: {log_path}")

    request_phone_reveals_for_csv(
        csv_path,
        limit=args.limit,
        only_missing_phone=args.only_missing_phone,
        include_previously_requested=args.include_previously_requested,
        sleep=args.sleep,
        dry_run=args.dry_run,
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _first_field(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def normalize_phone_contact_aliases(row: dict[str, str]) -> dict[str, str]:
    """
    Map talent person columns onto company decision_maker_* fields.

    Talent T05/T06 uses email / apollo_person_id / full_name / person_id /
    phone / mobile_phone. Company CSVs use decision_maker_* names. One
    webhook request log path for both.
    """
    enriched = dict(row)
    email = _first_field(enriched, "decision_maker_email", "email")
    apollo_id = _first_field(
        enriched, "decision_maker_apollo_person_id", "apollo_person_id"
    )
    name = _first_field(enriched, "decision_maker_name", "full_name", "name")
    lead_id = _first_field(enriched, "lead_id", "person_id")
    direct = _first_field(enriched, "decision_maker_direct_phone", "phone")
    mobile = _first_field(enriched, "decision_maker_mobile_phone", "mobile_phone")
    linkedin = _first_field(
        enriched, "decision_maker_linkedin", "linkedin_url"
    )

    if email:
        enriched["decision_maker_email"] = email
    if apollo_id:
        enriched["decision_maker_apollo_person_id"] = apollo_id
    if name:
        enriched["decision_maker_name"] = name
    if lead_id:
        enriched["lead_id"] = lead_id
    if direct:
        enriched["decision_maker_direct_phone"] = direct
    if mobile:
        enriched["decision_maker_mobile_phone"] = mobile
    if linkedin:
        enriched["decision_maker_linkedin"] = linkedin
    return enriched


def _eligible_contacts(rows: list[dict[str, str]], only_missing_phone: bool) -> list[dict[str, str]]:
    contacts: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=2):  # Sheet row number: header is row 1.
        enriched = normalize_phone_contact_aliases(row)
        email = (enriched.get("decision_maker_email") or "").strip()
        person_id = (enriched.get("decision_maker_apollo_person_id") or "").strip()
        has_phone = bool(
            (enriched.get("decision_maker_direct_phone") or "").strip()
            or (enriched.get("decision_maker_mobile_phone") or "").strip()
        )
        if not email and not person_id:
            continue
        if only_missing_phone and has_phone:
            continue
        enriched["csv_row"] = str(index)
        contacts.append(enriched)
    return contacts


def _previously_requested_keys(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()
    keys: set[str] = set()
    with log_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("status") == "requested":
                keys.add(_request_event_key(event))
    return keys


def _contact_key(contact: dict[str, str]) -> str:
    return (
        (contact.get("decision_maker_apollo_person_id") or "").strip().lower()
        or (contact.get("decision_maker_email") or "").strip().lower()
        or f"row:{contact.get('csv_row')}"
    )


def _request_event_key(event: dict[str, Any]) -> str:
    return (
        (event.get("apollo_person_id") or "").strip().lower()
        or (event.get("email") or "").strip().lower()
        or f"row:{event.get('csv_row')}"
    )


def _build_params(contact: dict[str, str], webhook_url: str) -> list[tuple[str, str]]:
    params = [
        ("reveal_personal_emails", "false"),
        ("reveal_phone_number", "true"),
        ("webhook_url", webhook_url),
    ]
    person_id = (contact.get("decision_maker_apollo_person_id") or "").strip()
    email = (contact.get("decision_maker_email") or "").strip()
    name = (contact.get("decision_maker_name") or "").strip()
    if person_id:
        params.append(("id", person_id))
    elif email:
        params.append(("email", email))
    elif name:
        params.append(("name", name))
    return params


def request_phone_reveals_for_csv(
    csv_path: Path,
    *,
    limit: int | None = None,
    only_missing_phone: bool = True,
    include_previously_requested: bool = False,
    sleep: float = 1.0,
    dry_run: bool = False,
    api_key: str | None = None,
    webhook_url: str | None = None,
    http_client: Any | None = None,
) -> tuple[Path, int]:
    """
    Request Apollo phone reveals for rows in ``csv_path``.

    Returns (request_log_path, contacts_requested_count).
    Reused by company CLI and talent T06 — does not invent phone numbers.
    """
    key = api_key if api_key is not None else os.environ.get("APOLLO_API_KEY")
    hook = webhook_url if webhook_url is not None else resolve_apollo_phone_webhook_url()
    if not key:
        raise RuntimeError("APOLLO_API_KEY is not configured")
    if not hook:
        raise RuntimeError(
            "Set APOLLO_PHONE_WEBHOOK_URL or PUBLIC_BASE_URL + APOLLO_PHONE_WEBHOOK_SECRET"
        )

    path = Path(csv_path)
    rows = _read_csv(path)
    contacts = _eligible_contacts(rows, only_missing_phone)
    log_path = path.with_name(f"{path.stem}.phone_requests.jsonl")
    if not include_previously_requested:
        previous_keys = _previously_requested_keys(log_path)
        contacts = [c for c in contacts if _contact_key(c) not in previous_keys]
    if limit:
        contacts = contacts[:limit]

    own_client = http_client is None and not dry_run
    client = http_client
    if own_client:
        client = httpx.Client(timeout=30)

    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            for contact in contacts:
                params = _build_params(contact, hook)
                event: dict[str, Any] = {
                    "requested_at": datetime.now(UTC).isoformat(),
                    "csv_path": str(path),
                    "csv_row": contact["csv_row"],
                    "lead_id": contact.get("lead_id"),
                    "company_name": contact.get("company_name"),
                    "name": contact.get("decision_maker_name"),
                    "email": contact.get("decision_maker_email"),
                    "apollo_person_id": contact.get("decision_maker_apollo_person_id"),
                    "webhook_url": hook,
                    "dry_run": dry_run,
                }

                if dry_run:
                    event["status"] = "dry_run"
                else:
                    assert client is not None
                    try:
                        response = client.post(
                            f"{APOLLO_BASE}/people/match",
                            params=params,
                            headers={
                                "Content-Type": "application/json",
                                "Cache-Control": "no-cache",
                                "X-Api-Key": key,
                            },
                        )
                        event["status_code"] = response.status_code
                        event["response_preview"] = response.text[:1000]
                        if response.status_code >= 400:
                            event["status"] = "error"
                        else:
                            event["status"] = "requested"
                    except Exception as exc:
                        event["status"] = "exception"
                        event["error"] = str(exc)

                log_file.write(json.dumps(event, ensure_ascii=False) + "\n")
                print(
                    f"{event['status']}: row {event['csv_row']} | "
                    f"{event.get('company_name')} | {event.get('email')}"
                )
                if not dry_run:
                    time.sleep(sleep)
    finally:
        if own_client and client is not None:
            client.close()

    return log_path, len(contacts)


if __name__ == "__main__":
    main()
