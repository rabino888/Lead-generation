"""
Stage 03b — Pre-Apollo hard gate (size, mega, recruiter, re-bill protection).

Runs after dedupe; writes filtered rows back to 03_deduped.csv and rejects to
03_pre_apollo_rejected.csv.

Usage:
  python scripts/stage_pre_apollo.py --campaign automata_us_rnd
  python scripts/stage_pre_apollo.py --campaign automata_us_rnd --allow-unknown-size
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.utils.icp_rules import load_icp_rules
from agent.utils.pre_apollo_gate import filter_pre_apollo
from agent.utils.stage_runner import (
    campaign_arg_load,
    get_logger,
    read_csv_rows,
    resolve_input_csv,
    write_stage_csv,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-Apollo gate on deduped seeds")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--input", default="", help="Default: stages/03_deduped.csv")
    parser.add_argument(
        "--allow-unknown-size",
        action="store_true",
        help="Keep seeds without employee count (default: drop when company_size_max set)",
    )
    args = parser.parse_args()

    log = get_logger("stage_pre_apollo")
    campaign = campaign_arg_load(args.campaign)
    rules = load_icp_rules(campaign)
    inp = resolve_input_csv(campaign, input_path=args.input or None, default_stage_key="deduped")
    rows = read_csv_rows(inp)
    if not rows:
        print("ERROR: no rows in input", file=sys.stderr)
        return 2

    kept, rejected = filter_pre_apollo(
        rows,
        rules,
        campaign.campaign_dir,
        drop_unknown_size=not args.allow_unknown_size,
    )
    log.info("Pre-Apollo gate: %d kept, %d rejected (from %d)", len(kept), len(rejected), len(rows))

    fields = list(rows[0].keys()) if rows else ["company_name", "website"]
    if rejected:
        rej_fields = list(fields)
        if "pre_apollo_reason" not in rej_fields:
            rej_fields.append("pre_apollo_reason")
        write_stage_csv(campaign, "pre_apollo_rejected", rejected, rej_fields)

    out = write_stage_csv(campaign, "deduped", kept, fields)
    print(f"Kept {len(kept)} -> {out}")
    if rejected:
        print(f"Rejected {len(rejected)} -> stages/{campaign.campaign_id}/03_pre_apollo_rejected.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
