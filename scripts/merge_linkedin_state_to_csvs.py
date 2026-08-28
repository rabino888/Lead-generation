"""
Merge cached LinkedIn enrichment from linkedin_backfill_state.json into run CSVs.

Use when backfill was stopped mid-file: Apify data is in the state file but
never flushed to disk (CSV writes happen per file, not per lead).

Usage:
  python scripts/merge_linkedin_state_to_csvs.py --campaign {campaign_id}
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.integrations.apollo import _extract_domain

NEW_COLUMNS = [
    "decision_maker_linkedin_interests",
    "decision_maker_linkedin_post_summary",
    "decision_maker_linkedin_about",
    "decision_maker_linkedin_headline",
    "decision_maker_is_hiring",
    "company_linkedin",
    "company_linkedin_description",
    "company_is_hiring",
    "company_open_jobs_count",
    "company_open_jobs_summary",
]


def _profile_key(url: str) -> str:
    return (url or "").strip().lower().rstrip("/")


def _company_key(website: str, company_name: str) -> str:
    domain = _extract_domain(website) if website else ""
    return domain or company_name.lower().strip()


def _ensure_columns(fieldnames: list[str]) -> list[str]:
    out = list(fieldnames)
    for col in NEW_COLUMNS:
        if col not in out:
            out.append(col)
    return out


def _apply_person(row: dict, person: dict) -> None:
    if person.get("about"):
        row["decision_maker_linkedin_about"] = person["about"]
    if person.get("headline"):
        row["decision_maker_linkedin_headline"] = person["headline"]
    if person.get("is_hiring"):
        row["decision_maker_is_hiring"] = person["is_hiring"]
    if person.get("interests"):
        row["decision_maker_linkedin_interests"] = "; ".join(person["interests"])
    if person.get("post_summary"):
        row["decision_maker_linkedin_post_summary"] = person["post_summary"]


def _apply_company(row: dict, company: dict) -> None:
    if company.get("linkedin_url"):
        row["company_linkedin"] = company["linkedin_url"]
    if company.get("description"):
        row["company_linkedin_description"] = company["description"]
    if company.get("employee_count") and not row.get("company_size"):
        row["company_size"] = str(company["employee_count"])
    if company.get("is_hiring"):
        row["company_is_hiring"] = company["is_hiring"]
    if company.get("open_jobs_count") is not None:
        row["company_open_jobs_count"] = str(company["open_jobs_count"])
    if company.get("open_jobs_summary"):
        row["company_open_jobs_summary"] = company["open_jobs_summary"]


def merge_csv(csv_path: Path, state: dict) -> int:
    profiles = state.get("profiles", {})
    companies = state.get("companies", {})

    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = _ensure_columns(list(reader.fieldnames or []))
        rows = [r for r in reader if r.get("lead_id") and not str(r["lead_id"]).startswith("──")]

    updated = 0
    for row in rows:
        url = row.get("decision_maker_linkedin") or ""
        person = profiles.get(_profile_key(url))
        company = companies.get(_company_key(row.get("website", ""), row.get("company_name", "")))
        if not person and not company:
            continue
        before = (
            row.get("decision_maker_linkedin_about"),
            row.get("company_linkedin_description"),
        )
        if person:
            _apply_person(row, person)
        if company:
            _apply_company(row, company)
        after = (
            row.get("decision_maker_linkedin_about"),
            row.get("company_linkedin_description"),
        )
        if after != before:
            updated += 1

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", required=True, help="Campaign id under data/campaigns/")
    args = parser.parse_args()

    state_path = ROOT / "data" / "campaigns" / args.campaign / "linkedin_backfill_state.json"
    batch_path = ROOT / "data" / "campaigns" / args.campaign / "batch_state.json"
    if not state_path.exists():
        raise FileNotFoundError(f"Missing {state_path}")

    state = json.loads(state_path.read_text(encoding="utf-8"))
    batch = json.loads(batch_path.read_text(encoding="utf-8"))

    total = 0
    for segment, info in batch.get("segments", {}).items():
        csv_path = ROOT / info["csv_path"]
        if not csv_path.exists():
            print(f"SKIP {segment}: missing {csv_path}")
            continue
        n = merge_csv(csv_path, state)
        total += n
        print(f"{segment}: merged LinkedIn data into {n} rows ({csv_path.name})")

    print(f"\nDone — {total} rows updated across canonical CSVs.")
    print(f"State cache: {len(state.get('profiles', {}))} profiles, {len(state.get('companies', {}))} companies")


if __name__ == "__main__":
    main()
