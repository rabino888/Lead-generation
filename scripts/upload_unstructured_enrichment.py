"""
Upload Automata first-list enrichment JSONs (unstructured project) to a Google Sheet.

Joins Results/enrichment/*.json with Datasets/Automata/prospects_tbo_automata.csv.

Usage:
  python scripts/upload_unstructured_enrichment.py
  python scripts/upload_unstructured_enrichment.py --sheet-name "Automata batch1 enriched"
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.models import (
    ClientProfile,
    Contact,
    EnrichmentStatus,
    ICPProfile,
    InputMode,
    Lead,
    RunState,
    RunStatus,
    SenderProfile,
    WebsiteAnalysis,
)
from agent.stages import report as report_stage
from agent.utils.campaign_config import load_campaign, load_run_payload

UNSTRUCTURED = Path(r"C:\Users\ravi_\TBO-agents\Leads generation unstructured")
ENRICH_DIR = UNSTRUCTURED / "Results" / "enrichment"
PROSPECTS_CSV = UNSTRUCTURED / "Datasets" / "Automata" / "prospects_tbo_automata.csv"

_BLOG_RE = re.compile(r"/blog|/insights|/news|/resources|/articles|/press", re.I)


def _domain(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if url.startswith("http") else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def _parse_dm(cell: str) -> tuple[str, str, str]:
    """'Justin King, Owner/MD ... - linkedin.com/in/foo' -> name, title, linkedin."""
    cell = (cell or "").strip()
    if not cell:
        return "", "", ""
    li = ""
    m = re.search(r"(https?://)?(www\.)?linkedin\.com/in/[\w%-]+", cell, re.I)
    if m:
        li = m.group(0)
        if not li.startswith("http"):
            li = "https://" + li.lstrip("/")
        cell = cell[: m.start()].rstrip(" -")
    name, title = cell, ""
    if "," in cell:
        name, title = cell.split(",", 1)
    return name.strip(), title.strip(" -"), li


def _load_prospects() -> dict[str, dict]:
    by_domain: dict[str, dict] = {}
    with PROSPECTS_CSV.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            website = (row.get("Website") or "").strip()
            dom = _domain(website)
            if not dom:
                continue
            name, title, li = _parse_dm(row.get("Decision Maker") or "")
            by_domain[dom] = {
                "company_name": (row.get("Company") or "").strip(),
                "website": website,
                "email": (row.get("Email") or "").strip(),
                "location": (row.get("Location") or "").strip(),
                "company_size": (row.get("Headcount") or "").strip(),
                "segment": (row.get("Segment") or "").strip(),
                "dm_name": name,
                "dm_title": title,
                "dm_linkedin": li,
                "notes": (row.get("Notes / Caveats") or "").strip(),
            }
    return by_domain


def _website_from_markdown(md: str | None) -> WebsiteAnalysis:
    text = md or ""
    has_blog = bool(_BLOG_RE.search(text)) if text else None
    summary = ""
    if text:
        first = text.split("\n\n---\n\n")[0]
        summary = re.sub(r"\s+", " ", first).strip()[:900]
        if len(first) > 900:
            summary += "…"
    return WebsiteAnalysis(
        has_blog=has_blog,
        website_summary=summary or None,
        raw_markdown_length=len(text) if text else None,
    )


def _json_to_lead(path: Path, prospects: dict[str, dict], *, run_id: str, client_id: str) -> Lead | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    website = (data.get("website") or "").strip()
    dom = _domain(website)
    prospect = prospects.get(dom) or {}
    email = (prospect.get("email") or "").strip()
    if not email:
        # Still upload if we have a name+company — report stage needs email for "qualified"
        # Prefer skip without email to match contactability gate
        return None

    profile = data.get("profile") or {}
    posts = data.get("posts") or []
    interests = data.get("interests") or []
    company = prospect.get("company_name") or path.stem
    dm_name = (
        profile.get("name")
        or data.get("name")
        or prospect.get("dm_name")
        or None
    )
    dm = Contact(
        name=dm_name,
        title=prospect.get("dm_title") or None,
        email=email,
        location=profile.get("location") or prospect.get("location") or None,
        linkedin_url=(
            profile.get("linkedin_url")
            or data.get("linkedin_url")
            or prospect.get("dm_linkedin")
            or None
        ),
        linkedin_posts=posts if isinstance(posts, list) else [],
        linkedin_interests=[str(i) for i in interests] if isinstance(interests, list) else [],
        linkedin_post_summary=data.get("post_summary") or None,
        linkedin_about=profile.get("about") or None,
        linkedin_headline=profile.get("headline") or None,
    )
    return Lead(
        lead_id=f"unstructured_{path.stem}",
        run_id=run_id,
        client_id=client_id,
        input_mode=InputMode.CURATED_SEEDS,
        company_name=company,
        website=website or prospect.get("website"),
        industry=prospect.get("segment") or None,
        location=prospect.get("location") or None,
        company_size=prospect.get("company_size") or None,
        decision_maker=dm,
        website_analysis=_website_from_markdown(data.get("website_markdown")),
        qualification_notes=prospect.get("notes") or None,
        enrichment_status=EnrichmentStatus.COMPLETE,
        scraped_at=datetime.now(UTC),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload unstructured Automata enrichment to Sheet")
    parser.add_argument("--campaign", default="automata")
    parser.add_argument("--client-id", default="automata")
    parser.add_argument("--sheet-name", default=None)
    parser.add_argument("--enrich-dir", default=str(ENRICH_DIR))
    args = parser.parse_args()

    if not PROSPECTS_CSV.exists():
        print(f"ERROR: prospects CSV missing: {PROSPECTS_CSV}", file=sys.stderr)
        return 2
    enrich_dir = Path(args.enrich_dir)
    if not enrich_dir.is_dir():
        print(f"ERROR: enrich dir missing: {enrich_dir}", file=sys.stderr)
        return 2

    campaign = load_campaign(args.campaign)
    payload = load_run_payload(campaign)
    sender = SenderProfile(**payload["sender"])
    sender.client_id = args.client_id
    client = ClientProfile(
        client_id=sender.client_id,
        name=campaign.client_name,
        sender=sender,
        icp=ICPProfile(**payload["icp"]),
    )

    prospects = _load_prospects()
    run_id = f"upload_unstructured_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    leads: list[Lead] = []
    skipped = 0
    for path in sorted(enrich_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        lead = _json_to_lead(path, prospects, run_id=run_id, client_id=client.client_id)
        if lead is None:
            skipped += 1
            print(f"skip (no email match): {path.name}")
            continue
        leads.append(lead)

    if not leads:
        print("ERROR: no leads built from enrichment JSONs", file=sys.stderr)
        return 2

    sheet_name = args.sheet_name or (
        f"Automata batch1 enriched — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}"
    )
    run_state = RunState(
        run_id=run_id,
        client_id=client.client_id,
        status=RunStatus.COMPLETE,
        input_mode=InputMode.CURATED_SEEDS,
        keyword=sheet_name,
        max_leads=len(leads),
    )
    sheet_url, csv_out, _ = report_stage.run(
        qualified_leads=leads,
        partial_leads=[],
        client_profile=client,
        run_state=run_state,
        sheet_name=sheet_name,
    )
    print(f"Leads uploaded: {len(leads)} (skipped {skipped})")
    print(f"Sheet: {sheet_url}")
    print(f"Local CSV: {csv_out}")
    return 0 if sheet_url else 1


if __name__ == "__main__":
    raise SystemExit(main())
