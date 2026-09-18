"""
Talent search orchestrator (T01->T06).

Locked order: seed -> dedupe -> person ICP -> personal profile -> Apollo email
-> optional Apollo phone (when modules.apollo_phone).

Requires enrichment_plan.json (or campaign.json inference via load_plan) with
``campaign_type: talent_search``. Refuses ``company_outreach`` — use
``scripts/run_deterministic_campaign.py`` for the company funnel.

Usage:
  python scripts/run_talent_campaign.py --campaign {campaign_id}
  python scripts/run_talent_campaign.py --campaign {id} --source people_csv_ingest --csv people.csv
  python scripts/run_talent_campaign.py --campaign {id} --source linkedin_people --url "..."
  python scripts/run_talent_campaign.py --campaign {id} --skip-paid
  python scripts/run_talent_campaign.py --campaign {id} --match-only
  python scripts/run_talent_campaign.py --campaign {id} --skip-phone

Dry-run / unpaid path:
  ``--skip-paid`` and ``--match-only`` are aliases. Both run T01 (if needed) ->
  T02 -> T03, then stop **before** T04 profile and T05 Apollo (no Apify profile
  or Apollo spend). Existing ``stages/T01_raw_people.csv`` is reused when
  ``--source`` is omitted.

Phone (T06):
  Runs only when ``enrichment_plan.modules.apollo_phone`` is true and
  ``--skip-phone`` is not set. Requires APOLLO_API_KEY + phone webhook URL.
  Merge webhook payloads later with ``import_apollo_phone_webhooks.py``.

Smoke (refuse company plan without a real campaign tree)::

  from scripts.run_talent_campaign import talent_plan_gate
  assert talent_plan_gate({"campaign_type": "company_outreach"}) is not None
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

from agent.utils.campaign_config import load_campaign
from agent.utils.enrichment_plan import load_plan
from agent.utils.icp_rules import ensure_stages_dir
from agent.utils.talent_people import STAGE_T01_RAW_PEOPLE
from agent.utils.talent_phone import phone_prereqs_ok
from scripts.stage_talent_apollo import main as apollo_main
from scripts.stage_talent_dedupe import main as dedupe_main
from scripts.stage_talent_icp import main as icp_main
from scripts.stage_talent_phone import main as phone_main
from scripts.stage_talent_profile import main as profile_main
from scripts.stage_talent_seed import main as seed_main

TALENT_SOURCES = ("people_csv_ingest", "linkedin_people")


def talent_plan_gate(plan: dict[str, Any]) -> Optional[str]:
    """
    Return an error message if ``plan`` is not talent_search; else None.

    Used by the orchestrator and unit-tested without loading a campaign tree.
    """
    ctype = (plan.get("campaign_type") or "").strip() or "company_outreach"
    if ctype == "talent_search":
        return None
    if ctype == "company_outreach":
        return (
            "ERROR: this is a company_outreach campaign. "
            "Use scripts/run_deterministic_campaign.py — not run_talent_campaign.py."
        )
    return (
        f"ERROR: enrichment_plan campaign_type={ctype!r} "
        "(expected talent_search)."
    )


def talent_phone_gate(
    plan: dict[str, Any],
    *,
    skip_phone: bool = False,
) -> tuple[str, Optional[str]]:
    """
    Decide T06 phone action from plan + CLI.

    Returns (action, error_or_none) where action is:
      ``run`` | ``skip_plan_off`` | ``skip_flag`` | ``error``
    """
    if skip_phone:
        return "skip_flag", None
    modules = plan.get("modules") or {}
    if not bool(modules.get("apollo_phone")):
        return "skip_plan_off", None
    ok, err = phone_prereqs_ok()
    if not ok:
        return "error", f"ERROR: {err}"
    return "run", None


def _run_stage(label: str, stage_main: Callable[[], int], argv: Sequence[str]) -> int:
    old = sys.argv
    try:
        sys.argv = [label, *argv]
        return int(stage_main() or 0)
    finally:
        sys.argv = old


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run talent search funnel T01->T06 (phone optional)"
    )
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--source",
        default="",
        help="Optional T01 source: people_csv_ingest | linkedin_people",
    )
    parser.add_argument("--csv", default="", help="Person CSV for people_csv_ingest")
    parser.add_argument("--url", action="append", default=[], help="LinkedIn people-search URL")
    parser.add_argument("--url-file", default="")
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--actor", default="", help="Override Apify people-search actor")
    parser.add_argument(
        "--skip-paid",
        action="store_true",
        help="Stop after T03 ICP (before profile + Apollo)",
    )
    parser.add_argument(
        "--match-only",
        action="store_true",
        help="Alias for --skip-paid",
    )
    parser.add_argument(
        "--skip-phone",
        action="store_true",
        help="Force skip T06 even when modules.apollo_phone is on",
    )
    parser.add_argument("--client-id", default=None)
    parser.add_argument(
        "--max-leads",
        type=int,
        default=None,
        help="Cap T04 profile rows / T05 Apollo attempts",
    )
    parser.add_argument(
        "--sheets-dedup",
        action="store_true",
        help="Pass --sheets-dedup to T02",
    )
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    ensure_stages_dir(campaign)
    plan = load_plan(campaign.campaign_dir, campaign.campaign_id)

    gate_err = talent_plan_gate(plan)
    if gate_err:
        print(gate_err, file=sys.stderr)
        return 2

    source = (args.source or "").strip().lower()
    if source and source not in TALENT_SOURCES:
        print(
            f"ERROR: --source must be one of {', '.join(TALENT_SOURCES)} "
            f"(got {args.source!r})",
            file=sys.stderr,
        )
        return 2

    t01_path = campaign.stages_dir / STAGE_T01_RAW_PEOPLE
    t01_rows = 0
    if t01_path.is_file():
        try:
            import csv as _csv

            with t01_path.open(encoding="utf-8-sig", newline="") as f:
                t01_rows = sum(
                    1
                    for r in _csv.DictReader(f)
                    if (r.get("linkedin_url") or r.get("full_name") or "").strip()
                )
        except Exception:
            t01_rows = 0
    need_seed = bool(source) or t01_rows == 0

    # ── T01 seed (if needed) ──────────────────────────────────────────────────
    if need_seed:
        seed_argv = ["--campaign", args.campaign]
        if source:
            seed_argv.extend(["--source", source])
        if args.csv:
            seed_argv.extend(["--csv", args.csv])
        for u in args.url or []:
            seed_argv.extend(["--url", u])
        if args.url_file:
            seed_argv.extend(["--url-file", args.url_file])
        if args.max_results:
            seed_argv.extend(["--max-results", str(args.max_results)])
        if args.actor:
            seed_argv.extend(["--actor", args.actor])
        rc = _run_stage("stage_talent_seed.py", seed_main, seed_argv)
        if rc != 0:
            return rc
    else:
        print(f"Reusing existing {t01_path.name}")

    # ── T02 dedupe ────────────────────────────────────────────────────────────
    dedupe_argv = ["--campaign", args.campaign]
    if args.client_id:
        dedupe_argv.extend(["--client-id", args.client_id])
    if args.sheets_dedup:
        dedupe_argv.append("--sheets-dedup")
    rc = _run_stage("stage_talent_dedupe.py", dedupe_main, dedupe_argv)
    if rc != 0:
        return rc

    # ── T03 person ICP ────────────────────────────────────────────────────────
    rc = _run_stage(
        "stage_talent_icp.py",
        icp_main,
        ["--campaign", args.campaign],
    )
    if rc != 0:
        return rc

    if args.skip_paid or args.match_only:
        print("Stopping after T03 (--skip-paid / --match-only); skipped T04+T05+T06")
        return 0

    # ── T04 personal profile ──────────────────────────────────────────────────
    profile_argv = ["--campaign", args.campaign]
    if args.client_id:
        profile_argv.extend(["--client-id", args.client_id])
    if args.max_leads is not None:
        profile_argv.extend(["--limit", str(args.max_leads)])
    rc = _run_stage("stage_talent_profile.py", profile_main, profile_argv)
    if rc != 0:
        return rc

    # ── T05 Apollo email ──────────────────────────────────────────────────────
    apollo_argv = ["--campaign", args.campaign]
    if args.client_id:
        apollo_argv.extend(["--client-id", args.client_id])
    if args.max_leads is not None:
        apollo_argv.extend(["--max-leads", str(args.max_leads)])
    rc = _run_stage("stage_talent_apollo.py", apollo_main, apollo_argv)
    if rc != 0:
        return rc

    # ── T06 Apollo phone (optional) ───────────────────────────────────────────
    phone_action, phone_err = talent_phone_gate(plan, skip_phone=args.skip_phone)
    if phone_action == "skip_flag":
        print("Skipping T06 phone (--skip-phone)")
    elif phone_action == "skip_plan_off":
        print("Skipping T06 phone (modules.apollo_phone off in enrichment_plan)")
    elif phone_action == "error":
        print(phone_err, file=sys.stderr)
        return 2
    else:
        phone_argv = ["--campaign", args.campaign]
        if args.max_leads is not None:
            phone_argv.extend(["--limit", str(args.max_leads)])
        rc = _run_stage("stage_talent_phone.py", phone_main, phone_argv)
        if rc != 0:
            return rc

    print(f"Talent funnel complete. Stages: {campaign.stages_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
