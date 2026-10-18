"""Backfill outreach_language on talent T05 (and T04) from existing profile text.

No paid API calls — uses LinkedIn languages when present, else langdetect on
about/headline/experience.

Usage:
  python scripts/backfill_outreach_language.py --campaign decoracel_real_estate_advisors_in_marbella_new_lis_2
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.utils.campaign_config import load_campaign
from agent.utils.outreach_language import infer_outreach_language_from_row
from agent.utils.talent_people import (
    STAGE_T04_PROFILES,
    STAGE_T05_APOLLO_CONTACTABLE,
    T01_FIELDS,
    T04_PROFILE_FIELDS,
    talent_stage_path,
)
from agent.utils.talent_sheets import publish_talent_sheet


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    base = list(T01_FIELDS) + list(T04_PROFILE_FIELDS)
    extra = [k for k in rows[0].keys() if k not in base]
    # Prefer apollo/email columns after profile fields for T05
    for key in (
        "email",
        "email_status",
        "apollo_person_id",
        "apollo_reveal_status",
        "reject_reason",
    ):
        if key in extra:
            extra.remove(key)
            extra.append(key)
    fields = base + extra
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _backfill_file(path: Path) -> tuple[int, int]:
    if not path.is_file():
        return 0, 0
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = [{k: (v or "") for k, v in row.items()} for row in csv.DictReader(fh)]
    filled = 0
    for row in rows:
        lang = infer_outreach_language_from_row({**row, "outreach_language": ""})
        row["outreach_language"] = lang
        if lang:
            filled += 1
    _write(path, rows)
    return filled, len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--no-sheet", action="store_true")
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    camp = Path(campaign.campaign_dir)

    t05 = talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE)
    t04 = talent_stage_path(camp, STAGE_T04_PROFILES)
    f05, n05 = _backfill_file(t05)
    f04, n04 = _backfill_file(t04)
    print(f"T05 outreach_language filled {f05}/{n05}")
    print(f"T04 outreach_language filled {f04}/{n04}")

    if not args.no_sheet and n05:
        try:
            meta = publish_talent_sheet(camp, campaign.campaign_id, update=True)
            print("Sheet:", meta.get("sheet_url"))
        except Exception as exc:
            print(f"Sheet publish skipped: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
