"""
Apollo async phone reveal webhook — receive, persist, and export payloads.

Apollo POSTs phone enrichment results to APOLLO_PHONE_WEBHOOK_URL after
people/match with reveal_phone_number=true. Payloads are appended to a JSONL
file so import scripts can merge phones into lead CSVs.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

WEBHOOK_DIR = Path("data/webhooks/apollo_phone")
PAYLOADS_FILE = WEBHOOK_DIR / "payloads.jsonl"


def public_base_url() -> str | None:
    explicit = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    domain = (os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    if domain:
        return f"https://{domain}"
    return None


def resolve_apollo_phone_webhook_url() -> str | None:
    """Full webhook URL for Apollo reveal_phone_number callbacks."""
    explicit = (os.environ.get("APOLLO_PHONE_WEBHOOK_URL") or "").strip()
    if explicit:
        return explicit

    base = public_base_url()
    secret = (os.environ.get("APOLLO_PHONE_WEBHOOK_SECRET") or "").strip()
    if base and secret:
        return f"{base}/webhooks/apollo-phone/{secret}"
    return None


def verify_webhook_secret(secret: str) -> bool:
    expected = (os.environ.get("APOLLO_PHONE_WEBHOOK_SECRET") or "").strip()
    if not expected:
        return False
    return secret == expected


def store_payload(payload: dict[str, Any]) -> Path:
    WEBHOOK_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "received_at": datetime.now(UTC).isoformat(),
        "payload": payload,
    }
    with PAYLOADS_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return PAYLOADS_FILE


def load_payloads(*, since: str | None = None) -> list[dict[str, Any]]:
    if not PAYLOADS_FILE.exists():
        return []

    payloads: list[dict[str, Any]] = []
    with PAYLOADS_FILE.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            received_at = record.get("received_at")
            if since and isinstance(received_at, str) and received_at < since:
                continue
            payload = record.get("payload")
            if isinstance(payload, dict):
                payloads.append(payload)
    return payloads


def payload_stats() -> dict[str, Any]:
    payloads = load_payloads()
    with_phones = 0
    for payload in payloads:
        people = payload.get("people")
        if not isinstance(people, list):
            continue
        for person in people:
            if not isinstance(person, dict):
                continue
            phones = person.get("phone_numbers")
            if isinstance(phones, list) and phones:
                with_phones += 1
    return {
        "payload_count": len(payloads),
        "people_with_phones": with_phones,
        "storage_path": str(PAYLOADS_FILE),
        "webhook_url": resolve_apollo_phone_webhook_url(),
    }
