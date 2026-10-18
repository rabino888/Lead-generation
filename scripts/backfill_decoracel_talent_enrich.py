"""One-shot: re-enrich Decoracel T05 contactable work history + publish Sheet."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.utils.talent_people import (
    STAGE_T04_PROFILES,
    STAGE_T05_APOLLO_CONTACTABLE,
    T01_FIELDS,
    T04_PROFILE_FIELDS,
    talent_stage_path,
)
from agent.utils.talent_profile import enrich_person_row
from agent.utils.talent_sheets import publish_talent_sheet

CAMP_ID = "decoracel_property_managers_in_marbella_new_list_3"
CAMP = (
    ROOT
    / "data"
    / "clients"
    / "decoracel"
    / "campaigns"
    / "in_progress"
    / CAMP_ID
)


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    base = list(T01_FIELDS) + list(T04_PROFILE_FIELDS)
    extra = [k for k in rows[0].keys() if k not in base]
    fields = base + extra
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def main() -> int:
    t05_path = talent_stage_path(CAMP, STAGE_T05_APOLLO_CONTACTABLE)
    t04_path = talent_stage_path(CAMP, STAGE_T04_PROFILES)
    with t05_path.open(encoding="utf-8", newline="") as fh:
        t05 = list(csv.DictReader(fh))
    t04_by_id: dict[str, dict[str, str]] = {}
    if t04_path.is_file():
        with t04_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                pid = (row.get("person_id") or "").strip()
                if pid:
                    t04_by_id[pid] = dict(row)

    print(f"Re-enriching {len(t05)} contactable profiles…")
    updated: list[dict[str, str]] = []
    with_exp = 0
    for i, row in enumerate(t05, 1):
        name = row.get("full_name") or row.get("person_id") or "?"
        print(f"  [{i}/{len(t05)}] {name}")
        enriched = enrich_person_row(row)
        # Keep Apollo email fields from original
        for k, v in row.items():
            if k.startswith("apollo") or k in ("email", "email_status", "reject_reason"):
                if v:
                    enriched[k] = v
        exp = (enriched.get("experience_summary") or "").strip()
        cur = (enriched.get("current_position") or "").strip()
        if exp or cur:
            with_exp += 1
        print(
            f"      pos={cur[:60]!r} exp_len={len(exp)} status={enriched.get('profile_status')}"
        )
        updated.append(enriched)
        pid = (enriched.get("person_id") or "").strip()
        if pid and pid in t04_by_id:
            t04_by_id[pid].update(
                {
                    k: enriched.get(k, "")
                    for k in (
                        "about",
                        "current_position",
                        "experience_summary",
                        "education_summary",
                        "profile_raw_chars",
                        "profile_scraped_at_utc",
                        "profile_status",
                        "headline",
                        "linkedin_url",
                    )
                }
            )

    _write(t05_path, updated)
    if t04_by_id:
        _write(t04_path, list(t04_by_id.values()))
    print(f"Wrote T05 ({len(updated)}) with work history on {with_exp}/{len(updated)}")

    meta = publish_talent_sheet(CAMP, CAMP_ID, update=True)
    print("Sheet:", meta.get("sheet_url"))

    from agent.utils.cost_dashboard import build_dashboard_index, persist_dashboard_index

    idx = build_dashboard_index(ROOT / "data")
    persist_dashboard_index(ROOT / "data", idx)
    print("Dashboard index rebuilt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
