"""
Build the lead-list cost dashboard index from data/.

Scans every campaign under `data/campaigns/` — v4 `cost_runs.json`, pre-v4
`cost_log_run_*.json`, and the stage CSVs — and writes:

  data/cost_dashboard_index.json   live API payload for /dashboard
  data/cost_dashboard.html         optional offline single-file export
                                   (master template + embedded data; no per-client files)

Live UI: agent/dashboard/static/dashboard.html + portal.css via
`python scripts/serve_cost_dashboard.py` → http://127.0.0.1:8765/dashboard

Usage:
  python scripts/build_cost_dashboard.py
  python scripts/build_cost_dashboard.py --no-open
  python scripts/build_cost_dashboard.py --body build/dashboard_body.html
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the lead-list cost dashboard index")
    parser.add_argument("--no-open", action="store_true", help="Build only; do not open a browser")
    parser.add_argument("--body", help="Also write page body only (legacy Artifact export)")
    args = parser.parse_args()

    from agent.utils.cost_dashboard import build_dashboard_index
    from agent.utils.cost_dashboard_html import render_body, render_document

    index = build_dashboard_index(ROOT / "data" / "campaigns")

    index_path = ROOT / "data" / "cost_dashboard_index.json"
    html_path = ROOT / "data" / "cost_dashboard.html"
    index_path.write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")
    html_path.write_text(render_document(index), encoding="utf-8")

    if args.body:
        body_path = Path(args.body)
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_text(render_body(index), encoding="utf-8")
        print(f"Body:      {body_path}")

    totals = index.get("totals") or {}
    print(f"Index:     {index_path}")
    print(f"Offline:   {html_path} (master template; open via serve for /static CSS)")
    print(f"Portal:    agent/dashboard/static/dashboard.html")
    print(
        f"{totals.get('campaign_count', 0)} lead lists "
        f"({totals.get('tracked_campaign_count', 0)} with cost data) · "
        f"{totals.get('delivered_leads', 0)} leads · "
        f"${float(totals.get('usd_total') or 0):.2f}"
    )

    errored = [c for c in index.get("campaigns") or [] if c.get("error")]
    for c in errored:
        print(f"  ! {c.get('campaign_id')}: {c['error']}", file=sys.stderr)

    if not args.no_open:
        webbrowser.open((ROOT / "agent" / "dashboard" / "static" / "dashboard.html").resolve().as_uri())
        print("Note: file:// needs serve for /dashboard/api — prefer: python scripts/serve_cost_dashboard.py")
    return 1 if errored else 0


if __name__ == "__main__":
    raise SystemExit(main())
