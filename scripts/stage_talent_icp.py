"""
Talent T03 — Person ICP match on seed fields → T03_icp_matched / T03_icp_rejected.

No LLM. Applies title/headline/location/keyword/exclusion rules from icp.json.

Usage:
  python scripts/stage_talent_icp.py --campaign {campaign_id}
  python scripts/stage_talent_icp.py --campaign {id} --input path/to/people.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.utils.campaign_config import load_campaign
from agent.utils.talent_icp import run_talent_icp_stage
from agent.utils.talent_people import STAGE_T02_DEDUPED


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Talent T03: person ICP match → T03_icp_matched.csv / T03_icp_rejected.csv"
    )
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--input",
        default="",
        help=f"CSV (default: stages/{STAGE_T02_DEDUPED})",
    )
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    input_path = Path(args.input) if args.input else None

    try:
        matched_path, rejected_path, n_ok, n_rej = run_talent_icp_stage(
            campaign.campaign_dir,
            input_path=input_path,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Campaign: {campaign.campaign_id}")
    print(f"Matched:  {n_ok} -> {matched_path}")
    print(f"Rejected: {n_rej} -> {rejected_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
