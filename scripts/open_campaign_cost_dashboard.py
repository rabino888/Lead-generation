"""
Build cost report and open the campaign cost dashboard in a browser.

Usage:
  python scripts/open_campaign_cost_dashboard.py --campaign automata_us_rnd
  python scripts/open_campaign_cost_dashboard.py --campaign automata_us_rnd --no-open
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

FUNNEL_STEPS = [
    ("intake", "ICP + seed plan (LLM/human)"),
    ("seeding", "Initial seed scrape"),
    ("icp_match", "ICP match (rules)"),
    ("dedupe", "De-duplication"),
    ("pre_apollo", "Pre-Apollo gate"),
    ("apollo_contact", "Apollo email + DM LinkedIn"),
    ("enrichment", "Firecrawl + Apify LinkedIn + LLM"),
    ("keyword_score", "Keyword score"),
    ("qa", "Human QA gate"),
    ("phone_reveal", "Apollo phone reveal (optional)"),
]


def _inject_funnel_html(campaign_dir: Path, report: dict) -> None:
    """Append funnel + workflow context to cost_dashboard.html."""
    html_path = campaign_dir / "cost_dashboard.html"
    if not html_path.exists():
        return
    steps_html = ""
    for key, label in FUNNEL_STEPS:
        steps_html += f"<li><code>{key}</code> — {label}</li>"
    by_step = report.get("by_workflow_step") or report.get("by_cost_center") or {}
    spend_rows = ""
    for key in ("seeding", "apollo_contact", "enrichment"):
        block = by_step.get(key) or {}
        total = block.get("usd_total", block.get("usd_billed", 0) + block.get("usd_estimated", 0))
        spend_rows += f"<tr><td>{key}</td><td class='num'>${float(total):.4f}</td></tr>"
    snippet = f"""
  <h2>Deterministic funnel (operator workflow)</h2>
  <ol>{steps_html}</ol>
  <p class="meta">Company LinkedIn open jobs are <strong>optional</strong> (APIFY_SKIP_LINKEDIN_JOBS=1 default).</p>
  <h2>Spend by workflow step (this campaign)</h2>
  <table>
    <thead><tr><th>Step</th><th>USD</th></tr></thead>
    <tbody>{spend_rows}</tbody>
  </table>
"""
    text = html_path.read_text(encoding="utf-8")
    if "Deterministic funnel" not in text:
        text = text.replace("</body>", snippet + "\n</body>")
        html_path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Open campaign cost dashboard")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--no-open", action="store_true", help="Build only; do not open browser")
    args = parser.parse_args()

    from scripts.build_campaign_cost_report import build_report, write_html, write_markdown

    campaign_dir = ROOT / "data" / "campaigns" / args.campaign
    report = build_report(args.campaign)
    json_path = campaign_dir / "cost_log.json"
    md_path = campaign_dir / "cost_log.md"
    html_path = campaign_dir / "cost_dashboard.html"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_markdown(report, md_path)
    write_html(report, html_path)
    _inject_funnel_html(campaign_dir, report)

    totals = report.get("totals") or {}
    print(f"Cost report: {json_path}")
    print(f"Dashboard:   {html_path}")
    print(
        f"Total ~${totals.get('usd_total_est', 0):.2f} "
        f"(billed ${totals.get('usd_billed', 0):.2f} + est ${totals.get('usd_estimated', 0):.2f})"
    )
    for key, block in (report.get("by_workflow_step") or {}).items():
        print(f"  {key}: ${block.get('usd_total', 0):.4f}")

    if not args.no_open:
        uri = html_path.resolve().as_uri()
        webbrowser.open(uri)
        print(f"Opened {uri}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
