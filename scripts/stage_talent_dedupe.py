"""
Talent T02 — Person dedupe by LinkedIn URL → T02_deduped / T02_dedupe_rejected.

Usage:
  python scripts/stage_talent_dedupe.py --campaign {id}
  python scripts/stage_talent_dedupe.py --campaign {id} --input path/to/T01.csv
  python scripts/stage_talent_dedupe.py --campaign {id} --sheets-dedup
  python scripts/stage_talent_dedupe.py --campaign {id} --sheets-dedup --client-id {client}
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.utils.stage_runner import campaign_arg_load, get_logger, load_client_profile
from agent.utils.talent_dedupe import load_sheets_seen_contacts, run_talent_dedupe


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Talent T02: dedupe T01 people by normalized LinkedIn URL"
    )
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--input",
        default="",
        help="CSV (default: stages/T01_raw_people.csv)",
    )
    parser.add_argument("--client-id", default=None)
    parser.add_argument(
        "--sheets-dedup",
        action="store_true",
        help="Also drop people already on Sheets ContactDedup (default: off)",
    )
    args = parser.parse_args()

    log = get_logger("stage_talent_dedupe")
    campaign = campaign_arg_load(args.campaign)

    seen: set[str] | None = None
    if args.sheets_dedup:
        client = load_client_profile(campaign, client_id_override=args.client_id)
        seen = load_sheets_seen_contacts(client.client_id, logger=log)
        log.info(
            "Sheets ContactDedup: %d known keys for client %s",
            len(seen),
            client.client_id,
        )

    try:
        kept_path, rejected_path, n_kept, n_rej = run_talent_dedupe(
            campaign.campaign_dir,
            input_path=Path(args.input) if args.input else None,
            seen_contact_keys=seen,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    log.info("In-batch (+optional Sheets) dedupe: %d kept, %d rejected", n_kept, n_rej)
    print(f"T02 deduped -> {kept_path} ({n_kept} rows)")
    print(f"T02 rejected -> {rejected_path} ({n_rej} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
