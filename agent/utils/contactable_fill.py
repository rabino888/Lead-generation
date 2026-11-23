"""Keep searching until a list reaches the contactable target from the builder."""
from __future__ import annotations

from urllib.parse import urlparse


def company_key(row: dict) -> str:
    website = (row.get("website") or "").strip()
    if website:
        parsed = urlparse(website if website.startswith("http") else f"https://{website}")
        host = parsed.netloc.lower().removeprefix("www.")
        if host:
            return f"d:{host}"
    name = (row.get("company_name") or "").strip().lower()
    return f"n:{name}" if name else ""


def unseen_rows(rows: list[dict], seen: set[str]) -> list[dict]:
    """Rows whose company is not already in seen. Adds kept keys to seen."""
    kept: list[dict] = []
    for row in rows:
        key = company_key(row)
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept


def refill_batch_size(target: int, have: int) -> int:
    """How many more companies to search while the list is still short."""
    gap = max(1, int(target) - int(have))
    return min(max(1, int(target)), max(25, gap * 4))
