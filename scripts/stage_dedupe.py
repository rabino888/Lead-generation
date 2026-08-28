"""
Stage 03 — Domain / name dedupe (in-batch + optional Sheets Dedup filter).

Usage:
  python scripts/stage_dedupe.py --campaign {campaign_id}
  python scripts/stage_dedupe.py --campaign {campaign_id} --input path/to/matched.csv
  python scripts/stage_dedupe.py --campaign {campaign_id} --skip-sheets-dedup
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.utils.deduplication import filter_seen_leads
from agent.utils.icp_rules import load_icp_rules
from agent.utils.pre_apollo_gate import filter_pre_apollo
from agent.utils.stage_artifacts import dedupe_seed_rows
from agent.utils.stage_runner import (
    campaign_arg_load,
    get_logger,
    load_client_profile,
    read_csv_rows,
    resolve_input_csv,
    rows_to_raw_companies,
    write_stage_csv,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 03: dedupe seed CSV")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--input", default="", help="CSV (default: stages/02_icp_matched.csv)")
    parser.add_argument("--client-id", default=None)
    parser.add_argument(
        "--skip-sheets-dedup",
        action="store_true",
        help="Only in-file dedupe (skip Google Sheets Dedup tab)",
    )
    parser.add_argument(
        "--skip-pre-apollo-gate",
        action="store_true",
        help="Skip pre-Apollo size/mega/recruiter gate (not recommended)",
    )
    parser.add_argument(
        "--allow-unknown-size",
        action="store_true",
        help="Pre-Apollo gate: keep seeds without employee count",
    )
    args = parser.parse_args()

    log = get_logger("stage_dedupe")
    campaign = campaign_arg_load(args.campaign)
    inp = resolve_input_csv(
        campaign, input_path=args.input or None, default_stage_key="icp_matched"
    )
    rows = read_csv_rows(inp)
    kept, dropped_inbatch = dedupe_seed_rows(rows)
    log.info("In-batch dedupe: %d kept, %d dropped from %s", len(kept), len(dropped_inbatch), inp)

    if not args.skip_sheets_dedup:
        client = load_client_profile(campaign, client_id_override=args.client_id)
        companies = rows_to_raw_companies(kept, client.icp)
        filtered = filter_seen_leads(client.client_id, companies, log)
        kept_names = {(c.company_name or "").lower() for c in filtered}
        kept = [r for r in kept if (r.get("company_name") or "").lower() in kept_names]
        log.info("Sheets Dedup: %d remaining for client %s", len(kept), client.client_id)

    if not args.skip_pre_apollo_gate:
        rules = load_icp_rules(campaign)
        pre_kept, pre_rejected = filter_pre_apollo(
            kept,
            rules,
            campaign.campaign_dir,
            drop_unknown_size=not args.allow_unknown_size,
        )
        if pre_rejected:
            rej_fields = list(kept[0].keys()) if kept else ["company_name", "website"]
            if "pre_apollo_reason" not in rej_fields:
                rej_fields.append("pre_apollo_reason")
            write_stage_csv(campaign, "pre_apollo_rejected", pre_rejected, rej_fields)
            log.info(
                "Pre-Apollo gate: %d kept, %d rejected",
                len(pre_kept),
                len(pre_rejected),
            )
        kept = pre_kept

    fields = list(kept[0].keys()) if kept else list(rows[0].keys()) if rows else ["company_name", "website"]
    out = write_stage_csv(campaign, "deduped", kept, fields)
    print(f"Wrote {len(kept)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
