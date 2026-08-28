"""
Apify spend helpers — always refetch billed USD; reconcile windows against the API.

CostTracker undercounted afanasenko ($6.27) because ActorClient.call() return values
often omit final usageTotalUsd until the run is re-fetched.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


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


def extract_run_usd(run: Any) -> float:
    """Best-effort USD from a run object/dict (may be 0 before refetch)."""
    for key in ("usageTotalUsd", "usage_total_usd", "usageUsd", "usage_usd"):
        if isinstance(run, dict) and run.get(key) is not None:
            return float(run[key])
        if hasattr(run, key):
            value = getattr(run, key)
            if value is not None:
                return float(value)
    return 0.0


def refetch_run_usd(run: Any, *, attempts: int = 4, delay_s: float = 1.5) -> float:
    """
    Re-fetch the run from Apify so usageTotalUsd is populated.
    PPE actors often return 0 on the immediate call() object; retry briefly.
    Falls back to whatever is already on the run object.
    """
    import time

    run_id = _get(run, "id", "run_id")
    immediate = extract_run_usd(run)
    if not run_id or not os.environ.get("APIFY_TOKEN"):
        return immediate
    best = immediate
    try:
        from apify_client import ApifyClient

        client = ApifyClient(os.environ["APIFY_TOKEN"])
        for i in range(max(1, attempts)):
            fresh = client.run(run_id).get()
            billed = extract_run_usd(fresh)
            if billed > best:
                best = billed
            if billed > 0:
                return billed
            if i + 1 < attempts:
                time.sleep(delay_s)
        return best
    except Exception:
        return best

def record_apify_run_billed(run: Any) -> float:
    """Record billed Apify USD onto the active CostTracker (refetching when needed)."""
    from agent.utils.cost_tracker import get_cost_tracker

    usd = refetch_run_usd(run)
    tracker = get_cost_tracker()
    if tracker and usd:
        tracker.add_usage(apify_usd=usd)
    return usd


def reconcile_apify_window(
    *,
    start: datetime,
    end: Optional[datetime] = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Sum Apify usageTotalUsd for all account runs in [start, end]."""
    from apify_client import ApifyClient

    end = end or datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    client = ApifyClient(os.environ["APIFY_TOKEN"])
    by_actor: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "usd": 0.0, "run_ids": []}
    )
    actor_cache: dict[str, str] = {}
    total = 0.0
    count = 0

    for r in client.runs().list(limit=limit, desc=True).items:
        started = _get(r, "startedAt", "started_at")
        if not started:
            continue
        started_dt = (
            datetime.fromisoformat(started.replace("Z", "+00:00"))
            if isinstance(started, str)
            else started
        )
        if started_dt < start or started_dt > end:
            continue
        count += 1
        act = _get(r, "actId", "act_id", default="unknown")
        usd = float(_get(r, "usageTotalUsd", "usage_total_usd", default=0) or 0)
        total += usd
        if act not in actor_cache:
            try:
                info = client.actor(act).get() or {}
                if not isinstance(info, dict):
                    info = {
                        "username": getattr(info, "username", None),
                        "name": getattr(info, "name", None),
                    }
                uname = info.get("username") or ""
                name = info.get("name") or act
                actor_cache[act] = f"{uname}/{name}".strip("/")
            except Exception:
                actor_cache[act] = str(act)
        name = actor_cache[act]
        by_actor[name]["runs"] += 1
        by_actor[name]["usd"] += usd
        rid = _get(r, "id")
        if rid and len(by_actor[name]["run_ids"]) < 5:
            by_actor[name]["run_ids"].append(rid)

    return {
        "window_utc": {"start": start.isoformat(), "end": end.isoformat()},
        "runs": count,
        "total_usd": round(total, 4),
        "by_actor": [
            {
                "actor": name,
                "runs": d["runs"],
                "usd": round(d["usd"], 4),
                "sample_run_ids": d["run_ids"],
            }
            for name, d in sorted(by_actor.items(), key=lambda x: -x[1]["usd"])
        ],
        "source": "Apify API usageTotalUsd (billed)",
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
    }


def persist_stage_cost_report(
    *,
    campaign_dir: Path,
    client_id: str,
    run_id: str,
    phase: str,
    started_at: datetime,
    tracker_summary: Optional[dict[str, Any]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> Path:
    """
    Write cost_log_{run_id}.json with CostTracker totals + Apify API reconcile.
    Flying blind without the reconcile block is a hard failure mode.
    """
    ended = datetime.now(timezone.utc)
    apify_api = {}
    if os.environ.get("APIFY_TOKEN"):
        apify_api = reconcile_apify_window(start=started_at, end=ended)

    tracker_apify = float(
        ((tracker_summary or {}).get("run_totals") or {}).get("apify_usd") or 0
    )
    api_apify = float(apify_api.get("total_usd") or 0)
    drift = round(api_apify - tracker_apify, 4)

    report = {
        "schema_version": "3",
        "campaign_dir": str(campaign_dir),
        "client_id": client_id,
        "run_id": run_id,
        "phase": phase,
        "started_at_utc": started_at.isoformat(),
        "ended_at_utc": ended.isoformat(),
        "cost_tracker": tracker_summary,
        "apify_api_reconcile": apify_api,
        "apify_drift_usd": drift,
        "apify_truth_usd": api_apify,
        "warning": (
            f"CostTracker apify=${tracker_apify:.4f} vs Apify API ${api_apify:.4f} "
            f"(drift ${drift:.4f}). Prefer apify_truth_usd for billing."
            if abs(drift) >= 0.01
            else None
        ),
        "extra": extra or {},
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }

    campaign_dir.mkdir(parents=True, exist_ok=True)
    out = campaign_dir / f"cost_log_{run_id}.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    client_dir = Path("data") / "clients" / client_id / "runs"
    client_dir.mkdir(parents=True, exist_ok=True)
    (client_dir / f"{run_id}_cost_log.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    return out
