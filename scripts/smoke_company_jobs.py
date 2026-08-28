"""
Manual smoke test: company LinkedIn open jobs for one company.

Default: Confido — proves /company/{slug}/jobs/ listing works via gtgyani206
(afanasenko default is known expensive + empty results).

Usage:
  python scripts/smoke_company_jobs.py
  python scripts/smoke_company_jobs.py --company-url https://www.linkedin.com/company/confidotech
  python scripts/smoke_company_jobs.py --actor afanasenko/linkedin-jobs-scraper
"""
from __future__ import annotations

import argparse
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


def _jobs_page_url(company_url: str) -> str:
    return f"{_normalize_company_url(company_url)}/jobs/"


def run_gtgyani(company_url: str, max_jobs: int) -> dict:
    from apify_client import ApifyClient

    client = ApifyClient(os.environ["APIFY_TOKEN"])
    company_url = _normalize_company_url(company_url)
    run_input = {
        "companyUrls": [company_url],
        "maxJobs": max_jobs,
        "runProfile": "fast",
        "scrapeDescription": False,
    }
    print(f"Actor: gtgyani206/linkedin-company-scraper")
    print(f"Input companyUrls: {company_url}")
    print(f"Expected jobs page: {_jobs_page_url(company_url)}")
    run = client.actor("gtgyani206/linkedin-company-scraper").call(run_input=run_input)
    usd = float(getattr(run, "usage_total_usd", None) or run.get("usageTotalUsd") or 0)
    items = list(client.dataset(run["defaultDatasetId"] if isinstance(run, dict) else run.default_dataset_id).iterate_items())
    titles = []
    for item in items:
        t = (item.get("title") or item.get("jobTitle") or "").strip()
        if t:
            titles.append(t)
    return {
        "actor": "gtgyani206/linkedin-company-scraper",
        "usd": usd,
        "item_count": len(items),
        "titles": titles[:20],
        "run_id": run.get("id") if isinstance(run, dict) else getattr(run, "id", None),
        "sample": items[0] if items else None,
    }


def run_afanasenko(company_url: str, max_jobs: int) -> dict:
    from apify_client import ApifyClient

    client = ApifyClient(os.environ["APIFY_TOKEN"])
    company_url = _normalize_company_url(company_url)
    run_input = {
        "operationMode": "searchCompanyJobs",
        "searchCompanyJobsCompanyUrls": [company_url],
        "maxItemsMode3": max_jobs,
    }
    print(f"Actor: afanasenko/linkedin-jobs-scraper")
    print(f"Input searchCompanyJobsCompanyUrls: {company_url}")
    run = client.actor("afanasenko/linkedin-jobs-scraper").call(run_input=run_input)
    usd = float(getattr(run, "usage_total_usd", None) or run.get("usageTotalUsd") or 0)
    ds = run["defaultDatasetId"] if isinstance(run, dict) else run.default_dataset_id
    items = list(client.dataset(ds).iterate_items())
    titles = []
    for item in items:
        t = (item.get("title") or item.get("jobTitle") or item.get("position") or "").strip()
        if t:
            titles.append(t)
    return {
        "actor": "afanasenko/linkedin-jobs-scraper",
        "usd": usd,
        "item_count": len(items),
        "titles": titles[:20],
        "run_id": run.get("id") if isinstance(run, dict) else getattr(run, "id", None),
        "sample": items[0] if items else None,
    }


def run_curious_coder_company_search(company_url: str, max_jobs: int) -> dict:
    """curious_coder wants jobs *search* URLs, not /company/.../jobs/."""
    from apify_client import ApifyClient

    client = ApifyClient(os.environ["APIFY_TOKEN"])
    # Public guest search filtered by company name keyword (no company ID needed).
    # Better long-term: resolve LinkedIn company ID and use f_C=.
    name = _normalize_company_url(company_url).rstrip("/").split("/")[-1]
    search_url = (
        "https://www.linkedin.com/jobs/search/"
        f"?keywords=&location=United%20States&f_C=&geoId=103644278"
        # fallback: keyword company slug in search
    )
    # Prefer company jobs listing via keyword + company page is NOT supported;
    # use slug in keywords as a cheap smoke only.
    search_url = (
        "https://www.linkedin.com/jobs/search/"
        f"?keywords={name}&location=United%20States&geoId=103644278&f_TPR=r604800"
    )
    run_input = {"urls": [search_url], "limitPerSource": max_jobs, "scrapeCompany": False}
    print(f"Actor: curious_coder/linkedin-jobs-scraper")
    print(f"Input search URL: {search_url}")
    print("NOTE: curious_coder is a search-results scraper, not a /company/.../jobs/ scraper.")
    run = client.actor("curious_coder/linkedin-jobs-scraper").call(run_input=run_input)
    usd = float(getattr(run, "usage_total_usd", None) or run.get("usageTotalUsd") or 0)
    ds = run["defaultDatasetId"] if isinstance(run, dict) else run.default_dataset_id
    items = list(client.dataset(ds).iterate_items())
    titles = [(item.get("title") or "", item.get("companyName") or "") for item in items]
    return {
        "actor": "curious_coder/linkedin-jobs-scraper",
        "usd": usd,
        "item_count": len(items),
        "titles": [f"{t} @ {c}" for t, c in titles[:20]],
        "run_id": run.get("id") if isinstance(run, dict) else getattr(run, "id", None),
        "sample": items[0] if items else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test LinkedIn company jobs actors")
    parser.add_argument(
        "--company-url",
        default="https://www.linkedin.com/company/confidotech",
        help="LinkedIn company page (not a /jobs/view/ URL)",
    )
    parser.add_argument(
        "--actor",
        choices=["gtgyani", "afanasenko", "curious_coder", "all"],
        default="gtgyani",
        help="Which actor to test (default: gtgyani — company jobs page scraper)",
    )
    parser.add_argument("--max-jobs", type=int, default=5)
    args = parser.parse_args()

    if not os.environ.get("APIFY_TOKEN"):
        print("ERROR: APIFY_TOKEN missing", file=sys.stderr)
        return 2

    runners = {
        "gtgyani": run_gtgyani,
        "afanasenko": run_afanasenko,
        "curious_coder": run_curious_coder_company_search,
    }
    keys = list(runners) if args.actor == "all" else [args.actor]
    results = []
    for key in keys:
        print("=" * 60)
        result = runners[key](args.company_url, args.max_jobs)
        results.append(result)
        print(json.dumps({k: v for k, v in result.items() if k != "sample"}, indent=2))
        if result.get("sample"):
            print("sample keys:", list(result["sample"].keys())[:25])

    out = ROOT / "data" / "campaigns" / "automata_us_rnd" / "smoke_company_jobs.json"
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
