"""
Open the live cost ledger SPA, or build a single-campaign cost report.

Usage:
  python scripts/open_campaign_cost_dashboard.py --ledger
  python scripts/open_campaign_cost_dashboard.py --campaign automata_us_rnd
  python scripts/open_campaign_cost_dashboard.py --preview-template

Deprecated: --portal (alias of --ledger; old cost_portal.html is gone).
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)


def _rebuild_spa_index() -> Path:
    from agent.utils.cost_dashboard import build_dashboard_index
    from agent.utils.cost_dashboard_html import render_document

    data = ROOT / "data"
    index = build_dashboard_index(data / "campaigns")
    index_path = data / "cost_dashboard_index.json"
    html_path = data / "cost_dashboard.html"
    index_path.write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")
    html_path.write_text(render_document(index), encoding="utf-8")
    print(f"SPA index: {index_path}")
    print(f"Offline:   {html_path}")
    print("Live UI:   python scripts/serve_cost_dashboard.py → /dashboard")
    return html_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Open cost ledger or campaign cost report")
    parser.add_argument("--campaign", help="Campaign id under data/campaigns/")
    parser.add_argument(
        "--ledger",
        "--portal",
        dest="ledger",
        action="store_true",
        help="Rebuild SPA index (replaces old cost_portal.html)",
    )
    parser.add_argument(
        "--preview-template",
        action="store_true",
        help="Open examples/campaign_cost_dashboard/preview.html",
    )
    parser.add_argument("--no-open", action="store_true", help="Build only; do not open browser")
    args = parser.parse_args()

    from agent.utils.cost_report import (
        build_campaign_report,
        load_sample_report,
        write_campaign_cost_artifacts,
    )

    html_path: Path | None = None

    if args.preview_template:
        report = load_sample_report()
        preview_dir = ROOT / "examples" / "campaign_cost_dashboard"
        _, _, html_path = write_campaign_cost_artifacts(
            report,
            campaign_dir=preview_dir,
            html_path=preview_dir / "preview.html",
        )
        print(f"Template preview: {html_path}")
    elif args.ledger:
        html_path = _rebuild_spa_index()
    elif args.campaign:
        campaign_dir = ROOT / "data" / "campaigns" / args.campaign
        report = build_campaign_report(args.campaign, root=ROOT / "data")
        json_path, _, html_path = write_campaign_cost_artifacts(report, campaign_dir=campaign_dir)
        totals = report.get("totals") or {}
        print(f"Cost report: {json_path}")
        print(f"Dashboard:   {html_path}")
        print(f"Total ${totals.get('usd_total', 0):.2f}")
        print("Prefer the live SPA: /dashboard#/campaign/" + args.campaign)
    else:
        parser.error("Provide --campaign, --ledger, or --preview-template")

    if not args.no_open and html_path is not None and html_path.is_file():
        uri = html_path.resolve().as_uri()
        webbrowser.open(uri)
        print(f"Opened {uri}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
