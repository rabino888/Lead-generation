"""Autopilot: company URL → plan → companies → optional sequence.

Three human gates. The sequence is off unless you pass --generate-sequence
at the last gate. Nothing here sends email.

  python scripts/autopilot.py plan --url https://example.com
  python scripts/autopilot.py approve --run ap_... --gate plan
  python scripts/autopilot.py approve --run ap_... --gate companies
  python scripts/autopilot.py approve --run ap_... --gate sequence --generate-sequence
  python scripts/autopilot.py approve --run ap_... --gate sequence
  python scripts/autopilot.py revise --run ap_... --notes "Only UK buyers"
  python scripts/autopilot.py status --run ap_...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.autopilot.orchestrator import approve, default_data_root, reject, start, status


def _print(state: dict) -> None:
    assistant = state.get("assistant") or {}
    print(f"run: {state.get('run_id')}")
    print(f"phase: {state.get('phase')}")
    print(f"campaign: {state.get('campaign_id')}")
    if state.get("halt_reason"):
        print(f"halted: {state.get('halt_reason')}")
    if assistant.get("headline"):
        print()
        print(assistant.get("headline"))
        print(assistant.get("narrative") or assistant.get("summary") or "")
        print(assistant.get("recommendation") or "")
    print()
    print((state.get("plan") or {}).get("markdown") or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lead-gen autopilot (three approval gates)")
    parser.add_argument("--data-root", default="", help="Defaults to DATA_ROOT or ./data")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Research a company URL and write a plan. No paid calls.")
    plan.add_argument("--url", required=True)
    plan.add_argument("--client-id", default="")
    plan.add_argument("--notes", default="")
    plan.add_argument("--target-leads", type=int, default=25)
    plan.add_argument("--max-usd", type=float, default=25.0)
    plan.add_argument("--seed-source", default="", help="Lock csv_ingest, google_search, google_maps, or linkedin_companies")
    plan.add_argument("--contact-provider", default="", choices=["", "apollo", "treg"])
    plan.add_argument("--csv", default="", help="Seed CSV when --seed-source csv_ingest")

    approve_p = sub.add_parser("approve", help="Approve the open gate and run until the next one")
    approve_p.add_argument("--run", required=True)
    approve_p.add_argument("--gate", default="", choices=["", "plan", "companies", "sequence"])
    approve_p.add_argument("--notes", default="")
    approve_p.add_argument("--generate-sequence", action="store_true", help="At the last gate, draft a sequence")
    approve_p.add_argument("--no-run", action="store_true", help="Record approval without starting the next stretch")

    revise = sub.add_parser("revise", help="Add notes and rebuild the plan")
    revise.add_argument("--run", required=True)
    revise.add_argument("--notes", required=True)
    revise.add_argument("--rerun", action="store_true", help="Re-run the stretch that just paused")

    show = sub.add_parser("status", help="Print the plan, phase, and assistant note")
    show.add_argument("--run", required=True)
    show.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    root = Path(args.data_root) if args.data_root else default_data_root()
    try:
        if args.command == "plan":
            state = start(
                args.url,
                data_root=root,
                client_id=args.client_id,
                notes=args.notes,
                target_leads=args.target_leads,
                max_usd=args.max_usd,
                seed_source=args.seed_source,
                contact_provider=args.contact_provider,
                csv_path=args.csv,
            )
        elif args.command == "approve":
            state = approve(
                args.run,
                data_root=root,
                gate=args.gate,
                notes=args.notes,
                and_run=not args.no_run,
                generate_sequence=bool(args.generate_sequence),
            )
        elif args.command == "revise":
            state = reject(args.run, args.notes, data_root=root, rerun=bool(args.rerun))
        else:
            state = status(args.run, data_root=root)
            if args.json:
                print(json.dumps({k: state.get(k) for k in ("run_id", "phase", "assistant", "halt_reason")}, default=str, indent=2))
                return 0
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    _print(state)
    return 0 if state.get("phase") != "halted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
