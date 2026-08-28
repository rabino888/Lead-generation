"""
Backfill LinkedIn enrichment on existing run CSVs (posts, about, hiring).

Usage:
  python scripts/backfill_linkedin_enrichment.py --campaign {campaign_id}
  python scripts/backfill_linkedin_enrichment.py --campaign {campaign_id} --csv data/clients/{client_id}/runs/run_xxx.csv --limit 3
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

from agent.integrations.apify import enrich_company_linkedin, enrich_decision_maker_linkedin
from agent.integrations.apollo import _extract_domain
from agent.utils.concurrency import env_int, map_ordered
from agent.utils.logger import log

STATE_PATH: Path  # set from --campaign in main()


def _set_campaign_state_path(campaign: str) -> None:
    global STATE_PATH
    STATE_PATH = ROOT / "data" / "campaigns" / campaign / "linkedin_backfill_state.json"

NEW_COLUMNS = [
    "decision_maker_linkedin_about",
    "decision_maker_linkedin_headline",
    "decision_maker_is_hiring",
    "company_linkedin_description",
    "company_is_hiring",
    "company_open_jobs_count",
    "company_open_jobs_summary",
]


def _load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"profiles": {}, "companies": {}, "completed_leads": []}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(UTC).isoformat()
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _read_csv_rows(csv_path: Path) -> tuple[list[str], list[dict]]:
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [r for r in reader if r.get("lead_id") and not str(r["lead_id"]).startswith("──")]
    return fieldnames, rows


def _ensure_columns(fieldnames: list[str]) -> list[str]:
    out = list(fieldnames)
    for col in NEW_COLUMNS:
        if col not in out:
            idx = out.index("decision_maker_linkedin_post_summary") + 1 if "decision_maker_linkedin_post_summary" in out else len(out)
            out.insert(idx, col)
    return out


def _company_key(row: dict) -> str:
    website = row.get("website") or ""
    domain = _extract_domain(website) if website else ""
    return domain or (row.get("company_name") or "").lower().strip()


def _profile_key(row: dict) -> str:
    url = (row.get("decision_maker_linkedin") or "").strip().lower().rstrip("/")
    return url or row.get("lead_id", "")


def _get_person_enrichment(state: dict, row: dict) -> dict:
    key = _profile_key(row)
    if key in state["profiles"]:
        return state["profiles"][key]
    url = row.get("decision_maker_linkedin")
    if not url:
        return {}
    data = enrich_decision_maker_linkedin(url, max_posts=5)
    state["profiles"][key] = data
    return data


def _get_company_enrichment(state: dict, row: dict) -> dict:
    key = _company_key(row)
    if not key:
        return {}
    if key in state["companies"]:
        return state["companies"][key]
    data = enrich_company_linkedin(
        row.get("company_name", ""),
        website=row.get("website") or None,
        company_linkedin_url=row.get("company_linkedin") or None,
    )
    state["companies"][key] = data
    return data


def _apply_enrichment(row: dict, person: dict, company: dict) -> dict:
    row = dict(row)
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
    return row


def backfill_csv(csv_path: Path, state: dict, limit: int | None = None, force: bool = False) -> int:
    fieldnames, rows = _read_csv_rows(csv_path)
    fieldnames = _ensure_columns(fieldnames)
    completed = set(state.get("completed_leads", []))
    pending = [r for r in rows if force or r["lead_id"] not in completed]
    if limit:
        pending = pending[:limit]

    if not pending:
        log.info("No pending leads in %s", csv_path.name)
        return 0

    concurrency = env_int("LINKEDIN_BACKFILL_CONCURRENCY", default=1)
    log.info("Backfilling %d leads in %s (concurrency=%d)", len(pending), csv_path.name, concurrency)

    def _process(row: dict) -> dict:
        person = _get_person_enrichment(state, row)
        company = _get_company_enrichment(state, row)
        enriched = _apply_enrichment(row, person, company)
        if enriched["lead_id"] not in completed:
            state.setdefault("completed_leads", []).append(enriched["lead_id"])
        _save_state(state)
        return enriched

    enriched_rows = map_ordered(pending, _process, concurrency=concurrency, label=f"LinkedIn backfill {csv_path.stem}")

    enriched_by_id = {r["lead_id"]: r for r in enriched_rows}
    final_rows = [enriched_by_id.get(r["lead_id"], r) for r in rows]

    backup = csv_path.with_suffix(csv_path.suffix + ".pre_linkedin.bak")
    if not backup.exists():
        shutil.copy2(csv_path, backup)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(final_rows)

    log.info("Updated %s (%d leads enriched)", csv_path, len(enriched_rows))
    return len(enriched_rows)


def _campaign_csvs(campaign: str) -> list[Path]:
    state_path = ROOT / "data" / "campaigns" / campaign / "batch_state.json"
    if not state_path.exists():
        raise FileNotFoundError(f"Missing campaign state: {state_path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    paths: list[Path] = []
    for info in state.get("segments", {}).values():
        if info.get("status") != "complete":
            continue
        csv_path = ROOT / info["csv_path"]
        if csv_path.exists():
            paths.append(csv_path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill LinkedIn enrichment on run CSVs")
    parser.add_argument("--campaign", required=True, help="Campaign id from batch_state.json")
    parser.add_argument("--csv", help="Single CSV path to backfill")
    parser.add_argument("--limit", type=int, help="Max leads per CSV (for testing)")
    parser.add_argument("--force", action="store_true", help="Re-enrich leads already marked complete")
    args = parser.parse_args()

    _set_campaign_state_path(args.campaign)
    state = _load_state()
    csv_paths = [ROOT / args.csv] if args.csv else _campaign_csvs(args.campaign)

    total = 0
    for csv_path in csv_paths:
        if not csv_path.exists():
            log.warning("Skipping missing CSV: %s", csv_path)
            continue
        total += backfill_csv(csv_path, state, limit=args.limit, force=args.force)

    print(f"\nLinkedIn backfill complete — {total} leads enriched across {len(csv_paths)} CSV(s).")
    print(f"State file: {STATE_PATH}")


if __name__ == "__main__":
    main()
