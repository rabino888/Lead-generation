"""
Stage 07 — QA flags for human review (borderline scores, location, empty matches).

Usage:
  python scripts/stage_qa_flags.py --campaign {campaign_id}
  python scripts/stage_qa_flags.py --campaign {campaign_id} --input path/to/06_scored.csv
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
from agent.utils.lead_from_csv import lead_from_row
from agent.utils.stage_artifacts import qa_flags_for_leads
from agent.utils.stage_runner import (
    campaign_arg_load,
    get_logger,
    load_client_profile,
    read_csv_rows,
    resolve_input_csv,
    write_stage_csv,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 07: QA flags CSV")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--input", default="", help="CSV (default: stages/06_scored.csv)")
    parser.add_argument("--client-id", default=None)
    parser.add_argument("--borderline-max", type=int, default=35)
    args = parser.parse_args()

    log = get_logger("stage_qa_flags")
    campaign = campaign_arg_load(args.campaign)
    client = load_client_profile(campaign, client_id_override=args.client_id)
    rules = load_icp_rules(campaign)
    inp = resolve_input_csv(campaign, input_path=args.input or None, default_stage_key="scored")
    rows = read_csv_rows(inp)
    if not rows:
        print("ERROR: empty input", file=sys.stderr)
        return 2

    run_id = (rows[0].get("run_id") or f"qa_{campaign.campaign_id}").strip()
    leads = [lead_from_row(r, run_id=run_id, client_id=client.client_id) for r in rows]
    flags = qa_flags_for_leads(
        leads,
        contact_locations=rules.contact_locations or client.icp.contact_locations,
        borderline_max=args.borderline_max,
    )
    log.info("QA flags: %d / %d leads", len(flags), len(leads))
    out = write_stage_csv(
        campaign,
        "qa_flags",
        flags,
        list(flags[0].keys()) if flags else ["company_name", "qa_flags"],
    )
    print(f"QA flags {len(flags)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
