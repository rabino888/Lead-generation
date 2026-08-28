"""
Build a curated company seed list for a campaign.

DEPRECATED for new campaigns — Apollo company search is ~10–50× more expensive than
enrichment and returns noisy results. Prefer web-researched seeds.csv (see
docs/OPERATOR-WORKFLOW.md Phase 2). This script is retained for legacy campaigns only.

Reads campaign config from data/campaigns/{campaign_id}/campaign.json.
Sources (in priority order):
  1. Parsed Clutch directory cache (optional agent-tools/*.txt)
  2. curated_supplement.json in the campaign folder
  3. Apollo company search — keywords per geo segment (AVOID for new campaigns)

Usage:
  python scripts/build_seed_list.py --campaign {campaign_id}
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.utils.campaign_config import (
    CLUTCH_BLOCK_RE,
    CampaignConfig,
    compile_excluded_name_re,
    load_campaign,
)
from agent.utils.icp_seed_quality import is_strong_icp_seed

DEFAULT_CLUTCH_CACHE_GLOB = str(
    Path.home() / ".cursor" / "projects" / "c-Users-ravi-TBO-agents-Lead-generation" / "agent-tools" / "*.txt"
)


@dataclass
class Seed:
    seed_id: str
    company_name: str
    website: str
    location: str
    geo_segment: str
    source_url: str
    status: str = "pending"
    notes: str = ""


def _root_domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _normalize_website(url: str) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = f"https://{url}"
    parsed = urlparse(url)
    if not parsed.netloc:
        return ""
    return f"https://{parsed.netloc.lower()}"


def _website_from_clutch_redirect(redirect_url: str) -> str:
    qs = parse_qs(urlparse(redirect_url).query)
    return _normalize_website(qs.get("u", [""])[0])


def _is_excluded(campaign: CampaignConfig, name: str, website: str, industry: str = "") -> bool:
    pattern = compile_excluded_name_re(campaign)
    if pattern.search(name) or pattern.search(industry):
        return True
    domain = _root_domain(website)
    if domain in campaign.discovery.merged_excluded_domains():
        return True
    for suffix in campaign.discovery.excluded_website_suffixes:
        s = suffix.lower().removeprefix(".")
        if domain.endswith(s):
            return True
    industry_lower = (industry or "").lower()
    for blocked in campaign.discovery.excluded_industries:
        if blocked.lower() in industry_lower:
            return True
    return False


def _matches_name_signal(campaign: CampaignConfig, name: str, industry: str) -> bool:
    """Campaign-defined name-signal filter (industry-agnostic; regex from campaign.json)."""
    signal_re = campaign.discovery.name_signal_re()
    if signal_re is None:
        return True
    return bool(signal_re.search(name) or signal_re.search(industry or ""))


def _parse_clutch_cache(campaign: CampaignConfig) -> list[Seed]:
    cache_glob = campaign.discovery.clutch_cache_glob
    if not cache_glob:
        return []
    seeds: list[Seed] = []
    for path in glob.glob(cache_glob):
        html = Path(path).read_text(encoding="utf-8", errors="ignore")
        segment = campaign.infer_segment_from_text(html)
        if not segment:
            continue
        for match in CLUTCH_BLOCK_RE.finditer(html):
            name = match.group(1).strip()
            slug = match.group(2).strip()
            website = _website_from_clutch_redirect(match.group(3))
            if not website or _is_excluded(campaign, name, website):
                continue
            seg_cfg = campaign.segments.get(segment)
            location = segment
            if seg_cfg and seg_cfg.country_codes:
                location = ", ".join(seg_cfg.country_codes.keys())
            seeds.append(
                Seed(
                    seed_id="",
                    company_name=name,
                    website=website,
                    location=location,
                    geo_segment=segment,
                    source_url=f"https://clutch.co/profile/{slug}",
                    notes="Clutch directory cache",
                )
            )
    return seeds


def _apollo_country_codes(campaign: CampaignConfig, location: str) -> list[str]:
    codes = campaign.all_country_codes()
    code = codes.get(location)
    return [code] if code else []


def _apollo_search(
    client: httpx.Client,
    api_key: str,
    campaign: CampaignConfig,
    location: str,
    keyword: str,
    page: int,
) -> list[dict]:
    disc = campaign.discovery
    payload: dict = {
        "per_page": 100,
        "page": page,
        "q_keywords": keyword,
        "organization_industries": disc.apollo_industries,
        "organization_num_employees_ranges": disc.apollo_employee_ranges,
        "q_not_keywords": disc.apollo_not_keywords,
    }
    codes = _apollo_country_codes(campaign, location)
    if codes:
        payload["organization_country_codes"] = codes
        payload["organization_locations"] = [location]

    resp = client.post(
        "https://api.apollo.io/api/v1/mixed_companies/search",
        json=payload,
        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
        timeout=45,
    )
    resp.raise_for_status()
    return resp.json().get("organizations", []) or []


def _apollo_seeds(campaign: CampaignConfig, api_key: str) -> tuple[list[Seed], int]:
    """Returns (seeds, apollo_search_api_calls). Company search is free on paper but
    Apollo bills export/API credits per search request on many plans."""
    seeds: list[Seed] = []
    api_calls = 0
    max_pages = max(1, int(os.environ.get("APOLLO_SEED_MAX_PAGES", "4")))
    pool_target = campaign.seed_target * 3  # stop paging once we have enough raw candidates

    with httpx.Client() as client:
        for segment_name in campaign.segment_order:
            seg = campaign.segments.get(segment_name)
            if not seg:
                continue
            for query in seg.apollo_queries:
                if len(seeds) >= pool_target:
                    break
                for page in range(1, max_pages + 1):
                    try:
                        orgs = _apollo_search(
                            client, api_key, campaign, query.location, query.keyword, page,
                        )
                    except Exception as exc:
                        print(f"WARN Apollo {query.location} p{page}: {exc}", file=sys.stderr)
                        break
                    api_calls += 1
                    if not orgs:
                        break
                    for org in orgs:
                        website = _normalize_website(
                            org.get("website_url") or org.get("primary_domain") or ""
                        )
                        name = (org.get("name") or "").strip()
                        industry = (org.get("industry") or "").strip()
                        if not website or not name:
                            continue
                        if _is_excluded(campaign, name, website, industry):
                            continue
                        if not is_strong_icp_seed(name, website, industry, enabled=False):
                            continue
                        if not _matches_name_signal(campaign, name, industry):
                            continue
                        loc = org.get("city") or org.get("state") or query.location
                        country = org.get("country") or query.location
                        full_loc = f"{loc}, {country}" if loc and country else (country or query.location)
                        seeds.append(
                            Seed(
                                seed_id="",
                                company_name=name,
                                website=website,
                                location=full_loc,
                                geo_segment=segment_name,
                                source_url=f"apollo://org/{org.get('id', '')}",
                                notes=f"Apollo search: {query.keyword} | {query.location}",
                            )
                        )
                    if len(seeds) >= pool_target:
                        break
    return seeds, api_calls


def _load_supplement(campaign: CampaignConfig) -> list[Seed]:
    path = campaign.curated_supplement_path
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    default_segment = campaign.segment_order[0] if campaign.segment_order else ""
    seeds: list[Seed] = []
    for row in data:
        website = _normalize_website(row.get("website", ""))
        name = (row.get("company_name") or "").strip()
        if not website or not name or _is_excluded(campaign, name, website):
            continue
        if not is_strong_icp_seed(name, website, enabled=False):
            continue
        seeds.append(
            Seed(
                seed_id="",
                company_name=name,
                website=website,
                location=row.get("location", row.get("geo_segment", default_segment)),
                geo_segment=row.get("geo_segment", default_segment),
                source_url=row.get("source_url", "curated://manual"),
                notes=row.get("notes", "Curated supplement"),
            )
        )
    return seeds


def _dedupe(seeds: list[Seed]) -> list[Seed]:
    seen: set[str] = set()
    out: list[Seed] = []
    for seed in seeds:
        domain = _root_domain(seed.website)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        out.append(seed)
    return out


def _fill_quotas(campaign: CampaignConfig, seeds: list[Seed]) -> list[Seed]:
    segment_targets = campaign.segment_targets()
    def _seed_priority(seed: Seed) -> int:
        src = seed.source_url or ""
        if src.startswith("curated://") or src.startswith("curated_"):
            return 0
        if src.startswith("https://clutch.co"):
            return 1
        return 2

    clutch_first = sorted(
        seeds,
        key=lambda s: (_seed_priority(s), s.company_name.lower()),
    )
    by_segment: dict[str, list[Seed]] = {k: [] for k in segment_targets}
    for seed in clutch_first:
        by_segment.setdefault(seed.geo_segment, []).append(seed)

    picked: list[Seed] = []
    used: set[str] = set()

    for segment, target in segment_targets.items():
        count = 0
        for seed in by_segment.get(segment, []):
            domain = _root_domain(seed.website)
            if domain in used:
                continue
            picked.append(seed)
            used.add(domain)
            count += 1
            if count >= target:
                break

    if len(picked) < campaign.seed_target:
        for seed in clutch_first:
            domain = _root_domain(seed.website)
            if domain in used:
                continue
            picked.append(seed)
            used.add(domain)
            if len(picked) >= campaign.seed_target:
                break

    prefix = campaign.seed_id_prefix
    for i, seed in enumerate(picked[: campaign.seed_target], start=1):
        seed.seed_id = f"{prefix}-{i:04d}"
    return picked[: campaign.seed_target]


def build(campaign: CampaignConfig) -> list[Seed]:
    load_dotenv(ROOT / ".env", override=True)
    api_key = os.environ.get("APOLLO_API_KEY", "")
    if not api_key:
        raise RuntimeError("APOLLO_API_KEY required")

    clutch = _parse_clutch_cache(campaign)
    supplement = _load_supplement(campaign)
    apollo, api_calls = _apollo_seeds(campaign, api_key)
    combined = _dedupe(clutch + supplement + apollo)
    print(f"Apollo company search API calls: {api_calls} (max_pages={os.environ.get('APOLLO_SEED_MAX_PAGES', '4')})")
    return _fill_quotas(campaign, combined)


def write_outputs(campaign: CampaignConfig, seeds: list[Seed]) -> None:
    out_dir = campaign.campaign_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = [
        "seed_id", "company_name", "website", "location", "geo_segment",
        "source_url", "status", "notes",
    ]
    csv_path = campaign.seeds_csv
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for seed in seeds:
            w.writerow(asdict(seed))

    (out_dir / "seeds.json").write_text(
        json.dumps([asdict(s) for s in seeds], indent=2), encoding="utf-8"
    )
    by_seg: dict[str, list] = {}
    for seed in seeds:
        by_seg.setdefault(seed.geo_segment, []).append(asdict(seed))
    (out_dir / "seeds_by_segment.json").write_text(
        json.dumps(by_seg, indent=2), encoding="utf-8"
    )

    print(f"Campaign: {campaign.campaign_id} ({campaign.display_name})")
    print(f"Wrote {len(seeds)} seeds -> {csv_path}")
    for seg, rows in by_seg.items():
        print(f"  {seg}: {len(rows)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build curated seed list for a campaign")
    parser.add_argument(
        "--campaign", required=True,
        help="Campaign id (folder under data/campaigns/)",
    )
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)
    seeds = build(campaign)
    if len(seeds) < campaign.seed_target:
        print(
            f"WARNING: only {len(seeds)} seeds (target {campaign.seed_target})",
            file=sys.stderr,
        )
    write_outputs(campaign, seeds)


if __name__ == "__main__":
    main()
