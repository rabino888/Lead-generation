"""
Stage 1 — Curated seed import.

Converts company+website seeds to RawCompany records. ICP matching runs upstream
via match_campaign_icp.py — this stage does not call Apollo company search.
"""
from __future__ import annotations

import logging
from typing import Optional

from agent.models import ApolloSignals, ClientProfile, CompanySeed, ICPProfile, LeadSource, RawCompany, RunRequest
from agent.utils.logger import get_run_logger


def run(
    request: RunRequest,
    client_profile: ClientProfile,
    run_id: str,
    logger: Optional[logging.Logger] = None,
) -> list[RawCompany]:
    log = logger or get_run_logger(run_id, client_profile.client_id)
    log.info("Stage 1 — Curated seed import")

    icp = request.icp or client_profile.icp
    seeds = list(request.company_seeds or [])
    if request.company_seeds_csv:
        from agent.utils.seeds import load_company_seeds_csv

        csv_seeds = load_company_seeds_csv(request.company_seeds_csv)
        log.info(
            "Loaded %d seeds from CSV %s (+%d inline)",
            len(csv_seeds),
            request.company_seeds_csv,
            len(seeds),
        )
        seeds.extend(csv_seeds)

    return _from_curated_seeds(seeds, icp, log)


def _from_curated_seeds(
    seeds: list[CompanySeed],
    icp: ICPProfile,
    log: logging.Logger,
) -> list[RawCompany]:
    log.info("Importing %d curated company seeds", len(seeds))
    results: list[RawCompany] = []
    seen_domains: set[str] = set()
    seen_names: set[str] = set()

    for seed in seeds:
        name = seed.company_name.strip()
        if not name:
            continue

        website = _clean_seed_url(seed.website)
        domain_key = _extract_domain(website) if website else ""
        name_key = name.lower()
        dedupe_key = domain_key or name_key

        if dedupe_key in seen_domains or name_key in seen_names:
            log.info("Curated seed skipped duplicate: %s (%s)", name, website or "no website")
            continue

        seen_domains.add(dedupe_key)
        seen_names.add(name_key)
        results.append(
            RawCompany(
                company_name=name,
                website=website,
                industry=seed.industry or icp.industry,
                location=seed.location or icp.location,
                company_size=seed.company_size,
                company_linkedin_url=seed.company_linkedin_url,
                lead_source=LeadSource.MANUAL,
                apollo_signals=ApolloSignals(),
            )
        )

    log.info("Curated seed import — %d/%d unique companies", len(results), len(seeds))
    return results


def _clean_seed_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    if "." in cleaned and not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"
    return cleaned


def _extract_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
        return (parsed.netloc or parsed.path).replace("www.", "").lower().strip("/")
    except Exception:
        return url.lower()
