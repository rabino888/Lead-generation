"""
Ingest an operator seed CSV that may already include emails.

Splits into:
  - rows WITH email → stages/04_apollo_contactable.csv (skip Apollo spend)
  - rows WITHOUT → stages/03_deduped.csv for scripts/stage_apollo.py

After Apollo on the missing set, merge contactable CSVs and continue
stage_enrich → stage_score → stage_qa_flags. Do not run phone scripts.

Usage:
  python scripts/ingest_seed_csv_with_emails.py --campaign automata \\
      --csv data/campaigns/automata/batch2_seeds.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.utils.campaign_config import load_campaign
from agent.utils.icp_rules import ensure_stages_dir
from agent.utils.stage_artifacts import write_stage_csv

# Common header aliases on curated research sheets
_COMPANY = ("Company", "company_name", "Company Name", "name")
_WEBSITE = ("Website", "website", "url", "domain")
_EMAIL = ("Email", "email", "decision_maker_email", "Work Email")
_EMAIL_STATUS = ("Email Status", "email_status", "EmailStatus")
_LOCATION = ("HQ", "location", "Location", "geo")
_SIZE = ("Headcount", "company_size", "size", "employees")
_DM = ("Decision Maker", "decision_maker", "Contact", "Person")
_NOTES = ("Content Gap / Fit Notes", "notes", "Notes", "What They Do")
_INDUSTRY = ("Segment", "industry", "Industry")
_LINKEDIN = ("linkedin", "LinkedIn", "decision_maker_linkedin")


def _get(row: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        if k in row and (row[k] or "").strip():
            return str(row[k]).strip()
    # case-insensitive
    lower = {str(k).lower(): v for k, v in row.items()}
    for k in keys:
        v = lower.get(k.lower())
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _parse_decision_maker(raw: str) -> tuple[str, str, str]:
    """Return (name, title, linkedin_url) from free-text DM cell."""
    if not raw:
        return "", "", ""
    # Take first person if semicolon-separated
    primary = raw.split(";")[0].strip()
    linkedin = ""
    m = re.search(r"(https?://)?(www\.)?linkedin\.com/in/[\w\-/%]+", primary, re.I)
    if m:
        linkedin = m.group(0)
        if not linkedin.startswith("http"):
            linkedin = "https://" + linkedin.lstrip("/")
        primary = primary[: m.start()].strip(" -–,")
    # "Name, Title" or "Name - Title"
    name, title = primary, ""
    if "," in primary:
        name, title = primary.split(",", 1)
    elif " - " in primary:
        name, title = primary.split(" - ", 1)
    return name.strip(), title.strip(), linkedin


def main() -> int:
    parser = argparse.ArgumentParser(description="Split seed CSV by existing email")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--csv", required=True, help="Operator seed CSV (may include Email)")
    parser.add_argument(
        "--write-raw",
        action="store_true",
        help="Also write stages/01_raw_seeds.csv from all rows",
    )
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    ensure_stages_dir(campaign)
    path = Path(args.csv)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 2

    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    with_email: list[dict] = []
    without: list[dict] = []
    raw_seeds: list[dict] = []

    for i, row in enumerate(rows, start=1):
        company = _get(row, _COMPANY)
        if not company:
            continue
        website = _get(row, _WEBSITE)
        if website and not website.startswith("http"):
            website = f"https://{website}"
        email = _get(row, _EMAIL)
        email_status = _get(row, _EMAIL_STATUS)
        location = _get(row, _LOCATION)
        size = _get(row, _SIZE)
        industry = _get(row, _INDUSTRY)
        notes = _get(row, _NOTES)
        dm_name, dm_title, dm_li = _parse_decision_maker(_get(row, _DM))
        if not dm_li:
            dm_li = _get(row, _LINKEDIN)

        seed = {
            "seed_id": f"{campaign.seed_id_prefix}-b2-{i:04d}",
            "company_name": company,
            "website": website,
            "location": location,
            "geo_segment": industry,
            "industry": industry,
            "company_size": size,
            "source_url": str(path),
            "status": "pending",
            "notes": notes,
        }
        raw_seeds.append(seed)

        if email:
            with_email.append(
                {
                    **seed,
                    "lead_id": f"lead_batch2_{i:04d}",
                    "decision_maker_name": dm_name,
                    "decision_maker_title": dm_title,
                    "decision_maker_email": email,
                    "decision_maker_email_status": email_status,
                    "decision_maker_linkedin": dm_li,
                    "decision_maker_location": location,
                    "run_id": f"batch2_prefilled_{campaign.campaign_id}",
                }
            )
        else:
            without.append(seed)

    if args.write_raw:
        write_stage_csv(campaign, "raw_seeds", raw_seeds)

    # Need-Apollo list becomes stage 03 input for stage_apollo.py
    need_path = write_stage_csv(campaign, "deduped", without)
    # Prefill contactable with known emails (will merge after Apollo)
    prefill_path = campaign.stages_dir / "04_apollo_contactable_prefill.csv"
    if with_email:
        fields = list(with_email[0].keys())
        with prefill_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(with_email)
    else:
        prefill_path.write_text("", encoding="utf-8")

    missing_path = campaign.stages_dir / "03_need_apollo.csv"
    if without:
        fields = list(without[0].keys())
        with missing_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(without)

    print(f"Total rows:        {len(raw_seeds)}")
    print(f"Already have email: {len(with_email)} -> {prefill_path}")
    print(f"Need Apollo:        {len(without)} -> {need_path}")
    print()
    print("Next (email only, no phones):")
    print(
        f'  $env:APOLLO_SKIP_PHONE="1"; '
        f"python scripts/stage_apollo.py --campaign {args.campaign} "
        f"--input {need_path} --max-leads {len(without) or 1} "
        f"--client-id {campaign.client_id}-batch2"
    )
    print(
        f"  python scripts/merge_apollo_prefill.py --campaign {args.campaign}"
    )
    print(f"  python scripts/stage_enrich.py --campaign {args.campaign}")
    print(f"  python scripts/stage_score.py --campaign {args.campaign}")
    print(f"  python scripts/stage_qa_flags.py --campaign {args.campaign}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
