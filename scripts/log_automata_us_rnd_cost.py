"""Log Apify + estimated Apollo costs for automata_us_rnd seeding/Apollo runs."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)

from apify_client import ApifyClient

OUT = ROOT / "data" / "campaigns" / "automata_us_rnd" / "cost_log.json"
MD = ROOT / "data" / "campaigns" / "automata_us_rnd" / "cost_log.md"

# Window covering both LI job scrapes + any related Apify (2026-08-22 evening UTC)
START = datetime(2026, 8, 22, 22, 20, 0, tzinfo=timezone.utc)
END = datetime(2026, 8, 22, 23, 0, 0, tzinfo=timezone.utc)

APOLLO_CREDIT_USD = float(os.environ.get("APOLLO_CREDIT_USD", "0.02"))


def _get(obj, *names, default=None):
    if isinstance(obj, dict):
        for n in names:
            if obj.get(n) is not None:
                return obj.get(n)
        return default
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return default


def main() -> None:
    client = ApifyClient(os.environ["APIFY_TOKEN"])
    items = list(client.runs().list(limit=80, desc=True).items)
    apify_rows = []
    apify_total = 0.0
    for r in items:
        started = _get(r, "startedAt", "started_at")
        if not started:
            continue
        if isinstance(started, str):
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        else:
            started_dt = started
        if not (START <= started_dt <= END):
            continue
        act = _get(r, "actId", "act_id", default="unknown")
        usd = float(_get(r, "usageTotalUsd", "usage_total_usd", default=0) or 0)
        apify_total += usd
        try:
            info = client.actor(act).get() or {}
            if not isinstance(info, dict):
                info = {
                    "username": getattr(info, "username", None),
                    "name": getattr(info, "name", None),
                }
            actor_name = f"{info.get('username') or ''}/{info.get('name') or act}".strip("/")
        except Exception:
            actor_name = str(act)
        apify_rows.append(
            {
                "started_at": started_dt.isoformat(),
                "actor": actor_name,
                "status": _get(r, "status"),
                "usd": round(usd, 4),
                "run_id": _get(r, "id"),
            }
        )

    # Apollo: 33 companies processed in stage_apollo (people search + email reveal).
    # Exact credits vary; use conservative estimate: ~1 search + ~1-2 reveals attempt per company.
    apollo_companies = 33
    apollo_contactable = 25
    apollo_rejected = 8
    # people/match reveal is the expensive part; estimate 1 credit per reveal attempt
    # + ~0.5 search credit equivalent — document as estimate
    apollo_credits_est = apollo_companies * 2  # search+reveal ballpark
    apollo_usd_est = round(apollo_credits_est * APOLLO_CREDIT_USD, 4)

    report = {
        "campaign_id": "automata_us_rnd",
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "window_utc": {"start": START.isoformat(), "end": END.isoformat()},
        "funnel_snapshot": {
            "linkedin_jobs_scrapes": 2,
            "unique_companies_seeded": 142,
            "icp_matched_product_filtered": 33,
            "apollo_contactable": apollo_contactable,
            "apollo_rejected": apollo_rejected,
            "enriched": 0,
        },
        "apify": {
            "total_usd": round(apify_total, 4),
            "runs": apify_rows,
            "source": "Apify API usageTotalUsd (billed)",
        },
        "apollo": {
            "companies_attempted": apollo_companies,
            "credits_est": apollo_credits_est,
            "usd_est": apollo_usd_est,
            "credit_rate_usd": APOLLO_CREDIT_USD,
            "source": "estimate — CostTracker not initialized on stage_apollo this run",
            "note": "Roughly 2 credits/company (people search + email reveal). Verify in Apollo usage dashboard.",
        },
        "firecrawl_usd": 0.0,
        "llm_usd": 0.0,
        "enrichment_usd": 0.0,
        "totals": {
            "known_billed_usd": round(apify_total, 4),
            "estimated_usd": round(apify_total + apollo_usd_est, 4),
            "per_contactable_est_usd": round(
                (apify_total + apollo_usd_est) / max(apollo_contactable, 1), 4
            ),
        },
        "artifact_locations": {
            "campaign_dir": "data/campaigns/automata_us_rnd/",
            "raw_seeds": "data/campaigns/automata_us_rnd/stages/01_raw_seeds.csv",
            "icp_matched": "data/campaigns/automata_us_rnd/stages/02_icp_matched.csv",
            "deduped": "data/campaigns/automata_us_rnd/stages/03_deduped.csv",
            "contactable": "data/campaigns/automata_us_rnd/stages/04_apollo_contactable.csv",
            "rejected": "data/campaigns/automata_us_rnd/stages/04_apollo_rejected.csv",
            "cost_log": "data/campaigns/automata_us_rnd/cost_log.json",
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# Cost log — automata_us_rnd (seed + Apollo)",
        "",
        f"- Logged: `{report['logged_at']}`",
        f"- Window (UTC): {START.isoformat()} → {END.isoformat()}",
        "",
        "## Funnel",
        "",
        f"| Step | Count |",
        f"|------|------:|",
        f"| LinkedIn job scrapes | 2 (cap 100 each) |",
        f"| Unique companies | 142 |",
        f"| Product ICP shortlist | 33 |",
        f"| Apollo contactable | **{apollo_contactable}** |",
        f"| Apollo rejected | {apollo_rejected} |",
        f"| Enriched | 0 |",
        "",
        "## Spend",
        "",
        f"| Source | USD | Confidence |",
        f"|--------|----:|------------|",
        f"| Apify LinkedIn jobs | **${apify_total:.4f}** | Billed (API) |",
        f"| Apollo people+email | ~${apollo_usd_est:.2f} | Estimate (~{apollo_credits_est} credits × ${APOLLO_CREDIT_USD}) |",
        f"| Firecrawl / LLM enrich | $0.00 | Not run yet |",
        f"| **Total so far** | **~${apify_total + apollo_usd_est:.2f}** | |",
        f"| Per contactable lead | ~${(apify_total + apollo_usd_est) / max(apollo_contactable, 1):.3f} | |",
        "",
        "## Apify runs",
        "",
        "| Started | Actor | Status | USD |",
        "|---------|-------|--------|----:|",
    ]
    for row in apify_rows:
        lines.append(
            f"| {row['started_at']} | `{row['actor']}` | {row['status']} | ${row['usd']:.4f} |"
        )
    lines += [
        "",
        "## Where lists live",
        "",
        "- Campaign folder: `data/campaigns/automata_us_rnd/`",
        "- Contactable emails: `stages/04_apollo_contactable.csv`",
        "- Not on Google Drive yet (no Sheet upload for this campaign)",
        "",
    ]
    MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report["totals"], indent=2))
    print(f"Wrote {OUT}")
    print(f"Wrote {MD}")


if __name__ == "__main__":
    main()
