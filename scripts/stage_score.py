"""
Stage 06 — Deterministic keyword-overlap scoring (no LLM).

Usage:
  python scripts/stage_score.py --campaign {campaign_id}
  python scripts/stage_score.py --campaign {campaign_id} --input path/to/05_enriched.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.utils.keyword_score import score_leads
from agent.utils.lead_from_csv import lead_from_row
from agent.utils.stage_runner import (
    campaign_arg_load,
    get_logger,
    leads_to_rows,
    load_client_profile,
    read_csv_rows,
    resolve_input_csv,
    write_stage_csv,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 06: keyword-overlap score")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--input", default="", help="CSV (default: stages/05_enriched.csv)")
    parser.add_argument("--client-id", default=None)
    args = parser.parse_args()

    log = get_logger("stage_score")
    campaign = campaign_arg_load(args.campaign)
    client = load_client_profile(campaign, client_id_override=args.client_id)
    inp = resolve_input_csv(campaign, input_path=args.input or None, default_stage_key="enriched")
    rows = read_csv_rows(inp)
    if not rows:
        print("ERROR: empty input", file=sys.stderr)
        return 2

    run_id = (rows[0].get("run_id") or f"score_{campaign.campaign_id}").strip()
    leads = [lead_from_row(r, run_id=run_id, client_id=client.client_id) for r in rows]
    leads = score_leads(
        leads,
        client.sender,
        hiring_first=client.icp.website_analysis_mode == "hiring_first",
    )
    log.info("Scored %d leads (keyword_overlap)", len(leads))

    out = write_stage_csv(campaign, "scored", leads_to_rows(leads))
    print(f"Scored {len(leads)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
