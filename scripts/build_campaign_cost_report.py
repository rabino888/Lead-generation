"""
Build campaign cost report split by workflow step and source.

Reads data/campaigns/{campaign_id}/cost_runs.json (operator-maintained run manifest),
queries Apify for billed USD in each seed window, estimates Apollo from manifest counts,
writes:
  - cost_log.json          (machine-readable, v3 schema)
  - cost_log.md            (operator summary)
  - cost_dashboard.html    (browser view)

Usage:
  python scripts/build_campaign_cost_report.py --campaign automata_us_rnd
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

# Workflow steps (funnel stages)
WORKFLOW_STEPS = ("seeding", "apollo_contact", "enrichment")

# Billing sources (vendors / APIs)
SOURCES = ("apify", "apollo", "firecrawl", "llm")


def _get(obj, *names, default=None):
    if isinstance(obj, dict):
        for n in names:
            if obj.get(n) is not None:
                return obj[n]
        return default
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return default


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _norm_stage(stage: str) -> str:
    stage = (stage or "").strip().lower()
    if stage in ("enrich", "enrichment", "linkedin_enrich", "stage_enrich"):
        return "enrichment"
    if stage in ("apollo", "apollo_contact", "contact"):
        return "apollo_contact"
    if stage in ("seed", "seeding", "linkedin_jobs"):
        return "seeding"
    return stage or "unknown"


def _empty_center() -> dict:
    return {"usd_billed": 0.0, "usd_estimated": 0.0, "runs": 0}


def _empty_source() -> dict:
    return {"usd_billed": 0.0, "usd_estimated": 0.0}


def _fetch_apify_runs(client, start: datetime, end: datetime) -> list[dict]:
    rows = []
    for r in client.runs().list(limit=200, desc=True).items:
        started = _get(r, "startedAt", "started_at")
        if not started:
            continue
        started_dt = _parse_dt(started) if isinstance(started, str) else started
        if not (start <= started_dt <= end):
            continue
        act = _get(r, "actId", "act_id", default="unknown")
        usd = float(_get(r, "usageTotalUsd", "usage_total_usd", default=0) or 0)
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
        rows.append(
            {
                "started_at": started_dt.isoformat(),
                "actor": actor_name,
                "status": _get(r, "status"),
                "usd": round(usd, 4),
                "run_id": _get(r, "id"),
            }
        )
    return rows


def _apollo_usd(org_calls: int, reveals: int, rate: float) -> dict:
    org_credits = org_calls
    reveal_credits = reveals
    total_credits = org_credits + reveal_credits
    return {
        "org_enrich_credits": org_credits,
        "email_reveal_credits": reveal_credits,
        "total_credits_est": total_credits,
        "usd_est": round(total_credits * rate, 4),
        "confidence": "estimate",
    }


def _add_source(by_source: dict, name: str, *, billed: float = 0.0, estimated: float = 0.0) -> None:
    if name not in by_source:
        by_source[name] = _empty_source()
    by_source[name]["usd_billed"] += billed
    by_source[name]["usd_estimated"] += estimated


def build_report(campaign_id: str) -> dict:
    from apify_client import ApifyClient

    campaign_dir = ROOT / "data" / "campaigns" / campaign_id
    manifest_path = campaign_dir / "cost_runs.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rate = float(
        manifest.get("unit_rates_usd", {}).get("apollo_per_credit")
        or os.environ.get("APOLLO_CREDIT_USD", "0.02")
    )
    client = ApifyClient(os.environ["APIFY_TOKEN"])

    run_rows: list[dict] = []
    by_step: dict[str, dict] = {k: _empty_center() for k in WORKFLOW_STEPS}
    by_source: dict[str, dict] = {k: _empty_source() for k in SOURCES}

    for entry in manifest.get("runs", []):
        stage = _norm_stage(entry.get("stage", ""))
        row: dict = {
            "run_key": entry.get("run_key"),
            "label": entry.get("label"),
            "workflow_step": stage,
            "stage": stage,
            "run_id": entry.get("run_id"),
            "outcome": entry.get("outcome", {}),
            "sources": {},
            "cost_centers": {},
            "total_usd": 0.0,
            "total_billed_usd": 0.0,
            "total_estimated_usd": 0.0,
        }

        if stage == "seeding":
            start = _parse_dt(entry["started_at_utc"])
            end = _parse_dt(entry["ended_at_utc"])
            apify_runs = _fetch_apify_runs(client, start, end)
            pinned = list(entry.get("apify_run_ids") or [])
            if pinned:
                by_id = {r["run_id"]: r for r in apify_runs}
                resolved = []
                for rid in pinned:
                    if rid in by_id:
                        resolved.append(by_id[rid])
                        continue
                    try:
                        fresh = client.run(rid).get() or {}
                        if not isinstance(fresh, dict):
                            fresh = {
                                "id": getattr(fresh, "id", rid),
                                "usageTotalUsd": getattr(fresh, "usage_total_usd", None)
                                or getattr(fresh, "usageTotalUsd", 0),
                                "status": getattr(fresh, "status", None),
                                "actId": getattr(fresh, "act_id", None),
                            }
                        act = fresh.get("actId") or fresh.get("act_id") or "unknown"
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
                        resolved.append(
                            {
                                "started_at": entry.get("started_at_utc"),
                                "actor": actor_name,
                                "status": fresh.get("status"),
                                "usd": round(float(fresh.get("usageTotalUsd") or fresh.get("usage_total_usd") or 0), 4),
                                "run_id": rid,
                            }
                        )
                    except Exception:
                        continue
                if resolved:
                    apify_runs = resolved
            apify_usd = round(sum(r["usd"] for r in apify_runs), 4)
            detail = {
                "usd": apify_usd,
                "confidence": "billed",
                "runs": apify_runs,
                "source": "apify",
                "workflow_step": "seeding",
            }
            row["sources"]["apify_linkedin_jobs_seed"] = detail
            row["cost_centers"]["apify_linkedin_jobs"] = detail
            row["total_usd"] = apify_usd
            row["total_billed_usd"] = apify_usd
            by_step["seeding"]["usd_billed"] += apify_usd
            by_step["seeding"]["runs"] += 1
            _add_source(by_source, "apify", billed=apify_usd)

        elif stage == "apollo_contact":
            ap = entry.get("apollo") or {}
            credits_direct = ap.get("credits_consumed")
            if credits_direct is not None:
                apollo_usd = round(float(credits_direct) * rate, 4)
                confidence = "billed" if float(credits_direct) == 0 else "session_counter"
                row["sources"]["apollo"] = {
                    "credits": float(credits_direct),
                    "usd": apollo_usd,
                    "confidence": confidence,
                    "source": "apollo",
                    "workflow_step": "apollo_contact",
                }
                row["cost_centers"]["apollo"] = row["sources"]["apollo"]
                row["total_usd"] = apollo_usd
                row["total_billed_usd"] = apollo_usd
                by_step["apollo_contact"]["usd_billed"] += apollo_usd
                _add_source(by_source, "apollo", billed=apollo_usd)
            else:
                ap_cost = _apollo_usd(
                    int(ap.get("org_enrich_calls") or 0),
                    int(ap.get("email_reveals") or 0),
                    rate,
                )
                org_usd = round(ap_cost["org_enrich_credits"] * rate, 4)
                reveal_usd = round(ap_cost["email_reveal_credits"] * rate, 4)
                row["sources"]["apollo_org_enrich"] = {
                    "credits_est": ap_cost["org_enrich_credits"],
                    "usd_est": org_usd,
                    "confidence": "estimate",
                    "source": "apollo",
                    "workflow_step": "apollo_contact",
                }
                row["sources"]["apollo_email_reveal"] = {
                    "credits_est": ap_cost["email_reveal_credits"],
                    "usd_est": reveal_usd,
                    "confidence": "estimate",
                    "source": "apollo",
                    "workflow_step": "apollo_contact",
                }
                row["cost_centers"] = dict(row["sources"])
                row["apollo_detail"] = ap
                row["total_usd"] = ap_cost["usd_est"]
                row["total_estimated_usd"] = ap_cost["usd_est"]
                by_step["apollo_contact"]["usd_estimated"] += ap_cost["usd_est"]
                _add_source(by_source, "apollo", estimated=ap_cost["usd_est"])
            by_step["apollo_contact"]["runs"] += 1

        elif stage == "enrichment":
            apify_block = entry.get("apify") or {}
            tracker = entry.get("cost_tracker") or {}
            apify_usd = float(
                apify_block.get("api_truth_usd")
                if apify_block.get("api_truth_usd") is not None
                else apify_block.get("cost_tracker_usd")
                or tracker.get("apify_usd")
                or 0
            )
            jobs_usd = float(apify_block.get("afanasenko_jobs_usd") or 0)
            other_apify = float(
                apify_block.get("other_apify_usd")
                if apify_block.get("other_apify_usd") is not None
                else max(apify_usd - jobs_usd, 0)
            )
            if jobs_usd:
                row["sources"]["apify_linkedin_jobs_company"] = {
                    "usd": round(jobs_usd, 4),
                    "confidence": "billed",
                    "source": "apify",
                    "workflow_step": "enrichment",
                    "optional": True,
                    "note": apify_block.get("note"),
                }
            if other_apify or (apify_usd and not jobs_usd):
                li_usd = round(other_apify if jobs_usd else apify_usd, 4)
                row["sources"]["apify_linkedin_company_person"] = {
                    "usd": li_usd,
                    "confidence": "billed",
                    "source": "apify",
                    "workflow_step": "enrichment",
                    "cost_tracker_usd": apify_block.get("cost_tracker_usd"),
                    "api_truth_usd": apify_block.get("api_truth_usd"),
                }

            firecrawl_usd = float(
                (entry.get("firecrawl") or {}).get("usd")
                or tracker.get("firecrawl_usd")
                or 0
            )
            llm_usd = float(
                (entry.get("llm") or {}).get("usd")
                or tracker.get("llm_usd")
                or 0
            )
            if firecrawl_usd:
                row["sources"]["firecrawl"] = {
                    "usd": round(firecrawl_usd, 4),
                    "pages": (entry.get("firecrawl") or {}).get("pages")
                    or tracker.get("firecrawl_pages"),
                    "confidence": (entry.get("firecrawl") or {}).get("confidence", "estimate"),
                    "source": "firecrawl",
                    "workflow_step": "enrichment",
                }
            if llm_usd:
                row["sources"]["llm"] = {
                    "usd": round(llm_usd, 4),
                    "tokens": (entry.get("llm") or {}).get("tokens") or tracker.get("llm_tokens"),
                    "confidence": (entry.get("llm") or {}).get("confidence", "estimate"),
                    "source": "llm",
                    "workflow_step": "enrichment",
                }

            row["cost_centers"] = dict(row["sources"])
            billed = round(apify_usd, 4)
            estimated = round(firecrawl_usd + llm_usd, 4)
            for key in ("firecrawl", "llm"):
                block = entry.get(key) or {}
                if block.get("confidence") == "billed":
                    usd = float(block.get("usd") or 0)
                    billed += usd
                    estimated -= usd
            estimated = max(estimated, 0)
            row["total_billed_usd"] = round(billed, 4)
            row["total_estimated_usd"] = round(estimated, 4)
            row["total_usd"] = round(billed + estimated, 4)
            by_step["enrichment"]["usd_billed"] += billed
            by_step["enrichment"]["usd_estimated"] += estimated
            by_step["enrichment"]["runs"] += 1
            _add_source(by_source, "apify", billed=apify_usd)
            if firecrawl_usd:
                conf = (entry.get("firecrawl") or {}).get("confidence", "estimate")
                if conf == "billed":
                    _add_source(by_source, "firecrawl", billed=firecrawl_usd)
                else:
                    _add_source(by_source, "firecrawl", estimated=firecrawl_usd)
            if llm_usd:
                conf = (entry.get("llm") or {}).get("confidence", "estimate")
                if conf == "billed":
                    _add_source(by_source, "llm", billed=llm_usd)
                else:
                    _add_source(by_source, "llm", estimated=llm_usd)

        else:
            row["note"] = f"Unhandled workflow_step={stage}"

        run_rows.append(row)

    for k, v in by_step.items():
        v["usd_billed"] = round(v["usd_billed"], 4)
        v["usd_estimated"] = round(v["usd_estimated"], 4)
        v["usd_total"] = round(v["usd_billed"] + v["usd_estimated"], 4)

    for k, v in by_source.items():
        v["usd_billed"] = round(v["usd_billed"], 4)
        v["usd_estimated"] = round(v["usd_estimated"], 4)
        v["usd_total"] = round(v["usd_billed"] + v["usd_estimated"], 4)

    billed = round(sum(v["usd_billed"] for v in by_step.values()), 4)
    estimated = round(sum(v["usd_estimated"] for v in by_step.values()), 4)
    total_est = round(billed + estimated, 4)

    stages_dir = campaign_dir / "stages"
    contactable = 0
    contactable_path = stages_dir / "04_apollo_contactable.csv"
    if contactable_path.exists():
        import csv

        contactable = sum(1 for _ in csv.DictReader(contactable_path.open(encoding="utf-8")))

    return {
        "schema_version": "3",
        "campaign_id": campaign_id,
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "unit_rates_usd": manifest.get("unit_rates_usd", {}),
        "by_workflow_step": by_step,
        "by_cost_center": by_step,
        "by_source": by_source,
        "by_run": run_rows,
        "totals": {
            "usd_billed": billed,
            "usd_estimated": estimated,
            "usd_total_est": total_est,
            "per_contactable_est_usd": round(total_est / max(contactable, 1), 4),
            "contactable_leads": contactable,
        },
        "note": (
            "Segmented by workflow_step (seeding / apollo_contact / enrichment) and "
            "source (apify / apollo / firecrawl / llm). Apify uses API usageTotalUsd "
            "when available; Apollo org+reveal without session counters are estimates. "
            "Company LinkedIn open-jobs are optional (not default)."
        ),
    }


def write_markdown(report: dict, path: Path) -> None:
    steps = report["by_workflow_step"]
    sources = report["by_source"]
    totals = report["totals"]
    lines = [
        f"# Cost log — {report['campaign_id']}",
        "",
        f"- Logged: `{report['logged_at']}`",
        f"- Contactable leads (current): **{totals['contactable_leads']}**",
        "",
        "## By workflow step",
        "",
        "| Workflow step | Billed USD | Estimated USD | Total |",
        "|---------------|----------:|-------------:|------:|",
    ]
    labels = {
        "seeding": "Seeding (Apify LinkedIn jobs search)",
        "apollo_contact": "Apollo contact (org enrich + email reveal)",
        "enrichment": "Enrichment (Firecrawl + Apify LinkedIn + LLM)",
    }
    for key in WORKFLOW_STEPS:
        c = steps[key]
        lines.append(
            f"| {labels[key]} | ${c['usd_billed']:.4f} | ${c['usd_estimated']:.4f} | "
            f"**${c['usd_total']:.4f}** |"
        )
    lines += [
        f"| **Campaign total** | **${totals['usd_billed']:.4f}** | "
        f"**${totals['usd_estimated']:.4f}** | **~${totals['usd_total_est']:.2f}** |",
        "",
        "## By source",
        "",
        "| Source | Billed USD | Estimated USD | Total |",
        "|--------|----------:|-------------:|------:|",
    ]
    for key in SOURCES:
        s = sources.get(key) or _empty_source()
        total = s.get("usd_total", s["usd_billed"] + s["usd_estimated"])
        lines.append(
            f"| {key} | ${s['usd_billed']:.4f} | ${s['usd_estimated']:.4f} | "
            f"**${total:.4f}** |"
        )
    lines += [
        "",
        f"**Per contactable lead (est.):** ~${totals['per_contactable_est_usd']:.3f}",
        "",
        "## By run",
        "",
        "| Run | Step | USD | Outcome |",
        "|-----|------|----:|---------|",
    ]
    for r in report["by_run"]:
        outcome = r.get("outcome") or {}
        outcome_s = ", ".join(f"{k}={v}" for k, v in outcome.items() if k != "note")
        if outcome.get("note"):
            outcome_s += f" ({outcome['note']})"
        rid = r.get("run_id") or "—"
        lines.append(
            f"| {r['label']} `{r['run_key']}` | {r['workflow_step']} | ${r['total_usd']:.4f} | "
            f"{outcome_s} · run_id={rid} |"
        )

    lines += ["", "## Run detail (sources)", ""]
    for r in report["by_run"]:
        lines.append(f"### {r['label']} (`{r['run_key']}`)")
        if r.get("run_id"):
            lines.append(f"- Pipeline run_id: `{r['run_id']}`")
        lines.append(f"- Workflow step: `{r.get('workflow_step')}`")
        for src_key, detail in (r.get("sources") or r.get("cost_centers") or {}).items():
            src = detail.get("source", src_key)
            if "usd" in detail and detail.get("confidence") == "billed":
                lines.append(f"- **{src_key}** ({src}): ${detail['usd']:.4f} (billed)")
                for ar in detail.get("runs") or []:
                    lines.append(
                        f"  - `{ar['run_id']}` {ar['actor']} ${ar['usd']:.4f}"
                    )
            elif "usd_est" in detail:
                lines.append(
                    f"- **{src_key}** ({src}): ~${detail['usd_est']:.4f} "
                    f"(~{detail.get('credits_est', '?')} credits, estimate)"
                )
            elif "usd" in detail:
                lines.append(
                    f"- **{src_key}** ({src}): ${detail['usd']:.4f} "
                    f"({detail.get('confidence', '?')})"
                )
        if r.get("apollo_detail"):
            ap = r["apollo_detail"]
            lines.append(
                f"- Apollo: {ap.get('companies_attempted')} companies → "
                f"{ap.get('contactable')} contactable, {ap.get('rejected')} rejected"
            )
        lines.append("")

    lines.append(f"_Note: {report['note']}_")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_html(report: dict, path: Path) -> None:
    steps = report["by_workflow_step"]
    sources = report["by_source"]
    totals = report["totals"]
    step_labels = {
        "seeding": "Seeding",
        "apollo_contact": "Apollo contact",
        "enrichment": "Enrichment",
    }
    center_rows = ""
    for key in WORKFLOW_STEPS:
        c = steps[key]
        center_rows += f"""
        <tr>
          <td>{escape(step_labels[key])}</td>
          <td class="num">${c['usd_billed']:.4f}</td>
          <td class="num">${c['usd_estimated']:.4f}</td>
          <td class="num"><strong>${c['usd_total']:.4f}</strong></td>
        </tr>"""

    source_rows = ""
    for key in SOURCES:
        s = sources.get(key) or {"usd_billed": 0, "usd_estimated": 0, "usd_total": 0}
        total = s.get("usd_total", s["usd_billed"] + s["usd_estimated"])
        source_rows += f"""
        <tr>
          <td>{escape(key)}</td>
          <td class="num">${s['usd_billed']:.4f}</td>
          <td class="num">${s['usd_estimated']:.4f}</td>
          <td class="num"><strong>${total:.4f}</strong></td>
        </tr>"""

    run_rows_html = ""
    for r in report["by_run"]:
        outcome = r.get("outcome") or {}
        outcome_s = ", ".join(f"{k}={v}" for k, v in outcome.items() if k != "note")
        srcs = ", ".join(
            f"{k}=${(d.get('usd') or d.get('usd_est') or 0):.3f}"
            for k, d in (r.get("sources") or {}).items()
        )
        run_rows_html += f"""
        <tr>
          <td>{escape(r.get('label') or '')}<br><code>{escape(r.get('run_key') or '')}</code></td>
          <td>{escape(r.get('workflow_step') or '')}</td>
          <td class="num">${r.get('total_usd', 0):.4f}</td>
          <td>{escape(srcs)}</td>
          <td>{escape(outcome_s)}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Cost — {escape(report['campaign_id'])}</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem; color: #111; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0 2rem; }}
    th, td {{ border: 1px solid #ddd; padding: 0.5rem 0.75rem; text-align: left; }}
    th {{ background: #f4f4f5; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    h1 {{ margin-bottom: 0.25rem; }}
    .meta {{ color: #555; margin-bottom: 1.5rem; }}
    code {{ font-size: 0.85em; }}
  </style>
</head>
<body>
  <h1>Cost log — {escape(report['campaign_id'])}</h1>
  <p class="meta">Logged {escape(report['logged_at'])} ·
    {totals['contactable_leads']} contactable ·
    total ~${totals['usd_total_est']:.2f}
    (${totals['usd_billed']:.2f} billed + ${totals['usd_estimated']:.2f} est)</p>

  <h2>By workflow step</h2>
  <table>
    <thead><tr><th>Step</th><th>Billed</th><th>Estimated</th><th>Total</th></tr></thead>
    <tbody>{center_rows}</tbody>
  </table>

  <h2>By source</h2>
  <table>
    <thead><tr><th>Source</th><th>Billed</th><th>Estimated</th><th>Total</th></tr></thead>
    <tbody>{source_rows}</tbody>
  </table>

  <h2>By run</h2>
  <table>
    <thead><tr><th>Run</th><th>Step</th><th>USD</th><th>Sources</th><th>Outcome</th></tr></thead>
    <tbody>{run_rows_html}</tbody>
  </table>
  <p class="meta">{escape(report.get('note') or '')}</p>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build campaign cost report")
    parser.add_argument("--campaign", required=True)
    args = parser.parse_args()

    report = build_report(args.campaign)
    campaign_dir = ROOT / "data" / "campaigns" / args.campaign
    json_path = campaign_dir / "cost_log.json"
    md_path = campaign_dir / "cost_log.md"
    html_path = campaign_dir / "cost_dashboard.html"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_markdown(report, md_path)
    write_html(report, html_path)
    totals = report["totals"]
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"Wrote {html_path}")
    print(
        f"Total ~${totals['usd_total_est']:.2f} "
        f"(billed ${totals['usd_billed']:.2f} + est ${totals['usd_estimated']:.2f})"
    )
    print("By workflow step:")
    for k, v in report["by_workflow_step"].items():
        print(f"  {k}: ${v['usd_total']:.4f}")
    print("By source:")
    for k, v in report["by_source"].items():
        print(f"  {k}: ${v['usd_total']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
