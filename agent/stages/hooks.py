"""
Stage 6 — Hook Generation (Claude)
Writes personalised outreach hooks per lead.
Batches multiple leads per Claude call to save tokens.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from agent.integrations.llm import call_llm
from agent.models import ClientProfile, Lead
from agent.utils.logger import get_run_logger

_BATCH_SIZE = 5  # Leads per Claude call


def run(
    leads: list[Lead],
    client_profile: ClientProfile,
    run_id: str,
    logger: Optional[logging.Logger] = None,
) -> list[Lead]:
    """
    Writes personalised_hook and recommended_first_service into each lead.
    Returns the updated leads list.
    """
    log = logger or get_run_logger(run_id, client_profile.client_id)
    log.info("Stage 6 — Hook generation (%d leads)", len(leads))

    for i in range(0, len(leads), _BATCH_SIZE):
        batch = leads[i: i + _BATCH_SIZE]
        _generate_hooks_batch(batch, client_profile, log)

    log.info("Stage 6 complete — outreach hooks written")
    return leads


def _generate_hooks_batch(
    batch: list[Lead],
    client_profile: ClientProfile,
    log: logging.Logger,
) -> None:
    """Generate hooks for a batch of leads in one Claude call."""

    leads_data = []
    for i, lead in enumerate(batch):
        entry = {
            "index": i,
            "company_name": lead.company_name,
            "industry": lead.industry,
            "decision_maker_name": lead.decision_maker.name if lead.decision_maker else None,
            "decision_maker_title": lead.decision_maker.title if lead.decision_maker else None,
            "pain_points": lead.pain_points,
            "opportunities": lead.opportunities,
            "website_summary": lead.website_analysis.website_summary if lead.website_analysis else None,
            "lead_score": lead.lead_score,
        }
        leads_data.append(entry)

    client_name = client_profile.name
    industry_context = client_profile.icp.industry or "business services"

    prompt = f"""You are a world-class B2B sales copywriter writing personalised cold outreach openers.

SENDER: {client_name}
CONTEXT: They help {industry_context} companies grow through [automation, AI, marketing, etc.]

For each lead below, write:
1. A "personalized_hook" — 2-3 sentences that:
   - Reference something SPECIFIC about their company (pain point, tech gap, or growth signal)
   - Are conversational, not salesy
   - Feel like the sender did their homework, not like a template
   - End with an implicit reason why THIS company needs what the sender offers

2. A "recommended_first_service" — the single most relevant service to lead with, based on their pain points

LEADS:
{json.dumps(leads_data, indent=2)}

Return ONLY a JSON array (same order as input):
[
  {{
    "index": 0,
    "personalized_hook": "...",
    "recommended_first_service": "..."
  }},
  ...
]
"""

    try:
        results = call_llm(prompt, max_tokens=1500, expect_json=True)
        if not isinstance(results, list):
            raise ValueError("Expected list")

        hook_map = {r["index"]: r for r in results if "index" in r}
        for i, lead in enumerate(batch):
            entry = hook_map.get(i, {})
            lead.personalized_hook = entry.get("personalized_hook", "")
            lead.recommended_first_service = entry.get("recommended_first_service", "")
            log.debug("Hook generated for '%s'", lead.company_name)

    except Exception as e:
        log.warning("Hook generation batch failed: %s", e)
        for lead in batch:
            lead.personalized_hook = ""
            lead.recommended_first_service = ""
