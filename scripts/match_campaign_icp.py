"""
Deterministic ICP match for campaign seeds.

Reads stages/01_raw_seeds.csv (or seeds.csv fallback), writes:
  stages/02_icp_matched.csv
  stages/02_icp_rejected.csv

Usage:
  python scripts/match_campaign_icp.py --campaign {campaign_id}
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.utils.campaign_config import SEED_HEADERS, load_campaign
from agent.utils.icp_match import match_company, match_reason_summary
from agent.utils.icp_rules import ensure_stages_dir, load_icp_rules, stage_path


def _read_seeds(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic ICP match for campaign seeds")
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--input",
        default="",
        help="Optional input CSV (default: stages/01_raw_seeds.csv, else seeds.csv)",
    )
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    rules = load_icp_rules(campaign)
    ensure_stages_dir(campaign)

    if args.input:
        input_path = Path(args.input)
    else:
        raw = stage_path(campaign, "raw_seeds")
        input_path = raw if raw.exists() else campaign.seeds_csv

    if not input_path.exists():
        print(f"ERROR: input not found: {input_path}", file=sys.stderr)
        return 2

    rows = _read_seeds(input_path)
    matched: list[dict] = []
    rejected: list[dict] = []

    for row in rows:
        passed, reasons = match_company(row, rules)
        out = dict(row)
        out["icp_match"] = "pass" if passed else "fail"
        out["icp_match_reasons"] = match_reason_summary(passed, reasons)
        if passed:
            matched.append(out)
        else:
            rejected.append(out)

    out_fields = list(rows[0].keys()) if rows else list(SEED_HEADERS)
    for col in ("icp_match", "icp_match_reasons"):
        if col not in out_fields:
            out_fields.append(col)

    matched_path = stage_path(campaign, "icp_matched")
    rejected_path = stage_path(campaign, "icp_rejected")
    _write_csv(matched_path, matched, out_fields)
    _write_csv(rejected_path, rejected, out_fields)

    print(f"Campaign: {campaign.campaign_id}")
    print(f"Input:    {input_path} ({len(rows)} rows)")
    print(f"Matched:  {len(matched)} -> {matched_path}")
    print(f"Rejected: {len(rejected)} -> {rejected_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
