"""
Talent T06 — Apollo phone reveal for contactable people → T06_phone_enriched.

Reuses scripts/request_apollo_phone_reveals.py + webhook import path.
Does not invent phone numbers; import_apollo_phone_webhooks.py merges later.

Usage:
  python scripts/stage_talent_phone.py --campaign {campaign_id}
  python scripts/stage_talent_phone.py --campaign {id} --input path/to/T05.csv
  python scripts/stage_talent_phone.py --campaign {id} --dry-run
  python scripts/stage_talent_phone.py --campaign {id} --limit 10

After webhook delivery:
  python scripts/import_apollo_phone_webhooks.py \\
    --csv data/campaigns/{id}/stages/T06_phone_enriched.csv --fetch-railway
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.utils.stage_runner import campaign_arg_load, get_logger
from agent.utils.talent_people import STAGE_T05_APOLLO_CONTACTABLE
from agent.utils.talent_phone import run_talent_phone_stage


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Talent T06: Apollo phone reveal (async webhook) for T05 contactable"
    )
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--input",
        default="",
        help=f"CSV (default: stages/{STAGE_T05_APOLLO_CONTACTABLE})",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max phone reveal requests")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write T06 + log dry_run events without calling Apollo",
    )
    parser.add_argument(
        "--skip-request",
        action="store_true",
        help="Only write T06 CSV with empty phone columns (no Apollo request)",
    )
    args = parser.parse_args()

    log = get_logger("stage_talent_phone")
    campaign = campaign_arg_load(args.campaign)
    input_path = Path(args.input) if args.input else None

    try:
        out_path, n_rows, n_req = run_talent_phone_stage(
            campaign.campaign_dir,
            input_path=input_path,
            dry_run=args.dry_run,
            limit=args.limit,
            skip_request=args.skip_request,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    log.info("T06 phone: %d rows, %d requests -> %s", n_rows, n_req, out_path)
    print(f"T06 phone enriched: {n_rows} rows -> {out_path}")
    print(f"Phone reveal requests: {n_req}")
    if n_req and not args.skip_request:
        print(
            "Phones arrive asynchronously via webhook. Merge later with:\n"
            f"  python scripts/import_apollo_phone_webhooks.py "
            f"--csv {out_path} --fetch-railway"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
