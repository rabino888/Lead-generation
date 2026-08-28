"""
Diagnose Apollo contact search for a company — shows every candidate Apollo returns,
how they are ranked, and whether each title matches the campaign ICP job_titles.

Usage:
  python scripts/diagnose_apollo_contacts.py --campaign {campaign_id} \
      --company Synthesia --website https://www.synthesia.io
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(override=True)

from agent.integrations.apollo import (  # noqa: E402
    _search_contact_candidates,
    _seniority_rank,
)
from agent.models import ICPProfile  # noqa: E402
from agent.utils.job_titles import (  # noqa: E402
    primary_title,
    title_matches_icp,
    title_priority,
)


def _load_icp(campaign: str) -> ICPProfile:
    payload_path = Path("data") / "campaigns" / campaign / "run_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    return ICPProfile(**payload["icp"])


def _rank_candidates(people: list[dict], job_titles: list[str]) -> list[dict]:
    matched = [p for p in people if title_matches_icp(str(p.get("title") or ""), job_titles)]
    return sorted(
        matched,
        key=lambda p: (
            title_priority(str(p.get("title") or ""), job_titles),
            not bool(p.get("has_email")),
            _seniority_rank(str(p.get("seniority") or "")),
        ),
    )


def _print_person(idx: int, person: dict, job_titles: list[str], *, selected: bool = False) -> None:
    raw_title = person.get("title") or ""
    title = primary_title(raw_title)
    seniority = person.get("seniority") or ""
    matches = title_matches_icp(raw_title, job_titles)
    priority = title_priority(raw_title, job_titles)
    rank = _seniority_rank(str(seniority))
    marker = " <-- WOULD BE SELECTED" if selected else ""
    print(
        f"  [{idx}] {person.get('name', '?')}\n"
        f"       raw_title:  {raw_title[:120]}{'...' if len(raw_title) > 120 else ''}\n"
        f"       primary:    {title}\n"
        f"       seniority:  {seniority}\n"
        f"       has_email:  {person.get('has_email')}\n"
        f"       location:   {person.get('city')}, {person.get('country')}\n"
        f"       priority:   {priority} | icp_match: {matches} | seniority_rank: {rank}{marker}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Apollo contact candidate selection")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--website", required=True)
    args = parser.parse_args()

    icp = _load_icp(args.campaign)
    job_titles = icp.job_titles
    contact_locations = icp.contact_locations

    print("=" * 70)
    print(f"CAMPAIGN: {args.campaign}")
    print(f"COMPANY:  {args.company} | {args.website}")
    print(f"ICP job_titles ({len(job_titles)}):")
    for t in job_titles:
        print(f"  - {t}")
    if contact_locations:
        print(f"contact_locations: {', '.join(contact_locations)}")
    print("=" * 70)

    people = _search_contact_candidates(
        company_name=args.company,
        website=args.website,
        job_titles=job_titles,
        max_candidates=25,
        contact_locations=contact_locations or None,
    )

    if not people:
        print("Apollo returned 0 candidates.")
        return 1

    print(f"\nRAW CANDIDATES ({len(people)}):\n")
    for i, person in enumerate(people, 1):
        _print_person(i, person, job_titles)

    ranked = _rank_candidates(people, job_titles)
    print(f"\nAFTER ICP TITLE FILTER + PRIORITIZATION ({len(ranked)} kept):\n")
    first_with_email = None
    for i, person in enumerate(ranked, 1):
        if person.get("has_email") and first_with_email is None:
            first_with_email = person
        _print_person(i, person, job_titles, selected=(person is first_with_email))

    matching = [p for p in people if title_matches_icp(p.get("title") or "", job_titles)]
    print(f"\nICP TITLE FILTER: {len(matching)}/{len(people)} candidates match job_titles")
    if matching:
        print("Matching candidates:")
        for person in matching:
            print(
                f"  - {person.get('name')} | {primary_title(person.get('title') or '')} "
                f"| has_email={person.get('has_email')}"
            )
    else:
        print("  (none)")

    if ranked:
        selected = ranked[0]
        print(
            f"\nSELECTED AFTER FIX: {selected.get('name')} | "
            f"{primary_title(selected.get('title') or '')}"
        )
    elif first_with_email and not title_matches_icp(first_with_email.get("title") or "", job_titles):
        print(
            f"\nROOT CAUSE: Selected contact '{first_with_email.get('name')}' "
            f"({first_with_email.get('title')}) does NOT match any ICP job_title."
        )
        print("Likely drivers:")
        print("  1. include_similar_titles=true lets Apollo return adjacent roles (e.g. Product Manager)")
        print("  2. person_seniorities[] includes 'manager' — matches Product Manager seniority")
        print("  3. No post-search title validation before email reveal prioritization")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
