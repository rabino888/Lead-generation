"""
Smoke-test runner for the deterministic funnel.

Modes:
  icp          — $0: run ICP match on --seed / --input; assert pass/fail
  post_dedupe  — paid (required before full batch): Apollo+email → enrich → score
  apollo       — Apollo+email only; stop before Firecrawl
  full         — same as post_dedupe (legacy alias)

Usage:
  python scripts/run_smoke_test.py --campaign {campaign_id} --mode icp \\
      --seed "Good Co=https://good.example" --seed "Bad Staffing Agency=https://bad.example" \\
      --expect-reject "Bad Staffing Agency"

  python scripts/run_smoke_test.py --campaign {campaign_id} --mode post_dedupe \\
      --seed "Company One=https://www.one.com" --seed "Company Two=https://www.two.com"

  python scripts/run_smoke_test.py --campaign {campaign_id} --mode apollo --input stages/03_deduped.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.models import (  # noqa: E402
    ClientProfile,
    CompanySeed,
    ICPProfile,
    InputMode,
    RunRequest,
    SenderProfile,
)
from agent.pipeline import run_pipeline  # noqa: E402
from agent.utils.campaign_config import load_campaign, load_run_payload  # noqa: E402
from agent.utils.icp_match import match_company  # noqa: E402
from agent.utils.icp_rules import ensure_stages_dir, load_icp_rules  # noqa: E402
from agent.utils.run_tracker import create_run, get_run  # noqa: E402
from agent.utils.stage_artifacts import write_stage_csv  # noqa: E402
from agent.utils.stage_runner import rows_to_raw_companies  # noqa: E402


def _parse_seeds(raw_seeds: list[str]) -> list[CompanySeed]:
    seeds: list[CompanySeed] = []
    for raw in raw_seeds:
        name, _, website = raw.partition("=")
        if not name or not website:
            raise ValueError(f"bad --seed format: {raw!r} (expected Name=https://site)")
        seeds.append(CompanySeed(company_name=name.strip(), website=website.strip()))
    return seeds


def _seeds_from_input(path: Path, limit: int) -> list[CompanySeed]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out: list[CompanySeed] = []
    for row in rows:
        name = (row.get("company_name") or "").strip()
        if not name:
            continue
        out.append(
            CompanySeed(
                company_name=name,
                website=(row.get("website") or "").strip() or None,
                location=(row.get("location") or "").strip() or None,
                industry=(row.get("industry") or "").strip() or None,
            )
        )
        if len(out) >= limit:
            break
    return out


def _smoke_icp(args: argparse.Namespace) -> int:
    campaign = load_campaign(args.campaign)
    rules = load_icp_rules(campaign)
    seeds = _parse_seeds(args.seed or [])
    if args.input:
        seeds = _seeds_from_input(Path(args.input), args.max_seeds) + seeds
    if not seeds:
        print("ERROR: provide --seed and/or --input", file=sys.stderr)
        return 2

    expect_reject = {n.strip().lower() for n in (args.expect_reject or [])}
    failures = 0
    for seed in seeds:
        row = {
            "company_name": seed.company_name,
            "website": seed.website or "",
            "location": seed.location or "",
            "industry": seed.industry or "",
        }
        passed, reasons = match_company(row, rules)
        want_reject = seed.company_name.lower() in expect_reject
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {seed.company_name}: {reasons}")
        if want_reject and passed:
            print(f"    ERROR: expected reject for {seed.company_name}", file=sys.stderr)
            failures += 1
        if not want_reject and not passed:
            print(f"    ERROR: expected pass for {seed.company_name}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


def _smoke_paid(args: argparse.Namespace, *, stop_after_apollo: bool) -> int:
    campaign = load_campaign(args.campaign)
    ensure_stages_dir(campaign)
    payload = load_run_payload(campaign)
    sender = SenderProfile(**payload["sender"])
    icp = ICPProfile(**payload["icp"])

    if args.client_id:
        sender.client_id = args.client_id
    elif not args.keep_client_id:
        sender.client_id = f"{sender.client_id}-smoketest"

    seeds: list[CompanySeed] = []
    if args.input:
        seeds.extend(_seeds_from_input(Path(args.input), args.max_seeds))
    if args.seed:
        seeds.extend(_parse_seeds(args.seed))
    seeds = seeds[: args.max_seeds]
    if not seeds:
        print("ERROR: provide --seed and/or --input", file=sys.stderr)
        return 2

    # Write a tiny deduped stage for inspection
    write_stage_csv(
        campaign,
        "deduped",
        [
            {
                "company_name": s.company_name,
                "website": s.website or "",
                "location": s.location or "",
                "industry": s.industry or "",
                "status": "smoke",
            }
            for s in seeds
        ],
    )

    if stop_after_apollo:
        # Delegate to stage_apollo with smoketest client
        from scripts.stage_apollo import main as apollo_main

        old = sys.argv
        try:
            sys.argv = [
                "stage_apollo.py",
                "--campaign",
                args.campaign,
                "--client-id",
                sender.client_id,
                "--max-leads",
                str(len(seeds)),
                "--input",
                str(campaign.stage_artifact_path("03_deduped.csv")),
            ]
            rc = int(apollo_main() or 0)
        finally:
            sys.argv = old
        return rc

    request = RunRequest(
        mode=InputMode.CURATED_SEEDS,
        company_seeds=seeds,
        max_leads=len(seeds),
        sender=sender,
        icp=icp,
        output_sheet_name=f"SMOKE TEST — {args.campaign}",
    )
    client = ClientProfile(
        client_id=sender.client_id,
        name=f"{sender.name} (smoke test)",
        sender=sender,
        icp=icp,
    )
    run_state = create_run(
        client_id=client.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=len(seeds),
        keyword=f"smoke:{args.campaign}",
    )
    print(
        f"Smoke post_dedupe run {run_state.run_id} — {len(seeds)} seeds, "
        f"client={client.client_id}"
    )
    asyncio.run(run_pipeline(run_state.run_id, request, client))
    final = get_run(run_state.run_id)
    print(f"Status: {final.status if final else 'unknown'}")
    if final and final.csv_path:
        print(f"CSV: {final.csv_path}")
    if final and final.sheet_url:
        print(f"Sheet: {final.sheet_url}")
    status = getattr(final.status, "value", str(final.status)).lower() if final else ""
    return 0 if status == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic funnel smoke tests")
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--mode",
        choices=["icp", "post_dedupe", "apollo", "full"],
        default="full",
        help="icp=$0 match; post_dedupe/full=Apollo+enrich+score; apollo=Apollo only",
    )
    parser.add_argument("--seed", action="append", default=[], help='"Name=https://site"')
    parser.add_argument("--input", default="", help="CSV of seeds (takes first --max-seeds rows)")
    parser.add_argument("--max-seeds", type=int, default=2)
    parser.add_argument("--expect-reject", action="append", default=[], help="ICP mode: company names that must fail")
    parser.add_argument("--client-id", default=None)
    parser.add_argument("--keep-client-id", action="store_true")
    args = parser.parse_args()

    if args.mode == "icp":
        return _smoke_icp(args)
    if args.mode == "apollo":
        return _smoke_paid(args, stop_after_apollo=True)
    # post_dedupe + full
    return _smoke_paid(args, stop_after_apollo=False)


if __name__ == "__main__":
    raise SystemExit(main())
