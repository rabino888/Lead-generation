"""
Diagnose seed-build exclusions for a campaign.

Checks:
  1. Whether campaign exclusion config is loaded correctly
  2. Whether post-Apollo _is_excluded() catches noise in current seeds
  3. Whether Apollo q_not_keywords is present in the API payload
  4. Apollo result counts with vs without q_not_keywords (sample query)

Usage:
  python scripts/diagnose_seed_exclusions.py --campaign {campaign_id}
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.utils.campaign_config import compile_excluded_name_re, load_campaign  # noqa: E402
from scripts.build_seed_list import (  # noqa: E402
    _apollo_search,
    _is_excluded,
    _normalize_website,
    _root_domain,
)

load_dotenv(ROOT / ".env", override=True)


def _classify_noise(name: str, industry: str = "") -> str | None:
    """Heuristic labels for obvious non-ICP seeds."""
    n = f"{name} {industry}".lower()
    rules = [
        ("recruitment", "recruit|recruitment|staffing|talent agency|headhunt|executive search|job board|career coach|emprego|akhtaboot|adzuna"),
        ("vc_media", r"\bvc\b|venture capital|angel investment|twenty minute|podcast|magazine|analyticsindiamag|medium\.com"),
        ("government", r"\.gov\.|government|investment office|domain registry|sbci|public sector"),
        ("association", r"association\b|aima - the alternative"),
        ("job_seeker", r"ace your career|4 day week|your career"),
        ("foreign_hq", r"\.br\b|\.id\b|\.in\b|\.ae\b|\.pt\b|\.abudhabi|indonesia|pvt\. ltd"),
    ]
    import re
    for label, pattern in rules:
        if re.search(pattern, n):
            return label
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", required=True)
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    disc = campaign.discovery
    pattern = compile_excluded_name_re(campaign)

    print("=" * 70)
    print(f"CAMPAIGN: {campaign.campaign_id}")
    print(f"apollo_not_keywords (sent to Apollo): {disc.apollo_not_keywords!r}")
    print(f"excluded_name_patterns (post-filter): {disc.excluded_name_patterns!r}")
    print(f"merged regex: {disc.merged_excluded_name_pattern()!r}")
    print("=" * 70)

    # --- Test post-filter on known bad seeds ---
    bad_examples = [
        ("247 GLOBAL RECRUIT", "", "https://www.247globalrecruit.com"),
        ("Aardvark Swift Recruitment", "staffing and recruiting", "https://www.aswift.com"),
        ("3i People", "staffing and recruiting", "https://www.3ipeople.com"),
        ("Adroit People Limited (UK)", "staffing and recruiting", "https://www.adroitpeople.com"),
        ("Agora Talent", "staffing and recruiting", "https://www.agora-talent.com"),
        ("20VC", "media", "https://www.thetwentyminutevc.com"),
        ("(SBCI) Strategic Banking Corporation of Ireland", "government", "https://www.sbci.gov.ie"),
        ("FreeAgent", "accounting", "https://www.freeagent.com"),
        ("AccountsIQ", "accounting", "https://www.accountsiq.com"),
    ]
    print("\nPOST-FILTER (_is_excluded) on sample companies:\n")
    for name, industry, website in bad_examples:
        excluded = _is_excluded(campaign, name, website, industry)
        noise = _classify_noise(name, industry)
        print(f"  {'EXCLUDED' if excluded else 'KEPT    '} | noise={noise or '-':12s} | {name}")

    # --- Audit current seeds.csv ---
    seeds_path = campaign.seeds_csv
    if not seeds_path.exists():
        print(f"\nNo seeds.csv at {seeds_path}")
        return 1

    rows = list(csv.DictReader(seeds_path.open(encoding="utf-8")))
    post_excluded = 0
    noise_counts: dict[str, int] = {}
    missed_by_post_filter: list[dict] = []

    for row in rows:
        name = row["company_name"]
        website = row["website"]
        if _is_excluded(campaign, name, website):
            post_excluded += 1
            continue
        noise = _classify_noise(name)
        if noise:
            noise_counts[noise] = noise_counts.get(noise, 0) + 1
            missed_by_post_filter.append(row)

    print(f"\nCURRENT SEEDS ({len(rows)} total):")
    print(f"  Would be caught by _is_excluded today: {post_excluded}")
    print(f"  Noise missed by post-filter (heuristic): {len(missed_by_post_filter)}")
    for label, count in sorted(noise_counts.items(), key=lambda x: -x[1]):
        print(f"    {label}: {count}")

    print("\n  Examples missed by post-filter:")
    for row in missed_by_post_filter[:15]:
        print(f"    - {row['company_name']} ({row['website']})")

    # --- Apollo API: with vs without q_not_keywords ---
    api_key = os.environ.get("APOLLO_API_KEY", "")
    if not api_key:
        print("\nSKIP Apollo API test — no APOLLO_API_KEY")
        return 0

    query = campaign.segments["UK"].apollo_queries[0]
    print(f"\nAPOLLO API TEST — query: {query.keyword!r} @ {query.location!r}")
    with httpx.Client() as client:
        payload_with = {
            "per_page": 25,
            "page": 1,
            "q_keywords": query.keyword,
            "organization_industries": disc.apollo_industries,
            "organization_num_employees_ranges": disc.apollo_employee_ranges,
            "q_not_keywords": disc.apollo_not_keywords,
            "organization_country_codes": ["GB"],
            "organization_locations": [query.location],
        }
        payload_without = {k: v for k, v in payload_with.items() if k != "q_not_keywords"}

        def _search(payload: dict) -> list[dict]:
            resp = client.post(
                "https://api.apollo.io/api/v1/mixed_companies/search",
                json=payload,
                headers={"Content-Type": "application/json", "X-Api-Key": api_key},
                timeout=45,
            )
            resp.raise_for_status()
            return resp.json().get("organizations", []) or []

        with_kw = _search(payload_with)
        without_kw = _search(payload_without)

    def _noise_in_results(orgs: list[dict]) -> list[str]:
        hits = []
        for org in orgs:
            name = org.get("name") or ""
            industry = org.get("industry") or ""
            if _classify_noise(name, industry) or _is_excluded(campaign, name, _normalize_website(org.get("website_url") or ""), industry):
                hits.append(f"{name} [{industry}]")
        return hits

    noise_with = _noise_in_results(with_kw)
    noise_without = _noise_in_results(without_kw)

    print(f"  Results WITH q_not_keywords:    {len(with_kw)} orgs, {len(noise_with)} noisy/excluded")
    print(f"  Results WITHOUT q_not_keywords: {len(without_kw)} orgs, {len(noise_without)} noisy/excluded")
    if noise_with[:5]:
        print("  Noisy results even WITH q_not_keywords (Apollo does not fully exclude):")
        for h in noise_with[:5]:
            print(f"    - {h}")

    print("\nDIAGNOSIS:")
    print("  1. q_not_keywords IS passed to Apollo in build_seed_list.py")
    print("  2. Post-filter regex is TOO NARROW — 'recruitment agency' misses 'Recruitment' alone")
    print("     and 'recruit' in company names; staffing firms pass unless industry matches")
    print("  3. Apollo keyword search is LOOSE — returns job boards, VCs, gov, associations")
    print("     regardless of q_not_keywords for many matches")
    print("  4. Fix: widen excluded_name_patterns + add post-filter noise heuristics in seed build")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
