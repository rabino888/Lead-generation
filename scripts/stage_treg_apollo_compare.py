"""
Side-by-side decision-maker email pull: Apollo (own key) and treg.

treg calls are capped with X-Treg-Route-Max-Cost (default $0.20) and Apollo is
excluded from the treg route so the two sources stay independent.

Usage:
  python scripts/stage_treg_apollo_compare.py --campaign {id}
  python scripts/stage_treg_apollo_compare.py --campaign {id} --max-cost 0.20
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.integrations.apollo import enrich_contacts, get_credits_used, reset_credits_used
from agent.integrations.treg import (
    call_catalog,
    email_from_find_payload,
    people_from_search_payload,
    person_contact_fields,
    pick_decision_maker,
    treg_served_by,
)
from agent.utils.campaign_config import load_campaign, load_icp
from agent.utils.icp_match import _root_domain
from agent.utils.stage_artifacts import read_stage_csv

COMPARE_FIELDS = [
    "seed_id",
    "company_name",
    "website",
    "domain",
    "company_size",
    "industry",
    "apollo_name",
    "apollo_title",
    "apollo_email",
    "apollo_linkedin",
    "apollo_status",
    "treg_name",
    "treg_title",
    "treg_email",
    "treg_linkedin",
    "treg_search_usd",
    "treg_email_usd",
    "treg_served_by",
    "treg_status",
]


def _route_headers(max_cost: float) -> dict[str, str]:
    return {
        "X-Treg-Route-Max-Cost": f"{max_cost:.2f}",
        "X-Treg-Route-Exclude": "apollo",
    }


def _email_identity(domain: str, fields: dict[str, str]) -> dict[str, str] | None:
    """One complete treg email-find identity. Empty fields are omitted."""
    linkedin = (fields.get("linkedin_url") or "").strip()
    if "linkedin.com/in/" in linkedin.lower():
        return {"linkedin_url": linkedin}
    full = (fields.get("name") or "").strip()
    first = (fields.get("first_name") or "").strip()
    last = (fields.get("last_name") or "").strip()
    if not first or not last:
        parts = [part for part in full.split() if part]
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
    if domain and first and last:
        return {"domain": domain, "first_name": first, "last_name": last}
    if domain and full and " " in full:
        return {"domain": domain, "full_name": full}
    return None


def _lookup_treg(
    *,
    domain: str,
    job_titles: list[str],
    max_cost: float,
    idempotency_prefix: str,
) -> dict[str, str]:
    headers = _route_headers(max_cost)
    out = {
        "treg_name": "",
        "treg_title": "",
        "treg_email": "",
        "treg_linkedin": "",
        "treg_search_usd": "0",
        "treg_email_usd": "0",
        "treg_served_by": "",
        "treg_status": "",
    }
    search_data, search_usd = call_catalog(
        "treg.people.search",
        method="POST",
        body={
            "company_domain": domain,
            "country": "US",
            "location": "United States",
            "limit": 5,
        },
        headers=headers,
        idempotency_key=f"{idempotency_prefix}:{domain}:people.search",
        timeout=120,
    )
    out["treg_search_usd"] = f"{search_usd:.4f}"
    served = treg_served_by(search_data)
    people = people_from_search_payload(search_data)
    person = pick_decision_maker(people, job_titles)
    if not person:
        out["treg_status"] = "no_person"
        out["treg_served_by"] = served
        return out
    fields = person_contact_fields(person)
    out["treg_name"] = fields["name"]
    out["treg_title"] = fields["title"]
    out["treg_linkedin"] = fields["linkedin_url"]
    out["treg_served_by"] = served
    if fields["email"]:
        out["treg_email"] = fields["email"]
        out["treg_status"] = "email_on_search"
        return out
    email_body = _email_identity(domain, fields)
    if not email_body:
        out["treg_status"] = "no_identity"
        return out
    email_data, email_usd = call_catalog(
        "treg.people.email.find",
        method="POST",
        body=email_body,
        headers=headers,
        idempotency_key=f"{idempotency_prefix}:{domain}:email.find:v2",
        timeout=120,
    )
    out["treg_email_usd"] = f"{email_usd:.4f}"
    found_by = treg_served_by(email_data)
    if found_by:
        out["treg_served_by"] = found_by
    email = email_from_find_payload(email_data)
    out["treg_email"] = email
    out["treg_status"] = "revealed" if email else "no_email"
    return out


def _lookup_apollo(company_name: str, website: str, job_titles: list[str], locations: list[str]) -> dict[str, str]:
    contact, _generic, reason = enrich_contacts(
        company_name=company_name,
        website=website,
        job_titles=job_titles,
        contact_locations=locations or None,
    )
    if contact and (contact.email or "").strip():
        return {
            "apollo_name": contact.name or "",
            "apollo_title": contact.title or "",
            "apollo_email": contact.email or "",
            "apollo_linkedin": contact.linkedin_url or "",
            "apollo_status": "revealed",
        }
    return {
        "apollo_name": "",
        "apollo_title": "",
        "apollo_email": "",
        "apollo_linkedin": "",
        "apollo_status": reason or "no_email",
    }


def _load_done(path: Path) -> set[str]:
    """Domains already compared. Rows whose treg call errored are retried."""
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    keep = [
        row for row in rows
        if not (row.get("treg_status") or "").startswith("error:")
    ]
    if len(keep) != len(rows):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COMPARE_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(keep)
    return {
        (row.get("domain") or "").strip().lower()
        for row in keep
        if (row.get("domain") or "").strip()
    }


def _append_row(path: Path, row: dict[str, str]) -> None:
    new_file = not path.is_file()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COMPARE_FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def _summarize(path: Path, *, apollo_credits: int, apollo_usd_per_credit: float) -> dict:
    rows = []
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    apollo_hits = [r for r in rows if (r.get("apollo_email") or "").strip()]
    treg_hits = [r for r in rows if (r.get("treg_email") or "").strip()]
    apollo_domains = {(r.get("domain") or "").lower() for r in apollo_hits}
    treg_domains = {(r.get("domain") or "").lower() for r in treg_hits}
    treg_usd = 0.0
    for row in rows:
        for key in ("treg_search_usd", "treg_email_usd"):
            try:
                treg_usd += float(row.get(key) or 0)
            except ValueError:
                pass
    apollo_usd = round(apollo_credits * apollo_usd_per_credit, 4)
    both = len(apollo_domains & treg_domains)
    return {
        "companies": len(rows),
        "apollo_emails": len(apollo_hits),
        "treg_emails": len(treg_hits),
        "both": both,
        "apollo_only": len(apollo_domains - treg_domains),
        "treg_only": len(treg_domains - apollo_domains),
        "neither": len(rows) - len(apollo_domains | treg_domains),
        "apollo_credits": apollo_credits,
        "apollo_usd": apollo_usd,
        "treg_usd": round(treg_usd, 4),
        "apollo_usd_per_email": round(apollo_usd / len(apollo_hits), 4) if apollo_hits else None,
        "treg_usd_per_email": round(treg_usd / len(treg_hits), 4) if treg_hits else None,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Apollo vs treg email comparison")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--max-cost", type=float, default=0.20, help="Per treg call cap in USD")
    parser.add_argument("--max-companies", type=int, default=0, help="0 = entire deduped list")
    args = parser.parse_args()

    os.environ["APOLLO_SKIP_PHONE"] = "1"
    campaign = load_campaign(args.campaign)
    icp = load_icp(campaign)
    rows = read_stage_csv(campaign, "deduped")
    if args.max_companies > 0:
        rows = rows[: args.max_companies]
    stages = campaign.campaign_dir / "stages"
    stages.mkdir(parents=True, exist_ok=True)
    out_path = stages / "04_source_compare.csv"
    summary_path = stages / "04_source_compare_summary.json"
    done = _load_done(out_path)

    from agent.models import InputMode
    from agent.utils import run_tracker
    from agent.utils.cost_manifest import finalize_stage_cost
    from agent.utils.cost_tracker import get_cost_tracker, init_cost_tracker

    reset_credits_used()
    started = datetime.now(timezone.utc)
    run_state = run_tracker.create_run(
        client_id=campaign.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=len(rows),
        keyword=f"source_compare:{campaign.campaign_id}",
    )
    init_cost_tracker(run_state.run_id, campaign.client_id)

    pending = []
    for row in rows:
        domain = _root_domain(row.get("website") or "")
        if not domain or domain in done:
            continue
        pending.append(row)
    print(f"Comparing {len(pending)} companies ({len(done)} already on disk) -> {out_path}")

    prefix = campaign.campaign_id
    for index, row in enumerate(pending, start=1):
        name = (row.get("company_name") or "").strip()
        website = (row.get("website") or "").strip()
        domain = _root_domain(website)
        print(f"[{index}/{len(pending)}] {name} ({domain})", flush=True)
        record = {key: "" for key in COMPARE_FIELDS}
        record.update(
            {
                "seed_id": row.get("seed_id") or "",
                "company_name": name,
                "website": website,
                "domain": domain,
                "company_size": row.get("company_size") or "",
                "industry": row.get("industry") or "",
            }
        )
        try:
            record.update(
                _lookup_apollo(name, website, list(icp.job_titles), list(icp.contact_locations))
            )
        except Exception as exc:
            record["apollo_status"] = f"error:{exc}"
        try:
            record.update(
                _lookup_treg(
                    domain=domain,
                    job_titles=list(icp.job_titles),
                    max_cost=args.max_cost,
                    idempotency_prefix=prefix,
                )
            )
        except Exception as exc:
            record["treg_status"] = f"error:{exc}"
        _append_row(out_path, record)
        print(
            f"  apollo={record['apollo_status'][:80]} email={record['apollo_email'] or '—'}"
            f" | treg={record['treg_status']} email={record['treg_email'] or '—'}"
            f" ${float(record['treg_search_usd'] or 0) + float(record['treg_email_usd'] or 0):.4f}",
            flush=True,
        )

    rate = float(os.environ.get("APOLLO_CREDIT_USD", "0.02"))
    credits = get_credits_used()
    summary = _summarize(out_path, apollo_credits=credits, apollo_usd_per_credit=rate)
    summary["max_treg_cost_per_call"] = args.max_cost
    summary["treg_route_exclude"] = "apollo"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

    tracker = get_cost_tracker()
    try:
        finalize_stage_cost(
            campaign_dir=campaign.campaign_dir,
            campaign_id=campaign.campaign_id,
            client_id=campaign.client_id,
            run_id=run_state.run_id,
            phase="source_compare",
            started_at=started,
            tracker_summary=tracker.run_summary() if tracker else None,
            label=f"Apollo vs treg emails ({summary['companies']} companies)",
            outcome=summary,
            extra={"output": str(out_path), "max_treg_cost_per_call": args.max_cost},
            apollo_extra={"credits_consumed": credits, "companies_attempted": summary["companies"]},
        )
    except Exception as exc:
        print(f"cost manifest skipped: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
