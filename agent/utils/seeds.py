"""
Curated seed CSV import.
Loads company_name + website seed rows from a CSV into CompanySeed models,
so web-research sessions that end in a spreadsheet can feed curated_seeds runs
directly — no hand-built JSON payloads.

Header matching is case/spacing-insensitive and accepts common aliases
(e.g. "name"/"company" for company_name, "url"/"domain" for website).
"""
from __future__ import annotations

import csv
from pathlib import Path

from agent.models import CompanySeed

# Canonical CompanySeed field -> accepted CSV header aliases (normalized form).
COLUMN_ALIASES: dict[str, list[str]] = {
    "company_name": ["company_name", "name", "company", "empresa"],
    "website": ["website", "url", "domain", "web", "site"],
    "industry": ["industry", "sector"],
    "location": ["location", "geo", "geo_segment", "city", "country"],
    "company_size": ["company_size", "size", "employees"],
    "company_linkedin_url": ["company_linkedin_url", "company_linkedin", "linkedin_url", "linkedin"],
    "source_url": ["source_url", "source"],
    "notes": ["notes", "note", "comments"],
}


def _normalize_header(header: str) -> str:
    return header.strip().lower().replace(" ", "_").replace("-", "_")


def _build_column_map(headers: list[str]) -> dict[str, str]:
    """Map canonical field name -> actual CSV header for this file."""
    normalized = {_normalize_header(h): h for h in headers if h}
    column_map: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                column_map[field] = normalized[alias]
                break
    return column_map


def load_company_seeds_csv(path: str | Path) -> list[CompanySeed]:
    """
    Read a seed CSV and return CompanySeed records.
    Rows without a company name are skipped. Duplicate handling is left to the
    discovery stage, which already dedupes by domain and name.

    Raises FileNotFoundError / ValueError on unusable files.
    """
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Seed CSV not found: {csv_path}")

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        column_map = _build_column_map(headers)

        if "company_name" not in column_map:
            raise ValueError(
                f"Seed CSV {csv_path} has no company name column. "
                f"Expected one of: {', '.join(COLUMN_ALIASES['company_name'])}. "
                f"Found headers: {', '.join(headers)}"
            )

        seeds: list[CompanySeed] = []
        for row in reader:
            values = {
                field: (row.get(header) or "").strip() or None
                for field, header in column_map.items()
            }
            if not values.get("company_name"):
                continue
            seeds.append(CompanySeed(**values))

    return seeds
