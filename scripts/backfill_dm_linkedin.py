"""
Re-fetch Apollo decision-maker LinkedIn (and name/title) for contactable rows
missing decision_maker_linkedin. Reports Apollo credit usage.

Usage:
  python scripts/backfill_dm_linkedin.py --campaign automata_us_rnd
  python scripts/backfill_dm_linkedin.py --campaign automata_us_rnd --dry-run
  python scripts/backfill_dm_linkedin.py --campaign automata_us_rnd --limit 5
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.integrations.apollo import (
    enrich_person_by_email,
    get_credits_used,
    reset_credits_used,
)
from agent.models import InputMode
from agent.utils.cost_manifest import finalize_stage_cost
from agent.utils.cost_tracker import get_cost_tracker, init_cost_tracker
from agent.utils.run_tracker import create_run
from agent.utils.stage_runner import campaign_arg_load, load_client_profile


STAGE_KEYS = (
    "apollo_contactable",
    "enriched",
    "scored",
    "qa_flags",
)


def _stage_path(campaign_dir: Path, key: str) -> Path:
    # Prefer numbered stage files if present
    stages = campaign_dir / "stages"
    mapping = {
        "apollo_contactable": "04_apollo_contactable.csv",
        "enriched": "05_enriched.csv",
        "scored": "06_scored.csv",
        "qa_flags": "07_qa_flags.csv",
    }
    return stages / mapping[key]


def _read_rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    return fields, rows


def _write_rows(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bak = path.with_suffix(path.suffix + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    if path.exists():
        shutil.copy2(path, bak)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def _needs_li(row: dict) -> bool:
    email = (row.get("decision_maker_email") or "").strip()
    li = (row.get("decision_maker_linkedin") or "").strip()
    return bool(email) and not li


def _apply_contact(row: dict, contact) -> dict[str, str]:
    updates: dict[str, str] = {}
    if contact.linkedin_url and not (row.get("decision_maker_linkedin") or "").strip():
        updates["decision_maker_linkedin"] = contact.linkedin_url
    if contact.name and not (row.get("decision_maker_name") or "").strip():
        updates["decision_maker_name"] = contact.name
    if contact.title and not (row.get("decision_maker_title") or "").strip():
        updates["decision_maker_title"] = contact.title
    if contact.location and not (row.get("decision_maker_location") or "").strip():
        updates["decision_maker_location"] = contact.location
    if contact.apollo_person_id and not (row.get("apollo_person_id") or "").strip():
        updates["apollo_person_id"] = contact.apollo_person_id
    return updates


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill DM LinkedIn via Apollo email enrich")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--client-id", default=None)
    parser.add_argument("--limit", type=int, default=0, help="Max emails to enrich (0=all)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rebuild-cost-dashboard",
        action="store_true",
        help="Rebuild cost_dashboard.html after logging",
    )
    args = parser.parse_args()

    campaign = campaign_arg_load(args.campaign)
    client = load_client_profile(campaign, client_id_override=args.client_id)
    scored = _stage_path(campaign.campaign_dir, "scored")
    if not scored.exists():
        scored = _stage_path(campaign.campaign_dir, "enriched")
    if not scored.exists():
        print("ERROR: need 06_scored.csv or 05_enriched.csv", file=sys.stderr)
        return 2

    fields, rows = _read_rows(scored)
    missing = [r for r in rows if _needs_li(r)]
    if args.limit:
        missing = missing[: args.limit]

    print(f"Source: {scored}")
    print(f"Rows missing DM LinkedIn (with email): {len(missing)} / {len(rows)}")
    if args.dry_run:
        for r in missing[:20]:
            print(f"  - {r.get('company_name')}: {r.get('decision_maker_email')}")
        if len(missing) > 20:
            print(f"  ... +{len(missing) - 20} more")
        return 0

    if not missing:
        print("Nothing to backfill.")
        return 0

    started = datetime.now(timezone.utc)
    run_state = create_run(
        client_id=client.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=len(missing),
        keyword=f"backfill_dm_linkedin:{campaign.campaign_id}",
    )
    run_id = run_state.run_id
    init_cost_tracker(run_id, client.client_id)
    reset_credits_used()

    # Deduplicate by email so we don't double-bill the same person
    by_email: dict[str, list[dict]] = {}
    for r in missing:
        email = (r.get("decision_maker_email") or "").strip().lower()
        by_email.setdefault(email, []).append(r)

    results: dict[str, dict] = {}
    filled = 0
    failed = 0
    for email, group in by_email.items():
        contact = enrich_person_by_email(email)
        if not contact:
            failed += 1
            results[email] = {"ok": False}
            continue
        updates = {}
        for row in group:
            # Apply onto the shared row objects in `rows` via identity — group refs are same dicts
            u = _apply_contact(row, contact)
            for k, v in u.items():
                row[k] = v
            updates.update(u)
        if updates.get("decision_maker_linkedin"):
            filled += 1
        results[email] = {
            "ok": True,
            "linkedin": contact.linkedin_url,
            "name": contact.name,
            "title": contact.title,
            "updates": updates,
        }

    credits = get_credits_used()
    print(f"Apollo credits used this session: {credits}")
    print(f"Emails enriched with LinkedIn URL: {filled} / {len(by_email)} (failed={failed})")

    # Propagate updates into all stage CSVs keyed by email
    email_updates = {
        email: data["updates"]
        for email, data in results.items()
        if data.get("ok") and data.get("updates")
    }
    for key in STAGE_KEYS:
        path = _stage_path(campaign.campaign_dir, key)
        if not path.exists():
            continue
        f2, rows2 = _read_rows(path)
        changed = 0
        for row in rows2:
            email = (row.get("decision_maker_email") or "").strip().lower()
            upd = email_updates.get(email)
            if not upd:
                continue
            for k, v in upd.items():
                if not (row.get(k) or "").strip():
                    row[k] = v
                    changed += 1
            # Always set linkedin if we recovered it and row still blank
            li = upd.get("decision_maker_linkedin")
            if li and not (row.get("decision_maker_linkedin") or "").strip():
                row["decision_maker_linkedin"] = li
        for col in (
            "decision_maker_linkedin",
            "decision_maker_name",
            "decision_maker_title",
            "decision_maker_location",
            "apollo_person_id",
        ):
            if col not in f2:
                f2.append(col)
        _write_rows(path, f2, rows2)
        print(f"Updated {path.name} (field writes~={changed})")

    tracker = get_cost_tracker()
    summary = tracker.run_summary() if tracker else None
    report_path, manifest_path = finalize_stage_cost(
        campaign_dir=campaign.campaign_dir,
        campaign_id=campaign.campaign_id,
        client_id=client.client_id,
        run_id=run_id,
        phase="backfill_dm_linkedin",
        started_at=started,
        tracker_summary=summary,
        label=f"Apollo DM LinkedIn backfill ({filled} filled)",
        outcome={"emails_attempted": len(by_email), "linkedin_filled": filled, "failed": failed},
        extra={
            "apollo_credits_session": credits,
            "emails_attempted": len(by_email),
            "linkedin_filled": filled,
            "failed": failed,
            "results": {
                e: {k: v for k, v in d.items() if k != "updates"}
                for e, d in results.items()
            },
        },
        apollo_extra={
            "emails_attempted": len(by_email),
            "linkedin_filled": filled,
            "credits_consumed": credits,
            "note": "people/match by email for missing DM LinkedIn URLs",
        },
        rebuild_dashboard=args.rebuild_cost_dashboard,
    )
    print(f"Cost report (incl. Apify window reconcile): {report_path}")
    print(f"Cost manifest: {manifest_path}")
    print(f"run_id: {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
