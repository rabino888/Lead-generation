"""
Stage 2 — Pre-filter (Claude, cheap + fast)
Batch-scores companies against the client ICP before expensive Firecrawl scraping.
Drops companies below the threshold score. Saves Firecrawl credits.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from agent.integrations.llm import call_llm
from agent.models import ClientProfile, ICPProfile, RawCompany
from agent.utils.logger import get_run_logger

_BATCH_SIZE = 20  # Number of companies per Claude call


def run(
    companies: list[RawCompany],
    client_profile: ClientProfile,
    run_id: str,
    logger: Optional[logging.Logger] = None,
) -> list[RawCompany]:
    """
    Returns filtered list of companies that pass the ICP threshold.
    """
    log = logger or get_run_logger(run_id, client_profile.client_id)
    log.info("Stage 2 — Pre-filter (%d companies, threshold: %d)",
             len(companies), client_profile.icp.prefilter_threshold)

    if not companies:
        return []

    scored: list[tuple[RawCompany, int]] = []

    # Process in batches to avoid token limits
    for i in range(0, len(companies), _BATCH_SIZE):
        batch = companies[i: i + _BATCH_SIZE]
        batch_scores = _score_batch(batch, client_profile.icp, log)
        for company, score in zip(batch, batch_scores):
            company.prefilter_score = score
            scored.append((company, score))

    threshold = client_profile.icp.prefilter_threshold
    passed = [(c, s) for c, s in scored if s >= threshold]
    filtered = [(c, s) for c, s in scored if s < threshold]

    log.info(
        "Pre-filter: %d passed (score >= %d), %d dropped",
        len(passed), threshold, len(filtered)
    )
    if filtered:
        log.debug(
            "Dropped companies: %s",
            ", ".join(f"{c.company_name}({s})" for c, s in filtered[:10])
        )

    # Sort by score descending — best leads processed first
    passed.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in passed]


def _score_batch(
    batch: list[RawCompany],
    icp: ICPProfile,
    log: logging.Logger,
) -> list[int]:
    """
    Score a batch of companies against the ICP in a single Claude call.
    Returns a list of integer scores (0-100) in the same order as the input batch.
    """
    companies_json = []
    for i, c in enumerate(batch):
        entry = {
            "index": i,
            "company_name": c.company_name,
            "industry": c.industry,
            "location": c.location,
            "company_size": c.company_size,
            "website": c.website,
        }
        # Include Apollo signals if available (premium plan)
        if c.apollo_signals.intent_topics:
            entry["intent_topics"] = c.apollo_signals.intent_topics
        if c.apollo_signals.intent_strength:
            entry["intent_strength"] = c.apollo_signals.intent_strength
        if c.apollo_signals.technologies_used:
            entry["technologies"] = c.apollo_signals.technologies_used
        if c.apollo_signals.funding_round:
            entry["funding"] = c.apollo_signals.funding_round
        if c.apollo_signals.hiring_signals:
            entry["hiring"] = c.apollo_signals.hiring_signals
        companies_json.append(entry)

    icp_desc = _describe_icp(icp)

    prompt = f"""You are a B2B lead qualification expert.

TARGET CLIENT PROFILE:
{icp_desc}

Score each company below (0-100) on how well it matches the target profile.
- 80-100: Excellent match — high priority
- 60-79:  Good match — worth pursuing
- 40-59:  Moderate match — borderline
- 0-39:   Poor match — skip

Consider: industry fit, location, company size, buying signals, and any intent/technology data.

COMPANIES TO SCORE:
{json.dumps(companies_json, indent=2)}

Respond ONLY with a JSON array of objects, one per company, in the same order:
[{{"index": 0, "score": 75, "reason": "brief reason"}}, ...]
"""

    try:
        results = call_llm(prompt, max_tokens=1000, expect_json=True)
        if not isinstance(results, list):
            raise ValueError("Expected a list")

        # Map index → score, handle missing entries gracefully
        score_map = {r["index"]: r["score"] for r in results if "index" in r and "score" in r}
        return [int(score_map.get(i, 50)) for i in range(len(batch))]

    except Exception as e:
        log.warning("Pre-filter batch scoring failed: %s — using default score 50", e)
        return [50] * len(batch)


def _describe_icp(icp: ICPProfile) -> str:
    parts = []
    if icp.industry:
        parts.append(f"Industry: {icp.industry}")
    if icp.location:
        parts.append(f"Location: {icp.location}")
    if icp.company_size_min or icp.company_size_max:
        size = f"{icp.company_size_min or '?'}–{icp.company_size_max or '?'} employees"
        parts.append(f"Company size: {size}")
    if icp.job_titles:
        parts.append(f"Target titles: {', '.join(icp.job_titles)}")
    if icp.keywords:
        parts.append(f"Must relate to: {', '.join(icp.keywords)}")
    if icp.excluded_keywords:
        parts.append(f"Exclude if related to: {', '.join(icp.excluded_keywords)}")
    return "\n".join(parts) if parts else "General B2B companies"
