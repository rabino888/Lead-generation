"""
Suggest collectors for a campaign from its offer, lead magnet, and ICP.

No Apollo or Apify call. The model runs only with --generate.

  python scripts/suggest_collectors.py --campaign golemui_insurance_uk
  python scripts/suggest_collectors.py --campaign golemui_insurance_uk --print-prompt
  python scripts/suggest_collectors.py --campaign golemui_insurance_uk --generate
  python scripts/suggest_collectors.py --campaign golemui_insurance_uk --accept public_quote_form
  python scripts/suggest_collectors.py --campaign golemui_insurance_uk --reject hiring_function

Without --generate, a missing collector_suggestions.json exits and tells the
operator to pass --generate or write the file. Accepted and rejected rows are
kept when --generate runs again.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.utils.collector_suggestions import (
    CollectorInputError,
    build_collector_prompt,
    load_collector_context,
    load_suggestions,
    merge_generated_suggestions,
    stamp_suggestion_document,
    suggestions_path,
    validate_suggestion_document,
    write_suggestions,
    accept_suggestions,
    reject_suggestions,
)
from agent.utils.client_store import data_root, resolve_campaign_dir


def _root() -> Path:
    root = data_root()
    if not root.is_absolute():
        root = ROOT / root
    return root


def _resolve(campaign_id: str) -> Path:
    found = resolve_campaign_dir(_root(), campaign_id)
    if found is None:
        raise CollectorInputError(f"Campaign not found: {campaign_id}")
    return found


def _generate_from_model(prompt: str) -> dict:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    from agent.integrations.llm import call_llm, website_providers

    data = call_llm(
        prompt,
        system=(
            "You choose data collectors for a B2B list. "
            "Return one JSON object only. No outreach copy and no email subjects."
        ),
        max_tokens=2400,
        expect_json=True,
        providers=website_providers(),
    )
    if not isinstance(data, dict):
        raise CollectorInputError("model did not return a JSON object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Suggest collectors from the offer, lead magnet, and ICP."
    )
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--print-prompt",
        action="store_true",
        help="Print the model prompt and write nothing.",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Call Gemini Flash (website provider chain) and write suggestions.",
    )
    parser.add_argument(
        "--accept",
        default="",
        help="Comma-separated suggestion ids to merge into the enrichment plan.",
    )
    parser.add_argument(
        "--reject",
        default="",
        help="Comma-separated suggestion ids to mark rejected. Does not change the plan.",
    )
    args = parser.parse_args()

    try:
        campaign_dir = _resolve(args.campaign)
        context = load_collector_context(campaign_dir, data_root=_root())
    except CollectorInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for warning in context.get("warnings") or []:
        print(f"WARNING: {warning}", file=sys.stderr)

    if args.print_prompt:
        print(build_collector_prompt(context))
        return 0

    if args.accept or args.reject:
        accept_ids = {part.strip() for part in args.accept.split(",") if part.strip()}
        reject_ids = {part.strip() for part in args.reject.split(",") if part.strip()}
        overlap = sorted(accept_ids & reject_ids)
        if overlap:
            print(
                "ERROR: id cannot be both accepted and rejected: " + ", ".join(overlap),
                file=sys.stderr,
            )
            return 2
        try:
            if args.reject:
                reject_suggestions(campaign_dir, args.reject)
            if args.accept:
                accept_suggestions(campaign_dir, args.accept)
        except CollectorInputError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        doc = load_suggestions(campaign_dir)
        print(f"Updated {suggestions_path(campaign_dir)}")
        for row in doc.get("suggestions") or []:
            print(f"  {row.get('id')}: {row.get('status')}")
        return 0

    if args.generate:
        prompt = build_collector_prompt(context)
        try:
            raw = _generate_from_model(prompt)
        except Exception as exc:
            print(f"ERROR: model call failed: {exc}", file=sys.stderr)
            return 2
        fresh = validate_suggestion_document(
            raw, pitch=context.get("pitch") or "", reset_status=True
        )
        existing = None
        path = suggestions_path(campaign_dir)
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = None
        merged = merge_generated_suggestions(existing, fresh)
        doc = stamp_suggestion_document(
            merged,
            campaign_id=context["campaign_id"],
            client_id=context["client_id"],
            icp_id=context["icp_id"],
            offer_id=context["offer_id"],
            lead_magnet_id=context["lead_magnet_id"],
            magnet_status=context["magnet_status"],
            generator="model",
        )
        write_suggestions(campaign_dir, doc)
        print(f"Wrote {suggestions_path(campaign_dir)} ({len(doc['suggestions'])} suggestions)")
        return 0

    path = suggestions_path(campaign_dir)
    if not path.is_file():
        print(
            "No collector_suggestions.json. Pass --generate to call the model, or write the file.",
            file=sys.stderr,
        )
        return 2
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("suggestions") or []
    print(
        f"{path.name}: {len(rows)} suggestions, generator={doc.get('generator') or 'unknown'}"
    )
    for row in rows:
        if isinstance(row, dict):
            print(f"  {row.get('id')}: {row.get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
