"""
Refine LinkedIn-jobs seed lists by employee count (and optional filters).

Reads data/campaigns/{campaign}/seeds.json + linkedin_jobs_raw.json,
writes filtered seeds back to seeds.csv/json and archives the prior list.

Usage:
  python scripts/refine_linkedin_jobs_seeds.py --campaign automata --max-employees 100
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _domain(url: str) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = f"https://{url}"
    return urlparse(url).netloc.lower().removeprefix("www.")


def _employee_index(jobs: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    by_domain: dict[str, int] = {}
    by_name: dict[str, int] = {}
    for job in jobs:
        raw = job.get("companyEmployeesCount")
        if raw is None:
            continue
        try:
            count = int(raw)
        except (TypeError, ValueError):
            continue
        domain = _domain(job.get("companyWebsite") or "")
        name = (job.get("companyName") or "").strip().lower()
        if domain:
            prev = by_domain.get(domain)
            if prev is None or count < prev:
                by_domain[domain] = count
        if name:
            prev = by_name.get(name)
            if prev is None or count < prev:
                by_name[name] = count
    return by_domain, by_name


def _employee_count(
    seed: dict,
    by_domain: dict[str, int],
    by_name: dict[str, int],
) -> int | None:
    match = re.search(r"employees:\s*(\d+)", seed.get("notes") or "")
    if match:
        return int(match.group(1))
    domain = _domain(seed.get("website") or "")
    if domain and domain in by_domain:
        return by_domain[domain]
    name = (seed.get("company_name") or "").strip().lower()
    if name in by_name:
        return by_name[name]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Refine LinkedIn jobs seeds by employee count")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--max-employees", type=int, required=True)
    parser.add_argument(
        "--include-unknown",
        action="store_true",
        help="Keep seeds with no employee count (default: drop them)",
    )
    args = parser.parse_args()

    campaign_dir = ROOT / "data" / "campaigns" / args.campaign
    seeds_path = campaign_dir / "seeds.json"
    raw_path = campaign_dir / "linkedin_jobs_raw.json"
    if not seeds_path.exists():
        raise SystemExit(f"Missing {seeds_path}")

    seeds: list[dict] = json.loads(seeds_path.read_text(encoding="utf-8"))
    jobs: list[dict] = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else []
    by_domain, by_name = _employee_index(jobs)

    kept: list[dict] = []
    dropped: list[dict] = []
    for seed in seeds:
        count = _employee_count(seed, by_domain, by_name)
        if count is not None and count < args.max_employees:
            seed = dict(seed)
            seed["company_size"] = str(count)
            kept.append(seed)
        elif count is None and args.include_unknown:
            kept.append(dict(seed))
        else:
            reason = "unknown_employee_count" if count is None else f"employees_{count}_gte_{args.max_employees}"
            dropped.append({**seed, "drop_reason": reason, "employee_count": count})

    prefix = args.campaign
    for i, seed in enumerate(kept, start=1):
        seed["seed_id"] = f"{prefix}-{i:04d}"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive = campaign_dir / f"seeds_before_refine_{stamp}.json"
    shutil.copy2(seeds_path, archive)

    fields = [
        "seed_id", "company_name", "website", "location", "geo_segment",
        "source_url", "status", "notes",
    ]
    csv_path = campaign_dir / "seeds.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for seed in kept:
            w.writerow(seed)

    seeds_path.write_text(json.dumps(kept, indent=2), encoding="utf-8")
    by_seg: dict[str, list] = {}
    for seed in kept:
        by_seg.setdefault(seed.get("geo_segment", "default"), []).append(seed)
    (campaign_dir / "seeds_by_segment.json").write_text(json.dumps(by_seg, indent=2), encoding="utf-8")
    (campaign_dir / "seeds_refine_dropped.json").write_text(json.dumps(dropped, indent=2), encoding="utf-8")

    meta = {
        "campaign_id": args.campaign,
        "max_employees": args.max_employees,
        "include_unknown": args.include_unknown,
        "seeds_before": len(seeds),
        "seeds_after": len(kept),
        "dropped": len(dropped),
        "archived_to": archive.name,
    }
    (campaign_dir / "seeds_refine_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Refined {args.campaign}: {len(seeds)} -> {len(kept)} seeds (< {args.max_employees} employees)")
    print(f"Dropped: {len(dropped)} (seeds_refine_dropped.json)")
    print(f"Archived prior list: {archive.name}")
    for seg, rows in by_seg.items():
        print(f"  {seg}: {len(rows)}")


if __name__ == "__main__":
    main()
