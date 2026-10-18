"""
Fill missing treg LinkedIn URLs on stages/04_source_compare.csv.

Uses icypeas.profile.url.bulk (about $0.019 per found URL, misses free).
Updates the existing Google Sheet and appends a cost-ledger entry.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.integrations.sheets import _get_gspread_client
from agent.integrations.treg import call_catalog
from agent.models import InputMode
from agent.utils import run_tracker
from agent.utils.campaign_config import load_campaign, load_icp
from agent.utils.cost_manifest import finalize_stage_cost
from agent.utils.cost_tracker import get_cost_tracker, init_cost_tracker
from scripts.upload_source_compare_sheet import SCORE_FIELDS, _matrix, _score_row

_ZW = re.compile(r"[\u200b\u200c\u200d\ufeff]")


def _clean_name(name: str) -> tuple[str, str]:
    text = _ZW.sub("", name or "").strip()
    parts = [part for part in text.split() if part]
    if len(parts) < 2:
        return "", ""
    return parts[0], parts[-1]


def _url_from_item(item: object) -> str:
    if isinstance(item, str) and "linkedin.com/" in item.lower():
        return item.strip()
    if not isinstance(item, dict):
        return ""
    for key in ("result", "url", "profileUrl", "profile_url", "linkedin_url", "linkedin"):
        val = item.get(key)
        if isinstance(val, str) and "linkedin.com/" in val.lower():
            return val.strip()
        if isinstance(val, dict):
            nested = _url_from_item(val)
            if nested:
                return nested
    return ""


def _items_from_payload(data: object) -> list:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("data", "results", "output"):
        val = data.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            for inner in ("data", "results", "items"):
                nested = val.get(inner)
                if isinstance(nested, list):
                    return nested
    return []


def main() -> int:
    campaign_id = "vista_business_brain_staffing_b2b_marketing_agencies_us_new_l"
    campaign = load_campaign(campaign_id)
    icp = load_icp(campaign)
    csv_path = campaign.campaign_dir / "stages" / "04_source_compare.csv"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0].keys()) if rows else []

    targets: list[tuple[int, str, str, str]] = []
    for index, row in enumerate(rows):
        if (row.get("treg_linkedin") or "").strip():
            continue
        first, last = _clean_name(row.get("treg_name") or "")
        domain = (row.get("domain") or "").strip()
        if first and last and domain:
            targets.append((index, first, last, domain))

    print(f"Looking up {len(targets)} LinkedIn URLs", flush=True)
    if not targets:
        print("Nothing to look up")
        return 0

    started = datetime.now(timezone.utc)
    run_state = run_tracker.create_run(
        client_id=campaign.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=len(targets),
        keyword=f"treg_linkedin:{campaign.campaign_id}",
    )
    init_cost_tracker(run_state.run_id, campaign.client_id)

    body = {
        "type": "profile",
        "data": [
            {"firstname": first, "lastname": last, "companyOrDomain": domain}
            for _index, first, last, domain in targets
        ],
    }
    data, usd = call_catalog(
        "icypeas.profile.url.bulk",
        method="POST",
        body=body,
        timeout=180,
        idempotency_key=f"{campaign.campaign_id}:treg-linkedin-bulk:v1",
    )
    raw_path = campaign.campaign_dir / "stages" / "04_treg_linkedin_raw.json"
    raw_path.write_text(json.dumps(data, indent=2, default=str)[:500000], encoding="utf-8")
    items = _items_from_payload(data)
    print(f"treg cost=${usd:.4f} items={len(items)}", flush=True)
    if len(items) != len(targets):
        print("Response shape did not match the request. Raw payload saved.", file=sys.stderr)
        print(json.dumps(data, default=str)[:1500], file=sys.stderr)
        return 1

    found = 0
    for (index, _first, _last, _domain), item in zip(targets, items):
        url = _url_from_item(item)
        if url:
            rows[index]["treg_linkedin"] = url
            found += 1
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Filled {found} of {len(targets)} LinkedIn URLs", flush=True)

    scored = [{**row, **_score_row(row, list(icp.job_titles))} for row in rows]
    sheet_fields = fields + [name for name in SCORE_FIELDS if name not in fields]
    meta = json.loads((campaign.campaign_dir / "campaign_sheet.json").read_text(encoding="utf-8"))
    gc = _get_gspread_client()
    spreadsheet = gc.open_by_key(meta["spreadsheet_id"])
    ws = spreadsheet.worksheet("Compare")
    ws.clear()
    ws.update([sheet_fields] + _matrix(scored, sheet_fields), value_input_option="USER_ENTERED")
    try:
        ws.format("1:1", {"textFormat": {"bold": True}})
    except Exception:
        pass

    tracker = get_cost_tracker()
    summary = tracker.run_summary() if tracker else None
    finalize_stage_cost(
        campaign_dir=campaign.campaign_dir,
        campaign_id=campaign.campaign_id,
        client_id=campaign.client_id,
        run_id=run_state.run_id,
        phase="treg_linkedin_urls",
        started_at=started,
        tracker_summary=summary,
        label=f"treg LinkedIn URLs ({found} of {len(targets)})",
        outcome={"lookups": len(targets), "found": found, "treg_usd": usd},
        extra={"output": str(csv_path)},
    )
    print(f"Sheet updated: {meta['sheet_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
