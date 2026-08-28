"""
Stage 4 — Contact Enrichment (Apollo)
Finds and reveals contactable decision-makers before expensive enrichment.
Only companies with a revealed email become deliverable leads.
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Optional

from agent.integrations.apify import (
    _default_max_posts,
    _skip_linkedin_jobs,
    _skip_linkedin_posts,
    enrich_company_linkedin,
    enrich_decision_maker_linkedin,
    get_linkedin_company_jobs_batch,
)
from agent.integrations.apollo import enrich_contacts, get_company_linkedin_url
from agent.utils.apollo_contact_gate import apollo_contactable
from agent.models import (
    ClientProfile, EnrichmentStatus, InputMode, Lead,
    RawCompany, WebsiteAnalysis,
)
from agent.utils.concurrency import env_int, map_ordered
from agent.utils.cost_tracker import get_cost_tracker
from agent.utils.logger import get_run_logger


def run(
    companies: list[RawCompany],
    client_profile: ClientProfile,
    input_mode: InputMode,
    run_id: str,
    logger: Optional[logging.Logger] = None,
    website_analyses: Optional[dict[str, Optional[WebsiteAnalysis]]] = None,
) -> tuple[list[Lead], list[Lead]]:
    """
    Returns (qualified_leads, rejected_leads).
    Qualified = Apollo revealed email (+ DM LinkedIn URL when required).
    Rejected  = no contactable decision-maker; kept for logs/counts only.
    """
    log = logger or get_run_logger(run_id, client_profile.client_id)
    concurrency = env_int("APOLLO_CONTACT_CONCURRENCY", default=6)
    log.info(
        "Stage 4 — Contact enrichment (%d companies, concurrency=%d)",
        len(companies),
        concurrency,
    )

    job_titles = client_profile.icp.job_titles
    contact_locations = client_profile.icp.contact_locations
    if contact_locations:
        log.info("Contact location filter active: %s", ", ".join(contact_locations))
    wa_map = website_analyses or {}

    def _process(company: RawCompany) -> tuple[Lead, bool]:
        decision_maker, generic_email = enrich_contacts(
            company_name=company.company_name,
            website=company.website,
            job_titles=job_titles,
            contact_locations=contact_locations,
        )
        company_linkedin_url = company.company_linkedin_url
        if not company_linkedin_url and company.website:
            company_linkedin_url = get_company_linkedin_url(
                company.company_name,
                company.website,
            )
        lead = Lead(
            lead_id=f"lead_{uuid.uuid4().hex[:10]}",
            run_id=run_id,
            client_id=client_profile.client_id,
            input_mode=input_mode,
            apollo_org_id=company.apollo_org_id,
            company_name=company.company_name,
            website=company.website,
            industry=company.industry,
            location=company.location,
            company_size=company.company_size,
            founded_year=company.founded_year,
            company_linkedin_url=company_linkedin_url,
            lead_source=company.lead_source,
            apollo_signals=company.apollo_signals,
            decision_maker=decision_maker,
            company_generic_email=generic_email,
            website_analysis=wa_map.get(company.company_name),
        )
        contactable, reject_reason = apollo_contactable(decision_maker)
        if contactable:
            lead.enrichment_status = EnrichmentStatus.COMPLETE
            tracker = get_cost_tracker()
            if tracker:
                tracker.begin_lead(lead.lead_id, lead.company_name)
        else:
            lead.enrichment_status = EnrichmentStatus.PARTIAL
            lead.partial_reason = reject_reason
        return lead, contactable

    processed = map_ordered(
        companies,
        _process,
        concurrency=concurrency,
        label="Apollo contact enrichment",
    )

    qualified: list[Lead] = []
    partial: list[Lead] = []
    for lead, contactable in processed:
        if contactable and lead.decision_maker:
            qualified.append(lead)
            log.info("Qualified: %s (%s)", lead.company_name, lead.decision_maker.email)
        else:
            partial.append(lead)
            reason = lead.partial_reason or "not contactable"
            log.info("Rejected: %s — %s", lead.company_name, reason)

    log.info(
        "Stage 4 complete — %d contactable, %d rejected",
        len(qualified), len(partial),
    )
    return qualified, partial


def enrich_linkedin_for_leads(
    leads: list[Lead],
    client_profile: ClientProfile,
    run_id: str,
    logger: Optional[logging.Logger] = None,
) -> list[Lead]:
    """Fetch LinkedIn profile, posts, and company hiring signals for contactable leads."""
    log = logger or get_run_logger(run_id, client_profile.client_id)
    if not leads:
        return leads

    skip_posts = _skip_linkedin_posts()
    concurrency = env_int("LINKEDIN_POST_CONCURRENCY", default=4)
    company_concurrency = env_int("COMPANY_LINKEDIN_CONCURRENCY", default=6)
    company_cache: dict[str, dict] = {}
    company_cache_lock = threading.Lock()

    def _company_key(lead: Lead) -> str:
        from agent.integrations.apollo import _extract_domain
        if lead.website:
            domain = _extract_domain(lead.website)
            if domain:
                return domain
        return lead.company_name.lower().strip()

    unique_leads: dict[str, Lead] = {}
    for lead in leads:
        unique_leads.setdefault(_company_key(lead), lead)

    # Optional batched company open-jobs (off by default — APIFY_SKIP_LINKEDIN_JOBS=1)
    jobs_by_url: dict[str, dict] = {}
    if not _skip_linkedin_jobs():
        from agent.utils.company_batch import batch_for_workflow

        batch_items = [
            {
                "company_name": lead.company_name,
                "website": lead.website,
                "company_linkedin": lead.company_linkedin_url,
            }
            for lead in unique_leads.values()
        ]
        batches = batch_for_workflow(
            batch_items,
            "linkedin_company_jobs",
            workflow_step="apollo_contactable",
            max_batch_size=100,
        )
        for batch in batches:
            urls = batch.linkedin_company_urls()
            if urls:
                jobs_by_url.update(get_linkedin_company_jobs_batch(urls))

    def _fetch_company(lead: Lead) -> None:
        """Enrich one unique company; runs in the pool, one lead per key."""
        key = _company_key(lead)
        tracker = get_cost_tracker()
        if tracker:
            with tracker.lead_context(lead.lead_id, lead.company_name):
                data = enrich_company_linkedin(
                    lead.company_name,
                    website=lead.website,
                    company_linkedin_url=lead.company_linkedin_url,
                    fetch_open_jobs=not jobs_by_url,
                )
        else:
            data = enrich_company_linkedin(
                lead.company_name,
                website=lead.website,
                company_linkedin_url=lead.company_linkedin_url,
                fetch_open_jobs=not jobs_by_url,
            )
        if jobs_by_url and data.get("linkedin_url"):
            li = data["linkedin_url"].split("?")[0].rstrip("/")
            if li.endswith("/jobs"):
                li = li[:-len("/jobs")]
            jobs = jobs_by_url.get(li) or {}
            if jobs.get("job_titles") or jobs.get("open_jobs_count"):
                data["is_hiring"] = jobs.get("is_hiring")
                data["open_jobs_count"] = jobs.get("open_jobs_count", 0)
                data["open_jobs_summary"] = jobs.get("summary")
        with company_cache_lock:
            company_cache[key] = data

    def _apply_company(lead: Lead) -> None:
        data = company_cache.get(_company_key(lead)) or {}
        if data.get("linkedin_url"):
            lead.company_linkedin_url = data["linkedin_url"]
        if data.get("description"):
            lead.company_linkedin_description = data["description"]
        if data.get("employee_count") and not lead.company_size:
            lead.company_size = str(data["employee_count"])
        if data.get("industry") and not lead.industry:
            lead.industry = data["industry"]
        lead.company_is_hiring = data.get("is_hiring")
        lead.company_open_jobs_count = data.get("open_jobs_count")
        lead.company_open_jobs_summary = data.get("open_jobs_summary")

    map_ordered(
        list(unique_leads.values()),
        _fetch_company,
        concurrency=company_concurrency,
        label="Company LinkedIn enrichment",
    )
    for lead in leads:
        _apply_company(lead)

    with_company_url = sum(1 for lead in leads if lead.company_linkedin_url)
    log.info(
        "Company LinkedIn resolved for %d/%d leads%s",
        with_company_url,
        len(leads),
        " (posts skipped)" if skip_posts else "",
    )

    targets = [lead for lead in leads if lead.decision_maker and lead.decision_maker.linkedin_url]
    if not targets:
        log.info("LinkedIn person enrichment skipped — no LinkedIn URLs on contactable leads")
        return leads

    log.info(
        "LinkedIn person enrichment (%d leads, concurrency=%d%s)",
        len(targets),
        concurrency,
        ", posts skipped" if skip_posts else "",
    )

    def _enrich_one(lead: Lead) -> Lead:
        dm = lead.decision_maker
        if not dm or not dm.linkedin_url:
            return lead
        tracker = get_cost_tracker()
        if tracker and tracker.should_halt():
            log.warning(
                "Cost cap breached — skipping LinkedIn person enrichment for '%s'",
                lead.company_name,
            )
            return lead

        def _fetch() -> None:
            linkedin_context = enrich_decision_maker_linkedin(
                dm.linkedin_url,
                max_posts=_default_max_posts(),
            )
            dm.linkedin_posts = linkedin_context.get("posts") or []
            dm.linkedin_interests = linkedin_context.get("interests") or []
            dm.linkedin_post_summary = linkedin_context.get("post_summary")
            dm.linkedin_about = linkedin_context.get("about")
            dm.linkedin_headline = linkedin_context.get("headline")
            dm.is_hiring = linkedin_context.get("is_hiring")

        if tracker:
            with tracker.lead_context(lead.lead_id, lead.company_name):
                _fetch()
                record = tracker.accumulate_lead_cost(lead.lead_id, lead.company_name, checkpoint="post-linkedin")
                lead.enrichment_cost_usd = record["total_usd"]
        else:
            _fetch()
        return lead

    map_ordered(targets, _enrich_one, concurrency=concurrency, label="LinkedIn enrichment")
    enriched = sum(1 for lead in targets if lead.decision_maker and lead.decision_maker.linkedin_posts)
    with_about = sum(1 for lead in targets if lead.decision_maker and lead.decision_maker.linkedin_about)
    if skip_posts:
        log.info(
            "LinkedIn person enrichment complete — %d/%d with profile about (posts skipped)",
            with_about,
            len(targets),
        )
    else:
        log.info(
            "LinkedIn enrichment complete — %d/%d with posts, %d/%d with about",
            enriched, len(targets), with_about, len(targets),
        )
    return leads
