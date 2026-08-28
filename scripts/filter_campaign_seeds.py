"""
Filter an existing campaign seed list to strong ICP fits only.

Usage:
  python scripts/filter_campaign_seeds.py --campaign {campaign_id}
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.utils.campaign_config import load_campaign
from agent.utils.icp_seed_quality import weak_seed_reason
from scripts.build_seed_list import Seed, _is_excluded, write_outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Keep only strong ICP seed fits")
    parser.add_argument("--campaign", required=True)
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    csv_path = campaign.seeds_csv
    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found", file=sys.stderr)
        return 2

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    kept: list[Seed] = []
    dropped: list[tuple[str, str, str]] = []

    for row in rows:
        name = row["company_name"]
        website = row["website"]
        if _is_excluded(campaign, name, website):
            dropped.append((name, website, "exclusion rule"))
            continue
        weak = weak_seed_reason(name, website, enabled=False)
        if weak:
            dropped.append((name, website, weak))
            continue
        kept.append(
            Seed(
                seed_id="",
                company_name=name,
                website=website,
                location=row.get("location", ""),
                geo_segment=row.get("geo_segment", ""),
                source_url=row.get("source_url", ""),
                status=row.get("status", "pending"),
                notes=row.get("notes", ""),
            )
        )

    prefix = campaign.seed_id_prefix
    for i, seed in enumerate(kept, start=1):
        seed.seed_id = f"{prefix}-{i:04d}"

    audit_path = campaign.campaign_dir / "seeds_dropped.json"
    audit_path.write_text(
        json.dumps(
            [{"company_name": n, "website": w, "reason": r} for n, w, r in dropped],
            indent=2,
        ),
        encoding="utf-8",
    )

    write_outputs(campaign, kept)
    print(f"Kept {len(kept)}/{len(rows)} strong ICP seeds")
    print(f"Dropped {len(dropped)} -> {audit_path}")
    for name, _, reason in dropped:
        print(f"  - {name} ({reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
