"""
Merge stages/04_apollo_contactable_prefill.csv (known emails) with
stages/04_apollo_contactable.csv (Apollo reveals) into the canonical
04_apollo_contactable.csv — preserving Apollo person fields.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.utils.apollo_contactable import write_apollo_contactable_csv
from agent.utils.campaign_config import load_campaign
from agent.utils.icp_rules import ensure_stages_dir, stage_path


def _read(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    import csv

    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge prefilled + Apollo contactable CSVs")
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--allow-thin-prefill",
        action="store_true",
        help="Allow prefill rows with email only (no name/LinkedIn) — not recommended",
    )
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    ensure_stages_dir(campaign)
    prefill = campaign.stages_dir / "04_apollo_contactable_prefill.csv"
    apollo = stage_path(campaign, "apollo_contactable")

    rows = _read(prefill) + _read(apollo)
    seen_email: set[str] = set()
    seen_co: set[str] = set()
    kept: list[dict] = []
    for row in rows:
        email = (row.get("decision_maker_email") or "").strip().lower()
        co = (row.get("company_name") or "").strip().lower()
        if email and email in seen_email:
            continue
        if not email and co in seen_co:
            continue
        if email:
            seen_email.add(email)
        if co:
            seen_co.add(co)
        kept.append(row)

    if not kept:
        print("ERROR: nothing to merge", file=sys.stderr)
        return 2

    out = write_apollo_contactable_csv(
        campaign,
        kept,
        strict=not args.allow_thin_prefill,
    )
    print(f"Merged {len(kept)} contactable leads -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
