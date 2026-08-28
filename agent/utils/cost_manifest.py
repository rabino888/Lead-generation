"""
Auto-append stage cost rows to data/campaigns/{id}/cost_runs.json.

Stage CLIs call finalize_stage_cost() on exit so build_campaign_cost_report.py
has a current manifest without hand-editing JSON.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _rates() -> dict[str, float]:
    return {
        "apollo_per_credit": float(os.environ.get("APOLLO_CREDIT_USD", "0.02")),
        "firecrawl_per_page": float(os.environ.get("FIRECRAWL_PAGE_USD", "0.01")),
        "llm_per_1k_tokens": float(os.environ.get("LLM_PER_1K_TOKENS_USD", "0.003")),
    }


def manifest_path(campaign_dir: Path) -> Path:
    return campaign_dir / "cost_runs.json"


def load_manifest(campaign_dir: Path, campaign_id: str) -> dict[str, Any]:
    path = manifest_path(campaign_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "schema_version": "1",
        "campaign_id": campaign_id,
        "unit_rates_usd": _rates(),
        "runs": [],
    }


def _manifest_stage(phase: str) -> str:
    p = (phase or "").strip().lower()
    if "apollo" in p or p == "backfill_dm_linkedin":
        return "apollo_contact"
    if "enrich" in p or "linkedin" in p:
        return "enrichment"
    if "seed" in p:
        return "seeding"
    return p or "unknown"


def _flat_tracker(run_totals: dict[str, Any]) -> dict[str, Any]:
    return {
        "apify_usd": run_totals.get("apify_usd"),
        "firecrawl_usd": run_totals.get("firecrawl_usd"),
        "firecrawl_pages": run_totals.get("firecrawl_pages"),
        "llm_usd": run_totals.get("llm_usd"),
        "llm_tokens": run_totals.get("llm_tokens"),
        "apollo_usd": run_totals.get("apollo_usd"),
        "total_usd": run_totals.get("total_usd"),
    }


def build_manifest_entry_from_report(
    report: dict[str, Any],
    *,
    label: Optional[str] = None,
    outcome: Optional[dict[str, Any]] = None,
    apollo_extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build one cost_runs.json row from persist_stage_cost_report output."""
    phase = report.get("phase") or ""
    stage = _manifest_stage(phase)
    run_id = report.get("run_id") or ""
    ct = report.get("cost_tracker") or {}
    run_totals = ct.get("run_totals") or {}
    apify_truth = float(report.get("apify_truth_usd") or 0)
    drift = float(report.get("apify_drift_usd") or 0)

    entry: dict[str, Any] = {
        "run_key": f"{phase}_{run_id}" if phase else run_id,
        "label": label or phase or run_id,
        "stage": stage,
        "run_id": run_id,
        "started_at_utc": report.get("started_at_utc"),
        "ended_at_utc": report.get("ended_at_utc"),
        "cost_log": report.get("cost_log_path"),
        "outcome": outcome or {},
    }

    if stage == "apollo_contact":
        credits = int((apollo_extra or {}).get("credits_consumed") or 0)
        apollo_block: dict[str, Any] = {
            "credits_consumed": credits,
            "cost_tracker_usd": run_totals.get("apollo_usd"),
            "source": "CostTracker + Apollo credits_consumed",
        }
        for key in (
            "companies_attempted",
            "email_reveals",
            "contactable",
            "rejected",
            "emails_attempted",
            "linkedin_filled",
        ):
            if apollo_extra and apollo_extra.get(key) is not None:
                apollo_block[key] = apollo_extra[key]
        if apollo_extra and apollo_extra.get("note"):
            apollo_block["note"] = apollo_extra["note"]
        entry["apollo"] = apollo_block
        if run_totals:
            entry["cost_tracker"] = _flat_tracker(run_totals)

    elif stage == "enrichment":
        entry["apify"] = {
            "cost_tracker_usd": run_totals.get("apify_usd"),
            "api_truth_usd": apify_truth,
            "drift_usd": drift,
            "source": "CostTracker + Apify API reconcile",
        }
        entry["firecrawl"] = {
            "pages": run_totals.get("firecrawl_pages"),
            "usd": run_totals.get("firecrawl_usd"),
            "confidence": "estimate",
            "source": "CostTracker",
        }
        entry["llm"] = {
            "tokens": run_totals.get("llm_tokens"),
            "usd": run_totals.get("llm_usd"),
            "confidence": "estimate",
            "source": "CostTracker flat rate",
        }
        entry["cost_tracker"] = _flat_tracker(run_totals)
    else:
        if run_totals:
            entry["cost_tracker"] = _flat_tracker(run_totals)

    return entry


def append_manifest_run(
    campaign_dir: Path,
    entry: dict[str, Any],
    *,
    campaign_id: Optional[str] = None,
) -> Path:
    cid = campaign_id or entry.get("campaign_id") or campaign_dir.name
    manifest = load_manifest(campaign_dir, cid)
    manifest["unit_rates_usd"] = manifest.get("unit_rates_usd") or _rates()
    runs = list(manifest.get("runs") or [])
    run_key = entry.get("run_key")
    run_id = entry.get("run_id")
    replaced = False
    for i, row in enumerate(runs):
        if run_key and row.get("run_key") == run_key:
            runs[i] = entry
            replaced = True
            break
        if run_id and row.get("run_id") == run_id:
            runs[i] = entry
            replaced = True
            break
    if not replaced:
        runs.append(entry)
    manifest["runs"] = runs
    path = manifest_path(campaign_dir)
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return path


def rebuild_cost_dashboard(campaign_id: str, root: Optional[Path] = None) -> Path:
    """Rebuild cost_log.json, cost_log.md, cost_dashboard.html."""
    root = root or Path("data")
    campaign_dir = root / "campaigns" / campaign_id
    from scripts.build_campaign_cost_report import build_report, write_html, write_markdown

    report = build_report(campaign_id)
    json_path = campaign_dir / "cost_log.json"
    md_path = campaign_dir / "cost_log.md"
    html_path = campaign_dir / "cost_dashboard.html"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_markdown(report, md_path)
    write_html(report, html_path)
    try:
        from scripts.open_campaign_cost_dashboard import _inject_funnel_html

        _inject_funnel_html(campaign_dir, report)
    except Exception:
        pass
    return html_path


def finalize_stage_cost(
    *,
    campaign_dir: Path,
    campaign_id: str,
    client_id: str,
    run_id: str,
    phase: str,
    started_at: datetime,
    tracker_summary: Optional[dict[str, Any]] = None,
    label: Optional[str] = None,
    outcome: Optional[dict[str, Any]] = None,
    extra: Optional[dict[str, Any]] = None,
    apollo_extra: Optional[dict[str, Any]] = None,
    rebuild_dashboard: bool = False,
) -> tuple[Path, Path]:
    """
    Persist cost_log_{run_id}.json, append cost_runs.json, optionally rebuild dashboard.
    Returns (cost_log_path, manifest_path).
    """
    from agent.utils.apify_spend import persist_stage_cost_report

    report_path = persist_stage_cost_report(
        campaign_dir=campaign_dir,
        client_id=client_id,
        run_id=run_id,
        phase=phase,
        started_at=started_at,
        tracker_summary=tracker_summary,
        extra=extra,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["cost_log_path"] = str(report_path)
    entry = build_manifest_entry_from_report(
        report,
        label=label,
        outcome=outcome,
        apollo_extra=apollo_extra,
    )
    manifest_path_written = append_manifest_run(
        campaign_dir,
        entry,
        campaign_id=campaign_id,
    )
    if rebuild_dashboard:
        rebuild_cost_dashboard(campaign_id)
    return report_path, manifest_path_written
