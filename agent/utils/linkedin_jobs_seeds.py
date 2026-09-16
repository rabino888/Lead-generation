"""
LinkedIn jobs search -> company seed extraction (Apify).

Shared by scripts/build_linkedin_jobs_seeds.py and agent/utils/seed_sources.py.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse

from agent.utils.icp_seed_quality import weak_seed_reason

DEFAULT_ACTOR = "curious_coder/linkedin-jobs-scraper"
DEFAULT_GEO_SEGMENT = "default"


@dataclass
class LinkedInJobSeed:
    seed_id: str
    company_name: str
    website: str
    location: str
    geo_segment: str
    source_url: str
    status: str = "pending"
    notes: str = ""
    industry: str = ""
    company_size: str = ""
    company_linkedin_url: str = ""


def root_domain(url: str) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = f"https://{url}"
    return urlparse(url).netloc.lower().removeprefix("www.")


def normalize_website(url: str, *, follow_redirects: bool = True) -> str:
    if not url:
        return ""
    url = url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    parsed = urlparse(url)
    if not parsed.netloc:
        return ""
    cleaned = f"https://{parsed.netloc.lower()}"
    if not follow_redirects:
        return cleaned
    try:
        import httpx

        with httpx.Client(follow_redirects=True, timeout=5.0) as client:
            r = client.head(cleaned)
            if r.status_code >= 400:
                r = client.get(cleaned)
            final = str(r.url)
            p2 = urlparse(final)
            if p2.netloc:
                return f"https://{p2.netloc.lower()}"
    except Exception:
        pass
    return cleaned


def normalize_linkedin_jobs_url(url: str) -> str:
    """Strip tracking params; map /jobs/search-results/ to /jobs/search/."""
    url = url.strip()
    parsed = urlparse(url)
    path = parsed.path.replace("/jobs/search-results", "/jobs/search")
    if not path.endswith("/") and "search" in path:
        path = path.rstrip("/") + "/"

    keep_keys = {"keywords", "geoId", "location", "f_C", "f_E", "f_JT", "f_TPR", "f_WT", "sortBy"}
    qs = parse_qs(parsed.query)
    filtered = {k: v for k, v in qs.items() if k in keep_keys and v}
    query_parts = [f"{key}={filtered[key][0]}" for key in sorted(filtered)]
    query = "&".join(query_parts)

    return urlunparse((parsed.scheme or "https", parsed.netloc or "www.linkedin.com", path, "", query, ""))


def load_linkedin_jobs_urls(
    *,
    urls: list[str] | None = None,
    url_file: str | Path | None = None,
) -> list[str]:
    collected: list[str] = []
    if urls:
        collected.extend(urls)
    if url_file:
        text = Path(url_file).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                collected.append(line)
    if not collected:
        raise ValueError("Provide at least one LinkedIn jobs search URL (--url or --url-file)")
    return [normalize_linkedin_jobs_url(u) for u in collected]


def scrape_linkedin_jobs(urls: list[str], count: int, actor_id: str) -> list[dict]:
    from apify_client import ApifyClient

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN required")

    client = ApifyClient(token)
    run_input = {
        "urls": urls,
        "scrapeCompany": True,
        "count": count,
        "splitByLocation": False,
    }
    run = client.actor(actor_id).call(run_input=run_input)
    dataset_id = run.default_dataset_id if hasattr(run, "default_dataset_id") else run.get("defaultDatasetId")
    return list(client.dataset(dataset_id).iterate_items())


def employee_bucket(count: int | float | str | None) -> str | None:
    if count is None or count == "":
        return None
    try:
        n = int(float(count))
    except (TypeError, ValueError):
        return str(count)
    if n <= 10:
        return "1-10"
    if n <= 50:
        return "11-50"
    if n <= 200:
        return "51-200"
    if n <= 500:
        return "201-500"
    if n <= 1000:
        return "501-1000"
    return "1000+"


def jobs_to_seeds(
    jobs: list[dict],
    geo_segment: str,
    require_website: bool,
) -> tuple[list[LinkedInJobSeed], list[dict]]:
    """Dedupe by root domain; keep the first job row per company."""
    seeds: list[LinkedInJobSeed] = []
    dropped: list[dict] = []
    seen_domains: set[str] = set()
    seen_names: set[str] = set()

    for job in jobs:
        name = (job.get("companyName") or job.get("company") or "").strip()
        website = normalize_website(job.get("companyWebsite") or job.get("website") or "")
        domain = root_domain(website)
        linkedin = (job.get("companyLinkedinUrl") or job.get("companyUrl") or "").split("?")[0].rstrip("/")
        location = (job.get("location") or job.get("jobLocation") or "").strip()
        job_title = (job.get("title") or job.get("jobTitle") or "").strip()
        job_link = (job.get("link") or job.get("url") or job.get("jobUrl") or "").strip()
        employees = job.get("companyEmployeesCount") or job.get("employeesCount")
        industries = job.get("industries") or job.get("companyIndustry") or ""
        if isinstance(industries, list):
            industries = ", ".join(str(x) for x in industries if x)

        if not name:
            dropped.append({"reason": "missing_company_name", "job": job})
            continue
        if require_website and not website:
            dropped.append({"reason": "missing_website", "company_name": name, "job_link": job_link})
            continue
        if domain and domain in seen_domains:
            continue
        name_key = re.sub(r"\s+", " ", name.lower())
        if not domain and name_key in seen_names:
            continue

        weak = weak_seed_reason(
            name, website or name, str(industries), enabled=False
        )
        if weak:
            dropped.append({"reason": weak, "company_name": name, "website": website})
            continue

        if domain:
            seen_domains.add(domain)
        seen_names.add(name_key)

        notes_parts = ["LinkedIn jobs scrape"]
        if job_title:
            notes_parts.append(f"hiring: {job_title}")
        if employees is not None:
            notes_parts.append(f"employees: {employees}")
        if industries:
            notes_parts.append(f"industry: {industries}")

        size_bucket = employee_bucket(employees) if employees is not None else ""

        seeds.append(
            LinkedInJobSeed(
                seed_id="",
                company_name=name,
                website=website,
                location=location,
                geo_segment=geo_segment,
                source_url=job_link or linkedin or "",
                notes=" | ".join(notes_parts),
                industry=str(industries or ""),
                company_size=str(employees) if employees is not None else (size_bucket or ""),
                company_linkedin_url=linkedin or "",
            )
        )

    return seeds, dropped


def prefer_linkedin_company_websites(seeds: list[LinkedInJobSeed]) -> list[LinkedInJobSeed]:
    """
    Overwrite jobs ``companyWebsite`` with the LinkedIn *company profile* website.

    Jobs-card websites are often a global .com; the company About page usually
    has the local domain (e.g. .es). Dedupes by company LinkedIn URL.
    """
    url_to_indices: dict[str, list[int]] = {}
    for i, seed in enumerate(seeds):
        li = (seed.company_linkedin_url or "").strip()
        if not li:
            continue
        url_to_indices.setdefault(li, []).append(i)
    if not url_to_indices:
        return seeds

    from agent.integrations.apify import resolve_websites_from_linkedin_company_urls

    resolved = resolve_websites_from_linkedin_company_urls(
        {url: url for url in url_to_indices}
    )
    for url, site in resolved.items():
        if not site:
            continue
        cleaned = normalize_website(site, follow_redirects=False) or site
        for idx in url_to_indices.get(url, []):
            old = seeds[idx].website
            seeds[idx].website = cleaned
            if old and old != cleaned:
                note = (seeds[idx].notes or "").strip()
                tag = f"website from LI company (was {old})"
                seeds[idx].notes = f"{note} | {tag}".strip(" |") if note else tag
    return seeds


def assign_seed_ids(seeds: list[LinkedInJobSeed], prefix: str) -> list[LinkedInJobSeed]:
    for i, seed in enumerate(seeds, start=1):
        seed.seed_id = f"{prefix}-{i:04d}"
    return seeds


def seeds_to_rows(seeds: list[LinkedInJobSeed]) -> list[dict[str, str]]:
    return [asdict(seed) for seed in seeds]
