"""
Batch smoke test: gtgyani206 company open jobs for many LinkedIn company URLs.

Use before re-enabling APIFY_SKIP_LINKEDIN_JOBS=0 — records billed USD per URL
and total for a cost gate decision.

Usage:
  python scripts/smoke_company_jobs_batch.py --campaign automata_us_rnd
  python scripts/smoke_company_jobs_batch.py --csv path/to/urls.csv --max-companies 53
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)


def _normalize_company_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if "/jobs" in url:
        url = url.split("/jobs")[0]
    return url


def _load_urls_from_campaign(campaign_id: str, limit: int) -> list[str]:
    stages = ROOT / "data" / "campaigns" / campaign_id / "stages"
    path = stages / "04_apollo_contactable.csv"
    if not path.exists():
        path = stages / "05_enriched.csv"
    if not path.exists():
        raise FileNotFoundError(f"No 04/05 stage CSV under {stages}")
    urls: list[str] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            raw = (row.get("company_linkedin") or row.get("company_linkedin_url") or "").strip()
            if raw and "linkedin.com/company/" in raw.lower():
                urls.append(_normalize_company_url(raw))
            if limit and len(urls) >= limit:
                break
    return list(dict.fromkeys(urls))


def _load_urls_from_csv(csv_path: Path, limit: int) -> list[str]:
    urls: list[str] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            for key in ("company_linkedin", "company_linkedin_url", "url", "linkedin"):
                raw = (row.get(key) or "").strip()
                if raw and "linkedin.com/company/" in raw.lower():
                    urls.append(_normalize_company_url(raw))
                    break
            if limit and len(urls) >= limit:
                break
    return list(dict.fromkeys(urls))


def run_batch(company_urls: list[str], max_jobs: int) -> dict:
    from apify_client import ApifyClient

    client = ApifyClient(os.environ["APIFY_TOKEN"])
    run_input = {
        "companyUrls": company_urls,
        "maxJobs": max_jobs,
        "runProfile": "fast",
        "scrapeDescription": False,
    }
    actor = "gtgyani206/linkedin-company-scraper"
    print(f"Actor: {actor}")
    print(f"Batch size: {len(company_urls)} company URLs")
    run = client.actor(actor).call(run_input=run_input)
    from agent.utils.apify_spend import refetch_run_usd

    usd = refetch_run_usd(run)
    ds = run["defaultDatasetId"] if isinstance(run, dict) else run.default_dataset_id
    items = list(client.dataset(ds).iterate_items())
    titles_by_company: dict[str, list[str]] = {}
    for item in items:
        company = (
            item.get("companyName")
            or item.get("company")
            or item.get("companyUrl")
            or "unknown"
        )
        title = (item.get("title") or item.get("jobTitle") or "").strip()
        if title:
            titles_by_company.setdefault(str(company), []).append(title)
    per_url = round(usd / max(len(company_urls), 1), 4)
    return {
        "actor": actor,
        "company_urls": len(company_urls),
        "usd_total": round(usd, 4),
        "usd_per_company_url": per_url,
        "item_count": len(items),
        "companies_with_titles": len(titles_by_company),
        "run_id": run.get("id") if isinstance(run, dict) else getattr(run, "id", None),
        "sample_titles": {
            k: v[:5] for k, v in list(titles_by_company.items())[:5]
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch smoke LinkedIn company jobs (gtgyani)")
    parser.add_argument("--campaign", default="")
    parser.add_argument("--csv", default="", help="CSV with company_linkedin column")
    parser.add_argument("--max-companies", type=int, default=100)
    parser.add_argument("--max-jobs", type=int, default=5)
    parser.add_argument("--out", default="", help="JSON output path")
    args = parser.parse_args()

    if not os.environ.get("APIFY_TOKEN"):
        print("ERROR: APIFY_TOKEN missing", file=sys.stderr)
        return 2

    if args.csv:
        urls = _load_urls_from_csv(Path(args.csv), args.max_companies)
    elif args.campaign:
        urls = _load_urls_from_campaign(args.campaign, args.max_companies)
    else:
        print("ERROR: pass --campaign or --csv", file=sys.stderr)
        return 2

    if not urls:
        print("ERROR: no company LinkedIn URLs found", file=sys.stderr)
        return 2

    result = run_batch(urls, args.max_jobs)
    print(json.dumps(result, indent=2))
    gate = float(os.environ.get("APIFY_JOBS_BATCH_USD_GATE", "2.0"))
    if result["usd_total"] <= gate:
        print(f"PASS cost gate: ${result['usd_total']:.4f} <= ${gate:.2f} for {len(urls)} URLs")
    else:
        print(
            f"FAIL cost gate: ${result['usd_total']:.4f} > ${gate:.2f} "
            f"(${result['usd_per_company_url']:.4f}/URL) — keep APIFY_SKIP_LINKEDIN_JOBS=1"
        )

    out = Path(args.out or "")
    if not out and args.campaign:
        out = ROOT / "data" / "campaigns" / args.campaign / "smoke_company_jobs_batch.json"
    if out:
        out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
