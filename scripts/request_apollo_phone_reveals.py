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
    api_key = os.environ.get("APOLLO_API_KEY")
    webhook_url = resolve_apollo_phone_webhook_url()
    if not api_key:
        raise RuntimeError("APOLLO_API_KEY is not configured")
    if not webhook_url:
        raise RuntimeError(
            "Set APOLLO_PHONE_WEBHOOK_URL or PUBLIC_BASE_URL + APOLLO_PHONE_WEBHOOK_SECRET"
        )

    csv_path = Path(args.csv)
    rows = _read_csv(csv_path)
    contacts = _eligible_contacts(rows, args.only_missing_phone)
    log_path = csv_path.with_name(f"{csv_path.stem}.phone_requests.jsonl")
    if not args.include_previously_requested:
        previous_keys = _previously_requested_keys(log_path)
        contacts = [contact for contact in contacts if _contact_key(contact) not in previous_keys]
    if args.limit:
        contacts = contacts[: args.limit]

    print(f"Requesting phone reveal for {len(contacts)} contacts")
    print(f"Request log: {log_path}")

    with httpx.Client(timeout=30) as client, log_path.open("a", encoding="utf-8") as log_file:
        for contact in contacts:
            params = _build_params(contact, webhook_url)
            event: dict[str, Any] = {
                "requested_at": datetime.now(UTC).isoformat(),
                "csv_path": str(csv_path),
                "csv_row": contact["csv_row"],
                "lead_id": contact.get("lead_id"),
                "company_name": contact.get("company_name"),
                "name": contact.get("decision_maker_name"),
                "email": contact.get("decision_maker_email"),
                "apollo_person_id": contact.get("decision_maker_apollo_person_id"),
                "webhook_url": webhook_url,
                "dry_run": args.dry_run,
            }

            if args.dry_run:
                event["status"] = "dry_run"
            else:
                try:
                    response = client.post(
                        f"{APOLLO_BASE}/people/match",
                        params=params,
                        headers={
                            "Content-Type": "application/json",
                            "Cache-Control": "no-cache",
                            "X-Api-Key": api_key,
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
            if not args.dry_run:
                time.sleep(args.sleep)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _eligible_contacts(rows: list[dict[str, str]], only_missing_phone: bool) -> list[dict[str, str]]:
    contacts: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=2):  # Sheet row number: header is row 1.
        email = (row.get("decision_maker_email") or "").strip()
        person_id = (row.get("decision_maker_apollo_person_id") or "").strip()
        has_phone = bool(
            (row.get("decision_maker_direct_phone") or "").strip()
            or (row.get("decision_maker_mobile_phone") or "").strip()
        )
        if not email and not person_id:
            continue
        if only_missing_phone and has_phone:
            continue
        enriched = dict(row)
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


if __name__ == "__main__":
    main()
