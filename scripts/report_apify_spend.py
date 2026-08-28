"""One-off: Apify spend last 7 days by actor."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)

from apify_client import ApifyClient


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


def main() -> None:
    client = ApifyClient(os.environ["APIFY_TOKEN"])
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)

    by_actor: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "usd": 0.0, "succeeded": 0, "failed": 0, "examples": []}
    )
    total = 0.0
    in_window = 0
    actor_cache: dict[str, str] = {}
    daily: dict[str, float] = defaultdict(float)

    for r in client.runs().list(limit=500, desc=True).items:
        started = _get(r, "startedAt", "started_at")
        if not started:
            continue
        started_dt = (
            datetime.fromisoformat(started.replace("Z", "+00:00"))
            if isinstance(started, str)
            else started
        )
        if started_dt < start:
            continue
        in_window += 1
        act = _get(r, "actId", "act_id", default="unknown")
        usd = float(_get(r, "usageTotalUsd", "usage_total_usd", default=0) or 0)
        status = _get(r, "status") or ""
        total += usd
        day = started_dt.strftime("%Y-%m-%d")
        daily[day] += usd

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
        by_actor[name]["count"] += 1
        by_actor[name]["usd"] += usd
        if status == "SUCCEEDED":
            by_actor[name]["succeeded"] += 1
        else:
            by_actor[name]["failed"] += 1
        if len(by_actor[name]["examples"]) < 2:
            by_actor[name]["examples"].append(
                {
                    "id": _get(r, "id"),
                    "started": started_dt.isoformat(),
                    "usd": round(usd, 4),
                    "status": status,
                }
            )

    report = {
        "window_utc": {"start": start.isoformat(), "end": end.isoformat()},
        "runs": in_window,
        "total_usd": round(total, 4),
        "by_day": {k: round(v, 4) for k, v in sorted(daily.items())},
        "by_actor": [
            {
                "actor": name,
                "runs": d["count"],
                "succeeded": d["succeeded"],
                "failed": d["failed"],
                "usd": round(d["usd"], 4),
                "examples": d["examples"],
            }
            for name, d in sorted(by_actor.items(), key=lambda x: -x[1]["usd"])
        ],
    }
    out = ROOT / "data" / "campaigns" / "automata_us_rnd" / "apify_spend_7d.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
