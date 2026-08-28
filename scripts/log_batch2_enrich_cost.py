"""Reconstruct Automata batch2 enrich cost log for run_20260719_150513_b2c108."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)

from apify_client import ApifyClient

RUN_ID = "run_20260719_150513_b2c108"
CLIENT_ID = "automata-batch2"
CAMPAIGN = "automata"
START = datetime(2026, 7, 19, 15, 5, 12, tzinfo=timezone.utc)
END = datetime(2026, 7, 19, 15, 24, 30, tzinfo=timezone.utc)

# Unit rates from cost_tracker defaults / .env.example
APOLLO_CREDIT_USD = float(os.environ.get("APOLLO_CREDIT_USD", "0.02"))
FIRECRAWL_PAGE_USD = float(os.environ.get("FIRECRAWL_PAGE_USD", "0.01"))
LLM_PER_1K_TOKENS_USD = float(os.environ.get("LLM_PER_1K_TOKENS_USD", "0.003"))

# Observed from enrich log (stage_enrich did not init CostTracker)
LEADS = 50
# Company LinkedIn path calls Apollo org enrich; log showed ~98 "Apollo enriched company"
# (includes retries / unique company lookups). Org enrich is typically 1 credit each.
APOLLO_ORG_ENRICH_CALLS = 98
# Firecrawl: 50 crawls, max 5 pages each; careers scrapes extra. Use conservative
# estimate of 5 pages/company average for crawl + ~0.4 careers page = ~5.4 → round 5.
FIRECRAWL_PAGES_EST = 50 * 5
# Website stage: 1 analyse LLM call per company (+ optional hiring LLM). ~2 calls × ~2k tokens.
LLM_TOKENS_EST = 50 * 2 * 2000


def main() -> None:
    client = ApifyClient(os.environ["APIFY_TOKEN"])
    items = list(client.runs().list(limit=250, desc=True).items)
    by_act: dict[str, dict] = defaultdict(lambda: {"count": 0, "usd": 0.0})
    act_names: dict[str, str] = {}
    apify_total = 0.0

    def _field(obj, *names, default=None):
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

    for r in items:
        started = _field(r, "startedAt", "started_at")
        if not started:
            continue
        if isinstance(started, str):
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        else:
            started_dt = started
        if not (START <= started_dt <= END):
            continue
        act = _field(r, "actId", "act_id", default="unknown")
        usd = float(_field(r, "usageTotalUsd", "usage_total_usd", default=0) or 0)
        apify_total += usd
        by_act[act]["count"] += 1
        by_act[act]["usd"] += usd
        if act not in act_names:
            try:
                info = client.actor(act).get() or {}
                if not isinstance(info, dict):
                    info = {
                        "username": getattr(info, "username", None),
                        "name": getattr(info, "name", None),
                    }
                uname = info.get("username") or ""
                name = info.get("name") or act
                act_names[act] = f"{uname}/{name}" if uname else name
            except Exception:
                act_names[act] = act

    apollo_usd = round(APOLLO_ORG_ENRICH_CALLS * APOLLO_CREDIT_USD, 4)
    firecrawl_usd = round(FIRECRAWL_PAGES_EST * FIRECRAWL_PAGE_USD, 4)
    llm_usd = round((LLM_TOKENS_EST / 1000) * LLM_PER_1K_TOKENS_USD, 4)
    apify_usd = round(apify_total, 4)
    total = round(apollo_usd + firecrawl_usd + apify_usd + llm_usd, 4)

    actors = [
        {
            "actor": act_names.get(act, act),
            "act_id": act,
            "runs": d["count"],
            "usd": round(d["usd"], 4),
        }
        for act, d in sorted(by_act.items(), key=lambda x: -x[1]["usd"])
    ]

    report = {
        "campaign_id": CAMPAIGN,
        "client_id": CLIENT_ID,
        "run_id": RUN_ID,
        "phase": "stage_enrich (batch2 re-enrich with LinkedIn profiles + posts)",
        "window_utc": {"start": START.isoformat(), "end": END.isoformat()},
        "leads_enriched": LEADS,
        "flags": {
            "APIFY_SKIP_LINKEDIN_POSTS": "0",
            "APIFY_SKIP_PERSON_PROFILE": "0",
            "APOLLO_SKIP_PHONE": "1",
        },
        "note": (
            "stage_enrich.py did not init CostTracker for this run; Apify USD is from "
            "Apify API usageTotalUsd in the enrich window. Apollo/Firecrawl/LLM are "
            "estimated from log counts and default unit rates."
        ),
        "unit_rates_usd": {
            "apollo_per_credit": APOLLO_CREDIT_USD,
            "firecrawl_per_page": FIRECRAWL_PAGE_USD,
            "llm_per_1k_tokens": LLM_PER_1K_TOKENS_USD,
        },
        "breakdown": {
            "apollo_org_enrich_calls": APOLLO_ORG_ENRICH_CALLS,
            "apollo_usd": apollo_usd,
            "firecrawl_pages_est": FIRECRAWL_PAGES_EST,
            "firecrawl_usd": firecrawl_usd,
            "apify_usd": apify_usd,
            "apify_runs": sum(a["runs"] for a in actors),
            "llm_tokens_est": LLM_TOKENS_EST,
            "llm_usd": llm_usd,
            "total_usd": total,
        },
        "apify_by_actor": actors,
        "unit_economics": {
            "usd_per_lead": round(total / LEADS, 4),
            "apify_usd_per_lead": round(apify_usd / LEADS, 4),
        },
        "deliverables": {
            "enriched_csv": "data/campaigns/automata/stages/05_enriched.csv",
            "scored_csv": "data/campaigns/automata/stages/06_scored.csv",
            "sheet_url": "https://docs.google.com/spreadsheets/d/1TEXV7JKZ1he4ROaXqt0pCyFciCeQANCPW1bSoQyvm_A",
        },
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }

    out_dir = ROOT / "data" / "campaigns" / CAMPAIGN
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"cost_log_{RUN_ID}.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    md_path = out_dir / "cost_log_batch2_enrich.md"
    lines = [
        "# Cost log — Automata batch2 re-enrich",
        "",
        f"- **Run ID:** `{RUN_ID}`",
        f"- **Client ID:** `{CLIENT_ID}`",
        f"- **When (UTC):** {START.strftime('%Y-%m-%d %H:%M')} → {END.strftime('%H:%M')}",
        f"- **Leads enriched:** {LEADS}",
        "- **Flags:** profiles + posts on (`APIFY_SKIP_*=0`), phones skipped",
        "",
        "## Totals",
        "",
        f"| Category | Units | USD |",
        f"|----------|------:|----:|",
        f"| Apollo org enrich (LinkedIn URL resolve) | {APOLLO_ORG_ENRICH_CALLS} calls | ${apollo_usd:.2f} |",
        f"| Firecrawl (est. {FIRECRAWL_PAGES_EST} pages) | {FIRECRAWL_PAGES_EST} | ${firecrawl_usd:.2f} |",
        f"| Apify LinkedIn (API billed) | {sum(a['runs'] for a in actors)} runs | **${apify_usd:.2f}** |",
        f"| LLM website analysis (est.) | ~{LLM_TOKENS_EST:,} tokens | ${llm_usd:.2f} |",
        f"| **Total (est.)** | | **${total:.2f}** |",
        "",
        f"**Cost per lead:** ~${total / LEADS:.2f}  ·  **Apify per lead:** ~${apify_usd / LEADS:.2f}",
        "",
        "## Apify by actor",
        "",
        "| Actor | Runs | USD |",
        "|-------|-----:|----:|",
    ]
    for a in actors:
        lines.append(f"| `{a['actor']}` | {a['runs']} | ${a['usd']:.4f} |")
    lines += [
        "",
        "## Notes",
        "",
        "- Live CostTracker was **not** initialized by `stage_enrich.py` for this run; "
        "Apify dollars are from Apify `usageTotalUsd` in the enrich window.",
        "- Apollo/Firecrawl/LLM lines are estimates from log counts + default unit rates "
        f"(`APOLLO_CREDIT_USD={APOLLO_CREDIT_USD}`, `FIRECRAWL_PAGE_USD={FIRECRAWL_PAGE_USD}`, "
        f"`LLM_PER_1K_TOKENS_USD={LLM_PER_1K_TOKENS_USD}`).",
        "- Machine-readable copy: "
        f"`data/campaigns/automata/cost_log_{RUN_ID}.json`",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")

    # Also mirror under client runs folder
    client_dir = ROOT / "data" / "clients" / CLIENT_ID / "runs"
    client_dir.mkdir(parents=True, exist_ok=True)
    (client_dir / f"{RUN_ID}_cost_log.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    print(json.dumps(report["breakdown"], indent=2))
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
