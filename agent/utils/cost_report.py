"""
Campaign cost report builder (schema v4) + multi-campaign portal index.

Reports use a single USD total per row; uncertain lines are tagged via basis / display_label
(e.g. "LLM (est)") — no separate "estimated" column.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = "4"

WORKFLOW_STEPS = ("seeding", "apollo_contact", "enrichment")
SOURCES = ("apify", "apollo", "firecrawl", "llm")

FUNNEL_STEPS = [
    ("intake", "ICP + seed plan"),
    ("seeding", "Initial seed scrape (Apify LinkedIn jobs)"),
    ("icp_match", "ICP match (rules)"),
    ("dedupe", "De-duplication"),
    ("pre_apollo", "Pre-Apollo gate"),
    ("apollo_contact", "Apollo email + DM LinkedIn"),
    ("enrichment", "Firecrawl + Apify LinkedIn + LLM"),
    ("keyword_score", "Keyword score"),
    ("qa", "Human QA gate"),
    ("phone_reveal", "Apollo phone reveal (optional)"),
]

SOURCE_DISPLAY = {
    "apify": "Apify",
    "apollo": "Apollo",
    "firecrawl": "Firecrawl",
    "llm": "LLM (est)",
}


def _get(obj: Any, *names: str, default: Any = None) -> Any:
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


def _empty_step() -> dict[str, Any]:
    return {"usd": 0.0, "runs": 0}


def _empty_source() -> dict[str, Any]:
    return {"usd": 0.0, "basis": None, "is_est": False, "display_label": ""}


def _round_usd(v: float) -> float:
    return round(float(v), 4)


def _load_campaign_context(campaign_id: str, root: Path) -> Optional[dict[str, Any]]:
    try:
        from agent.utils.campaign_handoff import load_campaign_output_context

        return load_campaign_output_context(campaign_id, root=root / "campaigns")
    except Exception:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve_run_cost_log(campaign_dir: Path, run_id: str, client_id: Optional[str]) -> dict[str, Any]:
    """Load per-run cost log from campaign dir or client runs folder."""
    if not run_id:
        return {}
    paths = [campaign_dir / f"cost_log_{run_id}.json"]
    if client_id:
        paths.append(Path("data") / "clients" / client_id / "runs" / f"{run_id}_cost_log.json")
    for p in paths:
        data = _read_json(p)
        if data:
            return data
    return {}


def _fetch_apify_runs(client, start: datetime, end: datetime) -> list[dict[str, Any]]:
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
                "usd": _round_usd(usd),
                "run_id": _get(r, "id"),
            }
        )
    return rows


def _resolve_pinned_apify_runs(client, entry: dict[str, Any], apify_runs: list[dict]) -> list[dict]:
    pinned = list(entry.get("apify_run_ids") or [])
    if not pinned:
        return apify_runs
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
                    "usd": _round_usd(
                        float(fresh.get("usageTotalUsd") or fresh.get("usage_total_usd") or 0)
                    ),
                    "run_id": rid,
                }
            )
        except Exception:
            continue
    return resolved if resolved else apify_runs


def _group_apify_by_actor(runs: list[dict]) -> list[dict[str, Any]]:
    by_actor: dict[str, dict[str, Any]] = {}
    for r in runs:
        actor = r.get("actor") or "unknown"
        bucket = by_actor.setdefault(
            actor,
            {"actor": actor, "runs": 0, "usd": 0.0, "run_ids": [], "statuses": []},
        )
        bucket["runs"] += 1
        bucket["usd"] = _round_usd(bucket["usd"] + float(r.get("usd") or 0))
        rid = r.get("run_id")
        if rid and len(bucket["run_ids"]) < 10:
            bucket["run_ids"].append(rid)
        st = r.get("status")
        if st and st not in bucket["statuses"]:
            bucket["statuses"].append(st)
    return sorted(by_actor.values(), key=lambda x: -x["usd"])


def _apollo_from_entry(
    entry: dict[str, Any],
    ap: dict[str, Any],
    rate: float,
    campaign_dir: Path,
    client_id: Optional[str],
) -> tuple[float, dict[str, Any]]:
    """Resolve Apollo USD from credits_consumed (preferred) or legacy estimate."""
    run_id = entry.get("run_id")
    credits = ap.get("credits_consumed")
    if credits is None:
        cost_log = _resolve_run_cost_log(campaign_dir, run_id or "", client_id)
        ct = (cost_log.get("cost_tracker") or {}).get("run_totals") or {}
        if ct.get("apollo_credits"):
            credits = int(ct["apollo_credits"])

    if credits is not None:
        credits_int = int(credits)
        usd = _round_usd(credits_int * rate)
        detail = {
            "usd": usd,
            "credits": credits_int,
            "basis": "apollo_credits",
            "is_est": False,
            "display_label": SOURCE_DISPLAY["apollo"],
            "source": "apollo",
            "workflow_step": "apollo_contact",
        }
        if ap.get("note"):
            detail["note"] = ap["note"]
        return usd, {"apollo": detail}

    org_calls = int(ap.get("org_enrich_calls") or 0)
    reveals = int(ap.get("email_reveals") or 0)
    credits_est = org_calls + reveals
    org_usd = _round_usd(org_calls * rate)
    reveal_usd = _round_usd(reveals * rate)
    total_usd = _round_usd(org_usd + reveal_usd)
    sources = {
        "apollo_org_enrich": {
            "usd": org_usd,
            "credits": org_calls,
            "basis": "apollo_credit_estimate",
            "is_est": True,
            "display_label": "Apollo org enrich (est)",
            "source": "apollo",
            "workflow_step": "apollo_contact",
        },
        "apollo_email_reveal": {
            "usd": reveal_usd,
            "credits": reveals,
            "basis": "apollo_credit_estimate",
            "is_est": True,
            "display_label": "Apollo email reveal (est)",
            "source": "apollo",
            "workflow_step": "apollo_contact",
        },
    }
    return total_usd, sources


def _firecrawl_from_entry(
    fc: dict[str, Any],
    tracker: dict[str, Any],
    manifest_fc_plan: dict[str, Any],
    per_page_rate: float,
) -> tuple[float, dict[str, Any]]:
    credits = fc.get("credits")
    if credits is None:
        credits = fc.get("pages") or tracker.get("firecrawl_pages") or 0
    credits = int(credits or 0)

    plan_credits = fc.get("plan_credits") or manifest_fc_plan.get("plan_credits")
    plan_name = fc.get("plan_name") or manifest_fc_plan.get("plan_name")
    plan_usd_per_credit = manifest_fc_plan.get("usd_per_credit")

    usd = fc.get("usd")
    if usd is None:
        if plan_usd_per_credit is not None:
            usd = _round_usd(credits * float(plan_usd_per_credit))
        elif plan_name == "free" or (plan_credits and not fc.get("usd")):
            usd = 0.0
        else:
            usd = _round_usd(credits * per_page_rate)

    basis = fc.get("basis") or (
        "firecrawl_plan_credits" if plan_credits else "firecrawl_flat_rate_est"
    )
    is_est = basis != "firecrawl_api" and float(usd or 0) == 0 and plan_credits

    detail = {
        "usd": _round_usd(float(usd or 0)),
        "credits": credits,
        "basis": basis,
        "is_est": is_est,
        "display_label": SOURCE_DISPLAY["firecrawl"],
        "source": "firecrawl",
        "workflow_step": "enrichment",
    }
    if plan_credits:
        detail["plan_credits"] = int(plan_credits)
        detail["plan_remaining_est"] = max(int(plan_credits) - credits, 0)
    if plan_name:
        detail["plan_name"] = plan_name
    if fc.get("note"):
        detail["note"] = fc["note"]
    return float(detail["usd"]), {"firecrawl": detail}


def _llm_from_entry(
    llm: dict[str, Any],
    tracker: dict[str, Any],
    llm_rate: float,
) -> tuple[float, dict[str, Any]]:
    tokens = int(llm.get("tokens") or tracker.get("llm_tokens") or 0)
    usd = llm.get("usd")
    if usd is None:
        usd = _round_usd((tokens / 1000) * llm_rate)
    providers = llm.get("providers") or tracker.get("llm_providers") or {}
    primary = llm.get("primary_provider") or tracker.get("llm_primary_provider")
    if not primary and providers:
        primary = max(providers, key=lambda k: int(providers[k] or 0))

    label = SOURCE_DISPLAY["llm"]
    if primary:
        label = f"LLM (est) — {primary}"

    detail = {
        "usd": _round_usd(float(usd or 0)),
        "tokens": tokens,
        "providers": providers,
        "primary_provider": primary,
        "basis": "llm_flat_rate_est",
        "is_est": True,
        "display_label": label,
        "source": "llm",
        "workflow_step": "enrichment",
    }
    if llm.get("note"):
        detail["note"] = llm["note"]
    return float(detail["usd"]), {"llm": detail}


def _add_source(by_source: dict[str, dict], name: str, usd: float, detail: dict[str, Any]) -> None:
    if name not in by_source:
        by_source[name] = _empty_source()
        by_source[name]["display_label"] = SOURCE_DISPLAY.get(name, name)
    by_source[name]["usd"] = _round_usd(by_source[name]["usd"] + usd)
    if detail.get("basis"):
        by_source[name]["basis"] = detail["basis"]
    if detail.get("is_est"):
        by_source[name]["is_est"] = True
    if detail.get("credits"):
        by_source[name]["credits"] = int(by_source[name].get("credits") or 0) + int(detail["credits"])
    if detail.get("tokens"):
        by_source[name]["tokens"] = int(by_source[name].get("tokens") or 0) + int(detail["tokens"])
    if detail.get("providers"):
        merged = dict(by_source[name].get("providers") or {})
        for k, v in detail["providers"].items():
            merged[k] = int(merged.get(k) or 0) + int(v or 0)
        by_source[name]["providers"] = merged
    if detail.get("plan_credits"):
        by_source[name]["plan_credits"] = int(detail["plan_credits"])


def build_campaign_report(campaign_id: str, root: Optional[Path] = None) -> dict[str, Any]:
    from apify_client import ApifyClient

    root = root or Path("data")
    campaign_dir = root / "campaigns" / campaign_id
    manifest_path = campaign_dir / "cost_runs.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rates = manifest.get("unit_rates_usd") or {}
    apollo_rate = float(rates.get("apollo_per_credit") or os.environ.get("APOLLO_CREDIT_USD", "0.02"))
    fc_rate = float(rates.get("firecrawl_per_page") or os.environ.get("FIRECRAWL_PAGE_USD", "0.01"))
    llm_rate = float(rates.get("llm_per_1k_tokens") or os.environ.get("LLM_PER_1K_TOKENS_USD", "0.003"))
    fc_plan = manifest.get("firecrawl_plan") or {}

    context = _load_campaign_context(campaign_id, root)
    client_id = None
    if context:
        client_id = (context.get("sender") or {}).get("client_id") or (
            context.get("summary") or {}
        ).get("client_name")

    client = ApifyClient(os.environ["APIFY_TOKEN"])

    run_rows: list[dict[str, Any]] = []
    by_step: dict[str, dict[str, Any]] = {k: _empty_step() for k in WORKFLOW_STEPS}
    by_source: dict[str, dict[str, Any]] = {k: _empty_source() for k in SOURCES}
    for k in SOURCES:
        by_source[k]["display_label"] = SOURCE_DISPLAY[k]

    for entry in manifest.get("runs", []):
        stage = _norm_stage(entry.get("stage", ""))
        run_date = entry.get("started_at_utc") or entry.get("ended_at_utc")
        row: dict[str, Any] = {
            "run_key": entry.get("run_key"),
            "label": entry.get("label"),
            "workflow_step": stage,
            "run_id": entry.get("run_id"),
            "run_date": (run_date or "")[:10] if run_date else None,
            "started_at_utc": entry.get("started_at_utc"),
            "ended_at_utc": entry.get("ended_at_utc"),
            "outcome": entry.get("outcome", {}),
            "sources": {},
            "total_usd": 0.0,
            "apify_breakdown": [],
        }

        if stage == "seeding":
            start = _parse_dt(entry["started_at_utc"])
            end = _parse_dt(entry["ended_at_utc"])
            apify_runs = _fetch_apify_runs(client, start, end)
            apify_runs = _resolve_pinned_apify_runs(client, entry, apify_runs)
            apify_usd = _round_usd(sum(float(r["usd"]) for r in apify_runs))
            breakdown = _group_apify_by_actor(apify_runs)
            detail = {
                "usd": apify_usd,
                "basis": "apify_api",
                "is_est": False,
                "display_label": SOURCE_DISPLAY["apify"],
                "source": "apify",
                "workflow_step": "seeding",
                "runs": apify_runs,
                "by_actor": breakdown,
            }
            row["sources"]["apify_linkedin_jobs_seed"] = detail
            row["apify_breakdown"] = breakdown
            row["total_usd"] = apify_usd
            by_step["seeding"]["usd"] = _round_usd(by_step["seeding"]["usd"] + apify_usd)
            by_step["seeding"]["runs"] += 1
            _add_source(by_source, "apify", apify_usd, detail)

        elif stage == "apollo_contact":
            ap = entry.get("apollo") or {}
            total_usd, ap_sources = _apollo_from_entry(
                entry, ap, apollo_rate, campaign_dir, client_id
            )
            row["sources"] = ap_sources
            if ap_sources.get("apollo"):
                row["apollo_detail"] = {**ap, "resolved_credits": ap_sources["apollo"].get("credits")}
            else:
                row["apollo_detail"] = ap
            row["total_usd"] = total_usd
            by_step["apollo_contact"]["usd"] = _round_usd(by_step["apollo_contact"]["usd"] + total_usd)
            by_step["apollo_contact"]["runs"] += 1
            for src_detail in ap_sources.values():
                _add_source(by_source, "apollo", float(src_detail["usd"]), src_detail)

        elif stage == "enrichment":
            apify_block = entry.get("apify") or {}
            tracker = entry.get("cost_tracker") or {}
            cost_log = _resolve_run_cost_log(campaign_dir, entry.get("run_id") or "", client_id)
            api_reconcile = cost_log.get("apify_api_reconcile") or {}

            apify_usd = float(
                apify_block.get("api_truth_usd")
                if apify_block.get("api_truth_usd") is not None
                else apify_block.get("cost_tracker_usd")
                or tracker.get("apify_usd")
                or api_reconcile.get("total_usd")
                or 0
            )
            jobs_usd = float(apify_block.get("afanasenko_jobs_usd") or 0)
            other_apify = float(
                apify_block.get("other_apify_usd")
                if apify_block.get("other_apify_usd") is not None
                else max(apify_usd - jobs_usd, 0)
            )

            by_actor = apify_block.get("by_actor") or api_reconcile.get("by_actor") or []
            if not by_actor and entry.get("started_at_utc") and entry.get("ended_at_utc"):
                window_runs = _fetch_apify_runs(
                    client,
                    _parse_dt(entry["started_at_utc"]),
                    _parse_dt(entry["ended_at_utc"]),
                )
                by_actor = _group_apify_by_actor(window_runs)
                row["apify_breakdown"] = by_actor
            else:
                row["apify_breakdown"] = by_actor

            if jobs_usd:
                jobs_detail = {
                    "usd": _round_usd(jobs_usd),
                    "basis": "apify_api",
                    "is_est": False,
                    "display_label": "Apify — afanasenko/linkedin-jobs-scraper",
                    "source": "apify",
                    "workflow_step": "enrichment",
                    "actor": "afanasenko/linkedin-jobs-scraper",
                    "optional": True,
                    "warning": apify_block.get("note") or "Expensive/broken actor — included in total",
                }
                row["sources"]["apify_linkedin_jobs_company"] = jobs_detail
            if other_apify or (apify_usd and not jobs_usd):
                li_usd = _round_usd(other_apify if jobs_usd else apify_usd)
                li_detail = {
                    "usd": li_usd,
                    "basis": "apify_api",
                    "is_est": False,
                    "display_label": SOURCE_DISPLAY["apify"],
                    "source": "apify",
                    "workflow_step": "enrichment",
                    "cost_tracker_usd": apify_block.get("cost_tracker_usd"),
                    "api_truth_usd": apify_block.get("api_truth_usd"),
                    "by_actor": [a for a in by_actor if "afanasenko" not in (a.get("actor") or "")],
                }
                row["sources"]["apify_linkedin_company_person"] = li_detail

            fc_usd, fc_sources = _firecrawl_from_entry(
                entry.get("firecrawl") or {},
                tracker,
                fc_plan,
                fc_rate,
            )
            llm_usd, llm_sources = _llm_from_entry(
                entry.get("llm") or {},
                tracker,
                llm_rate,
            )
            row["sources"].update(fc_sources)
            row["sources"].update(llm_sources)

            total_usd = _round_usd(apify_usd + fc_usd + llm_usd)
            row["total_usd"] = total_usd
            by_step["enrichment"]["usd"] = _round_usd(by_step["enrichment"]["usd"] + total_usd)
            by_step["enrichment"]["runs"] += 1

            apify_detail = {"usd": _round_usd(apify_usd), "basis": "apify_api", "by_actor": by_actor}
            _add_source(by_source, "apify", apify_usd, apify_detail)
            if fc_usd or fc_sources.get("firecrawl", {}).get("credits"):
                _add_source(by_source, "firecrawl", fc_usd, fc_sources["firecrawl"])
            if llm_usd:
                _add_source(by_source, "llm", llm_usd, llm_sources["llm"])

        else:
            row["note"] = f"Unhandled workflow_step={stage}"

        run_rows.append(row)

    for v in by_step.values():
        v["usd"] = _round_usd(v["usd"])
    for v in by_source.values():
        v["usd"] = _round_usd(v["usd"])

    total_usd = _round_usd(sum(v["usd"] for v in by_step.values()))

    stages_dir = campaign_dir / "stages"
    contactable = 0
    contactable_path = stages_dir / "04_apollo_contactable.csv"
    if contactable_path.exists():
        import csv

        contactable = sum(1 for _ in csv.DictReader(contactable_path.open(encoding="utf-8")))

    client_name = None
    if context and context.get("summary"):
        client_name = context["summary"].get("client_name")

    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "client_id": client_id,
        "client_name": client_name,
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "unit_rates_usd": rates,
        "firecrawl_plan": fc_plan,
        "campaign_context": context,
        "funnel_steps": [{"key": k, "label": v} for k, v in FUNNEL_STEPS],
        "by_workflow_step": by_step,
        "by_source": by_source,
        "by_run": run_rows,
        "totals": {
            "usd_total": total_usd,
            "per_contactable_usd": _round_usd(total_usd / max(contactable, 1)),
            "contactable_leads": contactable,
        },
        "note": (
            "USD from Apify API (usageTotalUsd), Apollo credits_consumed × rate, "
            "Firecrawl plan credits (free plan USD=0), LLM flat-rate (est) with provider tag. "
            "Apify actor breakdown includes afanasenko jobs when billed."
        ),
    }



def write_markdown(report: dict[str, Any], path: Path) -> None:
    from agent.utils.campaign_handoff import format_icp_markdown

    steps = report["by_workflow_step"]
    sources = report["by_source"]
    totals = report["totals"]
    lines = [
        f"# Cost log — {report['campaign_id']}",
        "",
        f"- Logged: `{report['logged_at']}`",
        f"- Client: **{report.get('client_name') or report.get('client_id') or '—'}**",
        f"- Contactable leads: **{totals['contactable_leads']}**",
        f"- Total: **~${totals['usd_total']:.2f}**",
        "",
    ]
    lines.extend(format_icp_markdown(report.get("campaign_context")))
    lines += [
        "## Funnel workflow (operator steps)",
        "",
    ]
    for step in report.get("funnel_steps") or []:
        key = step["key"]
        spend = steps.get(key) if key in WORKFLOW_STEPS else None
        usd_note = f" — **${spend['usd']:.4f}**" if spend else " — $0 (rules/local)"
        lines.append(f"- `{key}` — {step['label']}{usd_note}")
    lines += [
        "",
        "## By workflow step (spend)",
        "",
        "| Step | USD | Runs |",
        "|------|----:|-----:|",
    ]
    step_labels = {
        "seeding": "Seeding",
        "apollo_contact": "Apollo contact",
        "enrichment": "Enrichment",
    }
    for key in WORKFLOW_STEPS:
        c = steps[key]
        lines.append(f"| {step_labels[key]} | ${c['usd']:.4f} | {c['runs']} |")
    lines += [
        f"| **Total** | **${totals['usd_total']:.4f}** | |",
        "",
        "## By source",
        "",
        "| Source | USD | Detail |",
        "|--------|----:|--------|",
    ]
    for key in SOURCES:
        s = sources.get(key) or _empty_source()
        detail_parts = []
        if s.get("credits"):
            detail_parts.append(f"{s['credits']} credits")
        if s.get("tokens"):
            detail_parts.append(f"{s['tokens']} tokens")
        if s.get("providers"):
            detail_parts.append(
                ", ".join(f"{k}:{v}" for k, v in s["providers"].items())
            )
        if s.get("is_est"):
            detail_parts.append("est")
        lines.append(
            f"| {s.get('display_label') or key} | ${s['usd']:.4f} | "
            f"{'; '.join(detail_parts) or s.get('basis') or '—'} |"
        )
    lines += [
        "",
        f"**Per contactable lead:** ~${totals['per_contactable_usd']:.3f}",
        "",
        "## By run",
        "",
        "| Date | Run | Step | USD |",
        "|------|-----|------|----:|",
    ]
    for r in report["by_run"]:
        lines.append(
            f"| {r.get('run_date') or '—'} | {r['label']} `{r['run_key']}` | "
            f"{r['workflow_step']} | ${r['total_usd']:.4f} |"
        )
    lines += ["", "## Apify actor breakdown (per run)", ""]
    for r in report["by_run"]:
        breakdown = r.get("apify_breakdown") or []
        if not breakdown:
            for src in (r.get("sources") or {}).values():
                if src.get("by_actor"):
                    breakdown = src["by_actor"]
        if breakdown:
            lines.append(f"### {r['label']}")
            for a in breakdown:
                warn = " ⚠" if "afanasenko" in (a.get("actor") or "") else ""
                lines.append(
                    f"- `{a['actor']}` ${a['usd']:.4f} ({a['runs']} runs){warn}"
                )
            lines.append("")
    lines.append(f"_Note: {report.get('note')}_")
    path.write_text("\n".join(lines), encoding="utf-8")


def _source_cell(detail: dict[str, Any]) -> str:
    label = detail.get("display_label") or detail.get("source") or ""
    usd = float(detail.get("usd") or detail.get("usd_est") or 0)
    parts = [f"{label} ${usd:.4f}"]
    if detail.get("credits"):
        parts.append(f"{detail['credits']} cr")
    if detail.get("tokens"):
        parts.append(f"{detail['tokens']} tok")
    if detail.get("plan_credits"):
        parts.append(f"plan {detail['plan_credits']}")
    return escape(" · ".join(parts))


def write_campaign_html(report: dict[str, Any], path: Path) -> None:
    from agent.utils.campaign_handoff import format_icp_html

    steps = report["by_workflow_step"]
    sources = report["by_source"]
    totals = report["totals"]
    step_labels = {
        "seeding": "Seeding — LinkedIn jobs scrape",
        "apollo_contact": "Apollo — org enrich + email reveal",
        "enrichment": "Enrichment — Firecrawl + Apify LinkedIn + LLM",
    }

    funnel_rows = ""
    step_spend = {k: steps[k]["usd"] for k in WORKFLOW_STEPS}
    for key, label in FUNNEL_STEPS:
        usd = step_spend.get(key, 0.0)
        usd_cell = f"<td class='num'>${usd:.4f}</td>" if key in WORKFLOW_STEPS else "<td class='num muted'>—</td>"
        funnel_rows += f"<tr><td><code>{escape(key)}</code></td><td>{escape(label)}</td>{usd_cell}</tr>"

    step_rows = ""
    for key in WORKFLOW_STEPS:
        c = steps[key]
        step_rows += f"""
        <tr>
          <td>{escape(step_labels[key])}</td>
          <td class="num">${c['usd']:.4f}</td>
          <td class="num">{c['runs']}</td>
        </tr>"""

    source_rows = ""
    for key in SOURCES:
        s = sources.get(key) or _empty_source()
        detail = []
        if s.get("credits"):
            detail.append(f"{s['credits']} credits")
            if s.get("plan_credits"):
                detail.append(f"of {s['plan_credits']} plan")
        if s.get("tokens"):
            detail.append(f"{s['tokens']} tokens")
        if s.get("providers"):
            detail.append(
                ", ".join(f"{k}: {v}" for k, v in s["providers"].items())
            )
        if s.get("basis"):
            detail.append(s["basis"])
        source_rows += f"""
        <tr>
          <td>{escape(s.get('display_label') or key)}</td>
          <td class="num">${s['usd']:.4f}</td>
          <td>{escape('; '.join(detail) or '—')}</td>
        </tr>"""

    run_rows_html = ""
    apify_sections = ""
    for r in report["by_run"]:
        srcs = " · ".join(_source_cell(d) for d in (r.get("sources") or {}).values())
        outcome = r.get("outcome") or {}
        outcome_s = ", ".join(f"{k}={v}" for k, v in outcome.items() if k != "note")
        run_rows_html += f"""
        <tr>
          <td>{escape(r.get('run_date') or '—')}</td>
          <td>{escape(r.get('label') or '')}<br><code>{escape(r.get('run_key') or '')}</code></td>
          <td>{escape(r.get('workflow_step') or '')}</td>
          <td class="num">${r.get('total_usd', 0):.4f}</td>
          <td>{srcs}</td>
          <td>{escape(outcome_s)}</td>
        </tr>"""
        breakdown = r.get("apify_breakdown") or []
        if breakdown:
            actor_rows = ""
            for a in breakdown:
                warn = " class='warn'" if "afanasenko" in (a.get("actor") or "") else ""
                actor_rows += (
                    f"<tr{warn}><td><code>{escape(a['actor'])}</code></td>"
                    f"<td class='num'>{a['runs']}</td>"
                    f"<td class='num'>${a['usd']:.4f}</td>"
                    f"<td>{escape(', '.join(a.get('statuses') or []))}</td></tr>"
                )
            apify_sections += f"""
            <h3>{escape(r.get('label') or r.get('run_key'))}</h3>
            <table><thead><tr><th>Actor</th><th>Runs</th><th>USD</th><th>Status</th></tr></thead>
            <tbody>{actor_rows}</tbody></table>"""

    icp_html = format_icp_html(report.get("campaign_context"))
    portal_link = '<p class="meta"><a href="/dashboard">Lead list ledger</a> (serve via serve_cost_dashboard.py)</p>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Cost — {escape(report['campaign_id'])}</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem; color: #111; max-width: 1200px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0 2rem; font-size: 0.92rem; }}
    th, td {{ border: 1px solid #ddd; padding: 0.5rem 0.75rem; text-align: left; vertical-align: top; }}
    th {{ background: #f4f4f5; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .muted {{ color: #888; }}
    h1 {{ margin-bottom: 0.25rem; }}
    .meta {{ color: #555; margin-bottom: 1.5rem; }}
    code {{ font-size: 0.85em; }}
    tr.warn {{ background: #fef3c7; }}
    tr.warn td:first-child {{ font-weight: 600; }}
  </style>
</head>
<body>
  {portal_link}
  <h1>Cost — {escape(report['campaign_id'])}</h1>
  <p class="meta">
    {escape(report.get('client_name') or report.get('client_id') or '')} ·
    logged {escape(report['logged_at'])} ·
    {totals['contactable_leads']} contactable ·
    total <strong>${totals['usd_total']:.2f}</strong>
    (~${totals['per_contactable_usd']:.3f}/lead)
  </p>

  {icp_html}

  <h2>Funnel workflow</h2>
  <p class="meta">Steps with no API spend show —. Paid steps: seeding, apollo_contact, enrichment.</p>
  <table>
    <thead><tr><th>Step</th><th>Description</th><th>USD</th></tr></thead>
    <tbody>{funnel_rows}</tbody>
  </table>

  <h2>By workflow step</h2>
  <table>
    <thead><tr><th>Step</th><th>USD</th><th>Runs</th></tr></thead>
    <tbody>{step_rows}
    <tr><td><strong>Total</strong></td><td class="num"><strong>${totals['usd_total']:.4f}</strong></td><td></td></tr>
    </tbody>
  </table>

  <h2>By source</h2>
  <table>
    <thead><tr><th>Source</th><th>USD</th><th>Detail</th></tr></thead>
    <tbody>{source_rows}</tbody>
  </table>

  <h2>By run (date)</h2>
  <table>
    <thead><tr><th>Date</th><th>Run</th><th>Step</th><th>USD</th><th>Sources</th><th>Outcome</th></tr></thead>
    <tbody>{run_rows_html}</tbody>
  </table>

  <h2>Apify breakdown by actor</h2>
  {apify_sections or '<p class="meta">No Apify actor rows in manifest window.</p>'}

  <p class="meta">{escape(report.get('note') or '')}</p>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")



def load_sample_report() -> dict[str, Any]:
    sample_path = Path("examples") / "campaign_cost_dashboard" / "sample_report.json"
    if not sample_path.exists():
        raise FileNotFoundError(f"Missing template report: {sample_path}")
    return json.loads(sample_path.read_text(encoding="utf-8"))


def write_campaign_cost_artifacts(
    report: dict[str, Any],
    *,
    campaign_dir: Path | None = None,
    html_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    cid = report.get("campaign_id") or "campaign"
    out_dir = campaign_dir or (Path("data") / "campaigns" / cid)
    json_path = out_dir / "cost_log.json"
    md_path = out_dir / "cost_log.md"
    dashboard_path = html_path or (out_dir / "cost_dashboard.html")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_markdown(report, md_path)
    write_campaign_html(report, dashboard_path)
    return json_path, md_path, dashboard_path
