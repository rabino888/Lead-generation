"""
Lead-list cost dashboard: index over EVERY campaign under `data/campaigns/`.

Wider than a v4-only cost_runs scan:

* v4 campaigns             -> full `build_campaign_report` output
* pre-v4 campaigns         -> ingested from `cost_log_run_*.json` breakdowns
* campaigns with no cost   -> still listed, with `cost_basis: "none"`

so a lead list never disappears from the SPA just because spend was never logged.
Lead counts come from the stage CSVs on disk.

Live UI: `agent/dashboard/static/dashboard.html` + `cost_dashboard_index.json`.
Build with:  python scripts/build_cost_dashboard.py
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agent.utils.cost_report import (
    SCHEMA_VERSION,
    SOURCE_DISPLAY,
    _add_source,
    _load_campaign_context,
    _round_usd,
    build_campaign_report,
)

# Suffixes that mark a CSV as a backup / partial / rejected slice rather than a
# live stage output. Counting these would inflate every lead total.
_SKIP_MARKERS = (".bak", "_partial", "_prefill", "_rejected", "_dropped")

# Matches the `_batch3` / `_batch4` suffix a batched stage run leaves behind.
_BATCH_SUFFIX = re.compile(r"^_batch\d+$")

_COMPANY_KEY = ("website", "company_name", "seed_id")
_PERSON_KEY = ("decision_maker_email", "lead_id")

_LEGACY_SOURCE_KEYS = {
    "apify_usd": "apify",
    "apollo_usd": "apollo",
    "firecrawl_usd": "firecrawl",
    "llm_usd": "llm",
}


def _is_live_stage_csv(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() == ".csv" and not any(m in name for m in _SKIP_MARKERS)


def _count_unique_csv(paths: list[Path], key_fields: tuple[str, ...]) -> int:
    """Count distinct rows across CSVs, keyed on the first non-empty field present.

    Campaigns split their funnel across `_batch3` / `_batch4` files, so a plain row
    sum double-counts companies that appear in more than one batch.
    """
    seen: set[str] = set()
    keyless = 0
    for path in paths:
        try:
            with path.open(encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh):
                    key = ""
                    for field in key_fields:
                        value = (row.get(field) or "").strip().lower()
                        if value:
                            key = value
                            break
                    if key:
                        seen.add(key)
                    else:
                        keyless += 1
        except (OSError, csv.Error):
            continue
    return len(seen) + keyless


def _canonical_or_batches(paths: list[Path], stem: str, *, merged: bool) -> list[Path]:
    """Choose which of a stage's files actually represent the list at that stage.

    A campaign run in batches leaves `03_deduped.csv` next to `03_deduped_batch3.csv`
    and one-off slices like `03_deduped_product.csv`. Summing all of them counts the
    same leads twice — that made the campaign funnel report 111 contactable rows for a
    list that delivered 53. Only the canonical file and `_batchN` siblings count; for
    the person-level stages, whose batches are merged back into the canonical file,
    the canonical file alone is the answer when it exists.
    """
    canonical = next((p for p in paths if p.stem == stem), None)
    if merged and canonical is not None:
        return [canonical]
    keep = [p for p in paths if p.stem == stem or _BATCH_SUFFIX.match(p.stem[len(stem):])]
    return keep or ([canonical] if canonical else [])


def campaign_lead_counts(campaign_dir: Path) -> dict[str, int]:
    """Distinct row counts per funnel stage for one campaign's lead list."""
    stages = campaign_dir / "stages"

    def pick(pattern: str, stem: str, *, merged: bool = False) -> list[Path]:
        if not stages.is_dir():
            return []
        found = [p for p in sorted(stages.glob(pattern)) if _is_live_stage_csv(p)]
        return _canonical_or_batches(found, stem, merged=merged)

    seed_paths = pick("01_raw_seeds*.csv", "01_raw_seeds")
    if not seed_paths:
        # Campaigns predating the stage-CSV funnel keep seeds at the campaign root.
        seed_paths = [p for p in (campaign_dir / "seeds.csv",) if p.exists()]
        seed_paths += [
            p for p in sorted((campaign_dir / "batches").glob("*.csv")) if _is_live_stage_csv(p)
        ]

    return {
        "seeds": _count_unique_csv(seed_paths, _COMPANY_KEY),
        "icp_matched": _count_unique_csv(pick("02_icp_matched*.csv", "02_icp_matched"), _COMPANY_KEY),
        "deduped": _count_unique_csv(pick("03_deduped*.csv", "03_deduped"), _COMPANY_KEY),
        "contactable": _count_unique_csv(
            pick("04_apollo_contactable*.csv", "04_apollo_contactable", merged=True), _PERSON_KEY
        ),
        "enriched": _count_unique_csv(pick("05_enriched*.csv", "05_enriched", merged=True), _PERSON_KEY),
        "scored": _count_unique_csv(pick("06_scored*.csv", "06_scored", merged=True), _PERSON_KEY),
    }


def legacy_campaign_rollup(campaign_dir: Path) -> Optional[dict[str, Any]]:
    """Rollup from pre-v4 `cost_log_run_*.json` files, or None if there are none.

    Their `breakdown` block already carries per-vendor USD, so these runs can sit
    alongside v4 runs. Everything but Apify is flagged estimated — the legacy logs
    modelled Apollo/Firecrawl/LLM from log counts and unit rates.
    """
    logs = sorted(campaign_dir.glob("cost_log_run_*.json"))
    if not logs:
        return None

    by_source: dict[str, dict[str, Any]] = {}
    runs: list[dict[str, Any]] = []
    total = 0.0
    client_id: Optional[str] = None

    for log_path in logs:
        try:
            entry = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        client_id = client_id or entry.get("client_id")
        breakdown = entry.get("breakdown") or {}
        window = entry.get("window_utc") or {}
        run_usd = float(breakdown.get("total_usd") or 0)
        total += run_usd

        sources: dict[str, dict[str, Any]] = {}
        for key, source in _LEGACY_SOURCE_KEYS.items():
            usd = float(breakdown.get(key) or 0)
            if not usd:
                continue
            detail = {
                "usd": _round_usd(usd),
                "display_label": SOURCE_DISPLAY[source],
                "basis": "legacy_cost_log",
                "is_est": source != "apify",
                "source": source,
                "workflow_step": "enrichment",
            }
            sources[source] = detail
            _add_source(by_source, source, usd, detail)

        started = window.get("start") or ""
        runs.append(
            {
                "run_key": log_path.stem,
                "label": entry.get("phase") or log_path.stem,
                "workflow_step": "enrichment",
                "run_id": entry.get("run_id"),
                "run_date": started[:10],
                "started_at_utc": started,
                "ended_at_utc": window.get("end"),
                "total_usd": _round_usd(run_usd),
                "outcome": {"leads_enriched": entry.get("leads_enriched")},
                "sources": sources,
                "apify_breakdown": entry.get("apify_by_actor") or [],
            }
        )

    return {
        "client_id": client_id,
        "usd_total": _round_usd(total),
        "by_source": by_source,
        "by_run": runs,
        "cost_basis": "legacy_cost_log",
    }


def _icp_summary(context: dict[str, Any]) -> dict[str, Any]:
    """Slim ICP digest for the campaign page.

    The raw ICP carries long keyword and exclusion lists that would bloat every
    embedded payload; the drill-down only needs the targeting shape.
    """
    icp = context.get("icp") or {}
    geo = icp.get("geo") or {}
    return {
        "industries": (icp.get("industries") or [])[:8],
        "primary_location": geo.get("primary_location"),
        "contact_locations": icp.get("contact_locations") or [],
        "company_size_min": icp.get("company_size_min"),
        "company_size_max": icp.get("company_size_max"),
        "website_analysis_mode": icp.get("website_analysis_mode"),
        "job_titles": (icp.get("job_titles") or [])[:8],
    }


def _campaign_type_for(campaign_dir: Path) -> str:
    plan = _plan_summary(campaign_dir)
    raw = (plan.get("campaign_type") or "").strip()
    if raw in ("company_outreach", "talent_search"):
        return raw
    meta_path = campaign_dir / "campaign.json"
    if meta_path.is_file():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            raw = (data.get("campaign_type") or "").strip()
            if raw in ("company_outreach", "talent_search"):
                return raw
        except (OSError, json.JSONDecodeError):
            pass
    return "company_outreach"


def _plan_summary(campaign_dir: Path) -> dict[str, Any]:
    """Lightweight enrichment_plan.json slice for the ledger UI."""
    plan_path = campaign_dir / "enrichment_plan.json"
    if not plan_path.is_file():
        return {}
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    modules = data.get("modules") or {}
    return {
        "campaign_type": data.get("campaign_type") or "company_outreach",
        "modules": {k: bool(v) for k, v in modules.items()} if isinstance(modules, dict) else {},
        "updated_at_utc": data.get("updated_at_utc"),
    }


def _campaign_json_meta(campaign_dir: Path) -> dict[str, Any]:
    path = campaign_dir / "campaign.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _apply_campaign_meta(entry: dict[str, Any], campaign_dir: Path) -> dict[str, Any]:
    """Prefer campaign.json client + display_name over cost-report sender defaults."""
    meta = _campaign_json_meta(campaign_dir)
    if meta.get("client_id"):
        entry["client_id"] = meta["client_id"]
    if meta.get("client_name"):
        entry["client_name"] = meta["client_name"]
    display = meta.get("display_name") or meta.get("name")
    if display:
        entry["display_name"] = display
    else:
        entry.setdefault("display_name", entry.get("campaign_id"))
    return entry


def _v4_campaign_entry(
    campaign_id: str, campaigns_root: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build one v4 campaign entry plus its run rows, or an error entry."""
    try:
        report = build_campaign_report(campaign_id, root=campaigns_root.parent)
    except Exception as exc:  # noqa: BLE001 - one bad campaign must not sink the page
        return (
            {
                "campaign_id": campaign_id,
                "client_id": None,
                "client_name": campaign_id,
                "campaign_type": _campaign_type_for(campaigns_root / campaign_id),
                "usd_total": 0.0,
                "contactable_leads": 0,
                "by_source": {},
                "plan": _plan_summary(campaigns_root / campaign_id),
                "cost_basis": "error",
                "error": str(exc),
            },
            [],
        )

    totals = report.get("totals") or {}
    context = report.get("campaign_context") or {}
    sender = context.get("sender") or {}
    cid = report.get("client_id") or sender.get("client_id") or "unknown"
    cname = report.get("client_name") or sender.get("company") or cid
    ctype = _campaign_type_for(campaigns_root / campaign_id)

    entry = {
        "campaign_id": campaign_id,
        "client_id": cid,
        "client_name": cname,
        "campaign_type": ctype,
        "usd_total": float(totals.get("usd_total") or 0),
        "contactable_leads": int(totals.get("contactable_leads") or 0),
        "per_contactable_usd": totals.get("per_contactable_usd"),
        "logged_at": report.get("logged_at"),
        "by_source": report.get("by_source") or {},
        "by_workflow_step": report.get("by_workflow_step") or {},
        "unit_rates_usd": report.get("unit_rates_usd") or {},
        "firecrawl_plan": report.get("firecrawl_plan") or {},
        "note": report.get("note"),
        "icp": _icp_summary(context),
        "plan": _plan_summary(campaigns_root / campaign_id),
        "cost_basis": "cost_runs",
    }

    runs = []
    for r in report.get("by_run") or []:
        run_row = {k: v for k, v in r.items() if k != "run_id"}
        run_row.update(
            campaign_id=campaign_id,
            client_id=cid,
            client_name=cname,
            campaign_type=ctype,
            pipeline_run_id=r.get("run_id"),
        )
        runs.append(run_row)
    return entry, runs


def build_dashboard_index(campaigns_root: Optional[Path] = None) -> dict[str, Any]:
    """Cost + lead-count index across every campaign folder."""
    from agent.utils.client_store import list_all_campaign_dirs, resolve_campaign_dir

    campaigns_root = campaigns_root or Path("data") / "campaigns"
    data_root = campaigns_root.parent if campaigns_root.name == "campaigns" else campaigns_root
    campaign_dirs = list_all_campaign_dirs(data_root)
    campaigns: list[dict[str, Any]] = []
    all_runs: list[dict[str, Any]] = []
    by_client: dict[str, dict[str, Any]] = {}

    for campaign_dir in campaign_dirs:
        campaign_id = campaign_dir.name
        context = _load_campaign_context(campaign_id, data_root) or {}
        sender = context.get("sender") or {}

        if (campaign_dir / "cost_runs.json").exists():
            entry, runs = _v4_campaign_entry(campaign_id, campaigns_root)
            # Prefer resolved dir for plan/type when nested under clients
            entry["campaign_type"] = _campaign_type_for(campaign_dir)
            entry["plan"] = _plan_summary(campaign_dir)
        else:
            legacy = legacy_campaign_rollup(campaign_dir) or {}
            cid = legacy.get("client_id") or sender.get("client_id") or "unknown"
            cname = sender.get("company") or sender.get("name") or cid
            ctype = _campaign_type_for(campaign_dir)
            entry = {
                "campaign_id": campaign_id,
                "client_id": cid,
                "client_name": cname,
                "campaign_type": ctype,
                "usd_total": float(legacy.get("usd_total") or 0),
                "contactable_leads": 0,
                "per_contactable_usd": None,
                "logged_at": None,
                "by_source": legacy.get("by_source") or {},
                "icp": _icp_summary(context),
                "plan": _plan_summary(campaign_dir),
                "cost_basis": legacy.get("cost_basis", "none"),
            }
            runs = []
            for r in legacy.get("by_run") or []:
                run_row = {k: v for k, v in r.items() if k != "run_id"}
                run_row.update(
                    campaign_id=campaign_id,
                    client_id=cid,
                    client_name=cname,
                    campaign_type=ctype,
                    pipeline_run_id=r.get("run_id"),
                )
                runs.append(run_row)

        entry = _apply_campaign_meta(entry, campaign_dir)
        for r in runs:
            r["client_id"] = entry.get("client_id")
            r["client_name"] = entry.get("client_name")
            r["display_name"] = entry.get("display_name")

        campaigns.append(entry)
        all_runs.extend(runs)

        cid = entry.get("client_id")
        if cid:
            bucket = by_client.setdefault(
                cid,
                {
                    "client_id": cid,
                    "client_name": entry.get("client_name") or cid,
                    "usd_total": 0.0,
                    "campaign_ids": [],
                    "contactable_leads": 0,
                },
            )
            bucket["usd_total"] = _round_usd(
                float(bucket["usd_total"]) + float(entry.get("usd_total") or 0)
            )
            bucket["campaign_ids"].append(campaign_id)

    # Lead counts + cost per delivered lead, for v4 and non-v4 campaigns alike.
    for c in campaigns:
        camp_path = resolve_campaign_dir(data_root, c.get("campaign_id") or "") or (
            campaigns_root / (c.get("campaign_id") or "")
        )
        counts = campaign_lead_counts(camp_path)
        c["lead_counts"] = counts
        delivered = int(c.get("contactable_leads") or 0) or counts["contactable"]
        c["delivered_leads"] = delivered
        usd = float(c.get("usd_total") or 0)
        c["per_lead_usd"] = _round_usd(usd / delivered) if delivered and usd else None

        bucket = by_client.get(c.get("client_id") or "")
        if bucket is not None:
            bucket["contactable_leads"] = int(bucket.get("contactable_leads") or 0) + delivered

    for bucket in by_client.values():
        leads = int(bucket.get("contactable_leads") or 0)
        usd = float(bucket.get("usd_total") or 0)
        bucket["per_lead_usd"] = _round_usd(usd / leads) if leads and usd else None

    all_runs.sort(key=lambda r: r.get("started_at_utc") or r.get("run_date") or "", reverse=True)
    campaigns.sort(key=lambda c: float(c.get("usd_total") or 0), reverse=True)

    tracked = [c for c in campaigns if c.get("cost_basis") != "none"]
    return {
        "schema_version": SCHEMA_VERSION,
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "scope": {"kind": "all", "id": None},
        "campaigns": campaigns,
        "runs": all_runs,
        "by_client": by_client,
        "totals": {
            "usd_total": _round_usd(sum(float(c.get("usd_total") or 0) for c in campaigns)),
            "campaign_count": len(campaigns),
            "tracked_campaign_count": len(tracked),
            "run_count": len(all_runs),
            "client_count": len(by_client),
            "delivered_leads": sum(int(c.get("delivered_leads") or 0) for c in campaigns),
        },
    }
