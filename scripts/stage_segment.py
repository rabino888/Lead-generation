"""
Stage 08 — Outreach segments for any company-outreach campaign.

Groups enriched contacts by company, open roles, buyer seniority, and size
versus the campaign ICP. Optional role buckets live in icp.json
(`outreach_role_groups`), not in code.

Usage:
  python scripts/stage_segment.py --campaign {campaign_id}
  python scripts/stage_segment.py --campaign {campaign_id} --input path/to/05_enriched.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.utils.campaign_config import load_icp
from agent.utils.icp_rules import load_icp_rules, stage_path
from agent.utils.outreach_segments import segment_outreach_rows
from agent.utils.stage_runner import (
    campaign_arg_load,
    get_logger,
    read_csv_rows,
    resolve_input_csv,
    write_stage_csv,
)


def _role_groups(campaign) -> list[dict]:
    if not campaign.icp_path.exists():
        return []
    icp = load_icp(campaign)
    return [group.model_dump() for group in icp.outreach_role_groups]


def _default_input(campaign, explicit: str) -> Path:
    if explicit:
        return resolve_input_csv(campaign, input_path=explicit, default_stage_key="scored")
    scored = stage_path(campaign, "scored")
    if scored.exists():
        return scored
    return resolve_input_csv(campaign, input_path=None, default_stage_key="enriched")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 08: outreach segments")
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--input",
        default="",
        help="CSV (default: stages/06_scored.csv, else stages/05_enriched.csv)",
    )
    args = parser.parse_args()

    log = get_logger("stage_segment")
    campaign = campaign_arg_load(args.campaign)
    rules = load_icp_rules(campaign)
    inp = _default_input(campaign, args.input)
    rows = read_csv_rows(inp)
    if not rows:
        print("ERROR: empty input", file=sys.stderr)
        return 2

    segmented = segment_outreach_rows(
        rows,
        company_size_min=rules.company_size_min,
        company_size_max=rules.company_size_max,
        role_groups=_role_groups(campaign),
    )
    out = write_stage_csv(campaign, "outreach_segments", segmented)
    companies = len({row["segment_company_key"] for row in segmented})
    slices = len({row["segment_slice"] for row in segmented})
    first = sum(1 for row in segmented if row["segment_contact_use"] == "first_touch")
    log.info(
        "Segmented %d contacts into %d companies / %d slices",
        len(segmented),
        companies,
        slices,
    )
    print(
        f"Segmented {len(segmented)} contacts, {companies} companies, "
        f"{slices} slices, {first} first-touch -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
