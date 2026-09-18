"""
Import Apollo phone webhook payloads into a lead CSV and optional Google Sheet.

Inputs can be:
- a JSON file exported/copied from webhook.site, or
- live webhook.site requests fetched from APOLLO_PHONE_WEBHOOK_URL, or
- payloads stored on Railway via GET /admin/webhooks/apollo-phone, or
- local data/webhooks/apollo_phone/payloads.jsonl from a Railway volume/sync.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to lead CSV to update")
    parser.add_argument("--payload-json", help="Webhook.site export or copied payload JSON")
    parser.add_argument("--fetch-webhook-site", action="store_true")
    parser.add_argument("--fetch-railway", action="store_true", help="Fetch payloads from deployed Railway app")
    parser.add_argument("--fetch-local", action="store_true", help="Read data/webhooks/apollo_phone/payloads.jsonl")
    parser.add_argument("--sheet-url", help="Optional Google Sheet URL to update")
    parser.add_argument("--output", help="Optional output CSV path; defaults to overwriting --csv")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv(".env", override=True)

    payloads = _load_payloads(
        args.payload_json,
        args.fetch_webhook_site,
        args.fetch_railway,
        args.fetch_local,
    )
    phone_records = [_extract_phone_record(payload) for payload in payloads]
    phone_records = [record for record in phone_records if record]
    print(f"Loaded {len(payloads)} webhook payloads; extracted {len(phone_records)} phone records")

    csv_path = Path(args.csv)
    rows, fieldnames = _read_csv(csv_path)
    rows, fieldnames = _apply_request_log_person_ids(csv_path, rows, fieldnames)
    updated_rows = _merge_phone_records(rows, phone_records)
    updated_count = sum(1 for row in updated_rows if row.pop("_phone_updated", "") == "1")

    output_path = Path(args.output) if args.output else csv_path
    if args.dry_run:
        print(f"Dry run: would update {updated_count} CSV rows")
    else:
        _write_csv(output_path, fieldnames, updated_rows)
        print(f"Updated {updated_count} CSV rows: {output_path}")
        if args.sheet_url and updated_count:
            _update_google_sheet(args.sheet_url, updated_rows, fieldnames)


def _load_payloads(
    payload_json: str | None,
    fetch_webhook_site: bool,
    fetch_railway: bool,
    fetch_local: bool,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    if payload_json:
        payloads.extend(_normalize_loaded_json(json.loads(Path(payload_json).read_text(encoding="utf-8"))))
    if fetch_webhook_site:
        payloads.extend(_fetch_webhook_site_payloads())
    if fetch_railway:
        payloads.extend(_fetch_railway_payloads())
    if fetch_local:
        payloads.extend(_fetch_local_payloads())
    if not payloads:
        raise RuntimeError(
            "Provide --payload-json, --fetch-webhook-site, --fetch-railway, or --fetch-local"
        )
    return payloads


def _normalize_loaded_json(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [_unwrap_webhook_site_request(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        if isinstance(raw.get("data"), list):
            return [_unwrap_webhook_site_request(item) for item in raw["data"] if isinstance(item, dict)]
        if isinstance(raw.get("requests"), list):
            return [_unwrap_webhook_site_request(item) for item in raw["requests"] if isinstance(item, dict)]
        return [_unwrap_webhook_site_request(raw)]
    raise RuntimeError("Unsupported webhook JSON format")


def _fetch_webhook_site_payloads() -> list[dict[str, Any]]:
    webhook_url = os.environ.get("APOLLO_PHONE_WEBHOOK_URL")
    if not webhook_url:
        raise RuntimeError("APOLLO_PHONE_WEBHOOK_URL is not configured")
    token = _webhook_site_token(webhook_url)
    if not token:
        raise RuntimeError("Could not parse webhook.site token from APOLLO_PHONE_WEBHOOK_URL")

    api_url = f"https://webhook.site/token/{token}/requests"
    with httpx.Client(timeout=30) as client:
        response = client.get(api_url)
        response.raise_for_status()
    return _normalize_loaded_json(response.json())


def _fetch_railway_payloads() -> list[dict[str, Any]]:
    from agent.webhooks.apollo_phone import public_base_url

    base_url = public_base_url()
    admin_secret = os.environ.get("ADMIN_SECRET")
    if not base_url:
        raise RuntimeError("PUBLIC_BASE_URL or RAILWAY_PUBLIC_DOMAIN is not configured")
    if not admin_secret:
        raise RuntimeError("ADMIN_SECRET is not configured")

    api_url = f"{base_url.rstrip('/')}/admin/webhooks/apollo-phone"
    with httpx.Client(timeout=60) as client:
        response = client.get(api_url, headers={"X-Admin-Secret": admin_secret})
        response.raise_for_status()
    data = response.json()
    payloads = data.get("payloads")
    if not isinstance(payloads, list):
        raise RuntimeError("Unexpected Railway webhook response")
    return [payload for payload in payloads if isinstance(payload, dict)]


def _fetch_local_payloads() -> list[dict[str, Any]]:
    from agent.webhooks.apollo_phone import load_payloads

    return load_payloads()


def _webhook_site_token(webhook_url: str) -> str | None:
    parsed = urlparse(webhook_url)
    if parsed.netloc.lower() != "webhook.site":
        return None
    token = parsed.path.strip("/").split("/")[0]
    return token or None


def _unwrap_webhook_site_request(item: dict[str, Any]) -> dict[str, Any]:
    content = item.get("content")
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"raw_content": content}
    if isinstance(content, dict):
        return content
    return item


def _extract_phone_record(payload: dict[str, Any]) -> dict[str, str] | None:
    person = _find_person(payload)
    if not person:
        return None

    direct_phone = _first_nonempty(
        person.get("direct_phone"),
        person.get("phone"),
        person.get("phone_number"),
        person.get("sanitized_phone"),
    )
    mobile_phone = _first_nonempty(
        person.get("mobile_phone"),
        person.get("mobile"),
        person.get("personal_phone"),
        person.get("sanitized_mobile_phone"),
    )
    phones = person.get("phone_numbers")
    if isinstance(phones, list):
        for phone in phones:
            if not isinstance(phone, dict):
                continue
            value = _first_nonempty(phone.get("sanitized_number"), phone.get("raw_number"), phone.get("number"))
            phone_type = (phone.get("type") or "").lower()
            if "mobile" in phone_type and not mobile_phone:
                mobile_phone = value
            elif not direct_phone:
                direct_phone = value

    if not direct_phone and not mobile_phone:
        return None

    return {
        "apollo_person_id": str(_first_nonempty(person.get("id"), person.get("apollo_person_id")) or ""),
        "email": str(_first_nonempty(person.get("email"), person.get("personal_email")) or ""),
        "linkedin_url": str(_first_nonempty(person.get("linkedin_url"), person.get("linkedin")) or ""),
        "name": str(_first_nonempty(person.get("name"), _join_name(person)) or ""),
        "direct_phone": str(direct_phone or ""),
        "mobile_phone": str(mobile_phone or ""),
    }


def _find_person(payload: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("person", "contact", "matched_person"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    people = payload.get("people")
    if isinstance(people, list) and people and isinstance(people[0], dict):
        return people[0]
    if any(key in payload for key in ("email", "phone_numbers", "direct_phone", "mobile_phone")):
        return payload
    return None


def _join_name(person: dict[str, Any]) -> str:
    return " ".join(
        part for part in [str(person.get("first_name") or ""), str(person.get("last_name") or "")] if part
    )


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value:
            return value
    return None


def _first_field(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    # Company decision_maker_* + talent person-oriented phone columns.
    for column in (
        "decision_maker_direct_phone",
        "decision_maker_mobile_phone",
        "phone",
        "mobile_phone",
    ):
        if column not in fieldnames:
            fieldnames.append(column)
    return rows, fieldnames


def _apply_request_log_person_ids(
    csv_path: Path,
    rows: list[dict[str, str]],
    fieldnames: list[str],
) -> tuple[list[dict[str, str]], list[str]]:
    log_path = csv_path.with_name(f"{csv_path.stem}.phone_requests.jsonl")
    if not log_path.exists():
        return rows, fieldnames

    ids_by_email: dict[str, str] = {}
    ids_by_row: dict[str, str] = {}
    with log_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            person_id = _request_person_id(event)
            if not person_id:
                continue
            if event.get("email"):
                ids_by_email[_clean(event.get("email"))] = person_id
            if event.get("csv_row"):
                ids_by_row[str(event["csv_row"])] = person_id

    for index, row in enumerate(rows, start=2):
        if _first_field(row, "decision_maker_apollo_person_id", "apollo_person_id"):
            continue
        person_id = ids_by_email.get(
            _clean(_first_field(row, "decision_maker_email", "email"))
        ) or ids_by_row.get(str(index))
        if person_id:
            row["decision_maker_apollo_person_id"] = person_id
            # Talent rows use apollo_person_id without the decision_maker_ prefix.
            if "apollo_person_id" in row or "person_id" in row or "email" in row:
                if not (row.get("apollo_person_id") or "").strip():
                    row["apollo_person_id"] = person_id

    if "decision_maker_apollo_person_id" not in fieldnames:
        fieldnames.insert(_person_id_column_index(fieldnames), "decision_maker_apollo_person_id")
    if "apollo_person_id" not in fieldnames and any(
        "email" in (fn or "") or fn == "person_id" for fn in fieldnames
    ):
        # Keep talent CSVs self-describing when backfilling from the request log.
        if "email" in fieldnames or "person_id" in fieldnames:
            if "apollo_person_id" not in fieldnames:
                fieldnames.append("apollo_person_id")
    return rows, fieldnames


def _request_person_id(event: dict[str, Any]) -> str:
    if event.get("apollo_person_id"):
        return str(event["apollo_person_id"])
    preview = event.get("response_preview") or ""
    if not isinstance(preview, str):
        return ""
    match = re.search(r'"person"\s*:\s*\{\s*"id"\s*:\s*"([^"]+)"', preview)
    return match.group(1) if match else ""


def _person_id_column_index(fieldnames: list[str]) -> int:
    for name in ("decision_maker_name", "full_name", "name"):
        try:
            return fieldnames.index(name)
        except ValueError:
            continue
    return len(fieldnames)


def _merge_phone_records(rows: list[dict[str, str]], records: list[dict[str, str]]) -> list[dict[str, str]]:
    for row in rows:
        match = _match_record(row, records)
        if not match:
            row["_phone_updated"] = ""
            continue
        if match.get("direct_phone"):
            row["decision_maker_direct_phone"] = match["direct_phone"]
            row["phone"] = match["direct_phone"]
        if match.get("mobile_phone"):
            row["decision_maker_mobile_phone"] = match["mobile_phone"]
            row["mobile_phone"] = match["mobile_phone"]
        row["_phone_updated"] = "1"
    return rows


def _match_record(row: dict[str, str], records: list[dict[str, str]]) -> dict[str, str] | None:
    row_keys = {
        "apollo_person_id": _clean(
            _first_field(row, "decision_maker_apollo_person_id", "apollo_person_id")
        ),
        "email": _clean(_first_field(row, "decision_maker_email", "email")),
        "linkedin_url": _clean_url(
            _first_field(row, "decision_maker_linkedin", "linkedin_url")
        ),
        "name": _clean(_first_field(row, "decision_maker_name", "full_name", "name")),
    }
    for record in records:
        if row_keys["apollo_person_id"] and row_keys["apollo_person_id"] == _clean(record.get("apollo_person_id")):
            return record
        if row_keys["email"] and row_keys["email"] == _clean(record.get("email")):
            return record
        if row_keys["linkedin_url"] and row_keys["linkedin_url"] == _clean_url(record.get("linkedin_url")):
            return record
        if row_keys["name"] and row_keys["name"] == _clean(record.get("name")):
            return record
    return None


def _clean(value: str | None) -> str:
    return (value or "").strip().lower()


def _clean_url(value: str | None) -> str:
    cleaned = _clean(value)
    cleaned = re.sub(r"^https?://", "", cleaned)
    return cleaned.rstrip("/")


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _update_google_sheet(sheet_url: str, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    from agent.integrations.sheets import _get_gspread_client

    client = _get_gspread_client()
    spreadsheet = client.open_by_url(sheet_url)
    worksheet = spreadsheet.worksheet("Leads")
    worksheet.update([fieldnames] + [[row.get(field, "") for field in fieldnames] for row in rows])
    print(f"Updated Google Sheet: {sheet_url}")


if __name__ == "__main__":
    main()
