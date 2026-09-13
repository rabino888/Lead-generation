"""
Build campaign cost report (schema v4) or rebuild the live SPA cost index.

Usage:
  python scripts/build_campaign_cost_report.py --campaign automata_us_rnd
  python scripts/build_campaign_cost_report.py --ledger
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build campaign cost report")
    parser.add_argument("--campaign", help="Campaign id under data/campaigns/")
    parser.add_argument(
        "--ledger",
        "--portal",
        dest="ledger",
        action="store_true",
        help="Rebuild SPA cost_dashboard_index.json (replaces old cost_portal.html)",
    )
    parser.add_argument(
        "--preview-template",
        action="store_true",
        help="Render examples/campaign_cost_dashboard/preview.html from sample_report.json",
    )
    args = parser.parse_args()

    from agent.utils.cost_report import (
        build_campaign_report,
        load_sample_report,
        write_campaign_cost_artifacts,
    )

    if args.preview_template:
        report = load_sample_report()
        preview_dir = ROOT / "examples" / "campaign_cost_dashboard"
        _, _, html_path = write_campaign_cost_artifacts(
            report,
            campaign_dir=preview_dir,
            html_path=preview_dir / "preview.html",
        )
        print(f"Template preview: {html_path}")
        return 0

    if args.ledger:
        from agent.utils.cost_dashboard import build_dashboard_index
        from agent.utils.cost_dashboard_html import render_document

        data = ROOT / "data"
        index = build_dashboard_index(data / "campaigns")
        index_path = data / "cost_dashboard_index.json"
        html_path = data / "cost_dashboard.html"
        index_path.write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")
        html_path.write_text(render_document(index), encoding="utf-8")
        totals = index.get("totals") or {}
        print(f"SPA index: {index_path}")
        print(f"Offline:   {html_path}")
        print(
            f"{totals.get('campaign_count', 0)} campaigns · "
            f"${float(totals.get('usd_total') or 0):.2f}"
        )
        print("Serve with: python scripts/serve_cost_dashboard.py → /dashboard")
        return 0

    if not args.campaign:
        parser.error("Provide --campaign, --ledger, or --preview-template")

    report = build_campaign_report(args.campaign, root=ROOT / "data")
    campaign_dir = ROOT / "data" / "campaigns" / args.campaign
    json_path, md_path, html_path = write_campaign_cost_artifacts(
        report, campaign_dir=campaign_dir
    )
    totals = report["totals"]
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"Wrote {html_path}")
    print(f"Total ${totals['usd_total']:.2f} ({totals['contactable_leads']} contactable)")
    print("By workflow step:")
    for step, row in sorted((report.get("by_workflow_step") or {}).items()):
        print(f"  {step}: ${row.get('usd', 0):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
