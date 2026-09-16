"""
Deterministic pipeline orchestrator.

Curated seeds (ICP already applied upstream) → Apollo contacts → website enrich
→ keyword score → report. No LLM company prefilter or LLM qualify.

Stage CLIs (`scripts/stage_*.py`) are the primary operator entry; this module is
used by `run_deterministic_campaign.py`, smoke tests, and `POST /run`.
"""
from __future__ import annotations

import traceback

from agent.integrations.apollo import get_credits_used, reset_credits_used
from agent.integrations.firecrawl import get_pages_crawled, reset_pages_crawled
from agent.integrations.llm import get_tokens_used, reset_tokens_used
from agent.models import ClientProfile, InputMode, RunRequest, RunStatus
from agent.stages import apollo_csv, contacts, discovery, report, website
from agent.utils import run_tracker
from agent.utils.cost_tracker import (
    CostCapBreached,
    get_cost_tracker,
    init_cost_tracker,
    persist_cost_diagnostic,
    reset_cost_tracker,
)
from agent.utils.deduplication import (
    contact_key,
    filter_seen_contacts,
    filter_seen_leads,
    mark_contacts_as_delivered,
    mark_leads_as_delivered,
)
from agent.utils.keyword_score import score_leads
from agent.utils.logger import get_run_logger


async def run_pipeline(
    run_id: str,
    request: RunRequest,
    client_profile: ClientProfile,
) -> None:
    """
    Full async deterministic pipeline. Updates run_tracker state throughout.
  """
    log = get_run_logger(
        run_id,
        client_profile.client_id,
        keyword=(run_tracker.get_run(run_id).keyword if run_tracker.get_run(run_id) else None),
    )
    log.info("=" * 60)
    log.info("PIPELINE START — run_id: %s | client: %s", run_id, client_profile.client_id)
    log.info("Mode: %s | max_leads: %d", request.mode.value, request.max_leads)
    log.info("=" * 60)

    reset_pages_crawled()
    reset_credits_used()
    reset_tokens_used()
    reset_cost_tracker()
    init_cost_tracker(run_id, client_profile.client_id)

    run_tracker.update_run(run_id, status=RunStatus.RUNNING)

    all_qualified: list = []
    all_rejected: list = []

    try:
        if request.mode == InputMode.APOLLO_CSV:
            await _run_apollo_csv_pipeline(run_id, request, client_profile, log)
            return

        if request.mode != InputMode.CURATED_SEEDS:
            run_tracker.fail_run(
                run_id,
                f"Unsupported mode '{request.mode.value}'. Use curated_seeds or stage CLIs.",
            )
            return

        run_tracker.update_run(run_id, current_stage="seed_import")
        log.info("── Stage 1: Curated seed import ──")
        companies = discovery.run(request, client_profile, run_id, log)

        if not companies:
            log.warning("No companies found — ending pipeline early")
            run_tracker.fail_run(run_id, "No companies in curated seed list")
            return

        companies = filter_seen_leads(client_profile.client_id, companies, log)
        if not companies:
            log.warning("All seed companies already delivered to this client")
            run_tracker.fail_run(run_id, "No new companies found — all previously delivered")
            return

        run_tracker.update_run(run_id, current_stage="icp_passthrough")
        log.info("── Stage 2: ICP passthrough (rules applied upstream) ──")
        filtered_companies = companies

        initial_window_size = max(request.max_leads * 5, 20)
        processing_window = filtered_companies[:initial_window_size]
        log.info("Processing window: %d companies", len(processing_window))

        run_tracker.update_run(run_id, current_stage="contact_enrichment")
        log.info("── Stage 3: Contact enrichment (Apollo) ──")

        qualified_batch, rejected_batch = contacts.run(
            companies=processing_window,
            client_profile=client_profile,
            input_mode=request.mode,
            run_id=run_id,
            logger=log,
        )
        all_qualified.extend(qualified_batch)
        all_rejected.extend(rejected_batch)

        remaining = filtered_companies[initial_window_size:]
        batch_size = 20
        MAX_EXTRA_BATCHES = 2
        extra_batches_done = 0

        while (
            len(all_qualified) < request.max_leads
            and remaining
            and extra_batches_done < MAX_EXTRA_BATCHES
        ):
            extra_batches_done += 1
            extra_batch = remaining[:batch_size]
            remaining = remaining[batch_size:]

            log.info(
                "Need more qualified leads (%d/%d) — processing %d more companies (batch %d/%d)",
                len(all_qualified),
                request.max_leads,
                len(extra_batch),
                extra_batches_done,
                MAX_EXTRA_BATCHES,
            )

            extra_qualified, extra_rejected = contacts.run(
                companies=extra_batch,
                client_profile=client_profile,
                input_mode=request.mode,
                run_id=run_id,
                logger=log,
            )
            all_qualified.extend(extra_qualified)
            all_rejected.extend(extra_rejected)

        all_qualified = _dedupe_leads_by_contact(all_qualified, log)
        all_qualified = filter_seen_contacts(client_profile.client_id, all_qualified, log)

        all_qualified = contacts.enrich_linkedin_for_leads(
            all_qualified, client_profile, run_id, log
        )
        _raise_if_breached()

        all_qualified = all_qualified[:request.max_leads]
        run_tracker.update_run(
            run_id,
            qualified_count=len(all_qualified),
            partial_count=len(all_rejected),
        )

        log.info(
            "After contact enrichment: %d contactable, %d rejected",
            len(all_qualified),
            len(all_rejected),
        )

        if not all_qualified:
            log.warning("No contactable leads found — ending before website enrichment")
            run_tracker.fail_run(
                run_id,
                "No contactable leads — Apollo did not reveal email + LinkedIn URL",
            )
            return

        run_tracker.update_run(run_id, current_stage="website_enrichment")
        log.info("── Stage 4: Website enrichment ──")
        website_analyses = website.run(all_qualified, client_profile, run_id, log)
        _attach_website_analyses(all_qualified, website_analyses)
        _raise_if_breached()

        run_tracker.update_run(run_id, current_stage="keyword_score")
        log.info("── Stage 5: Keyword-overlap score ──")
        all_qualified = score_leads(
            all_qualified,
            client_profile.sender,
            hiring_first=client_profile.icp.website_analysis_mode == "hiring_first",
        )
        _raise_if_breached()

        run_tracker.update_run(run_id, current_stage="report")
        log.info("── Stage 6: Report ──")

        state = run_tracker.get_run(run_id)
        sheet_url, csv_path, recommendations = report.run(
            qualified_leads=all_qualified,
            partial_leads=[],
            client_profile=client_profile,
            run_state=state,
            sheet_name=request.output_sheet_name,
            logger=log,
        )

        mark_leads_as_delivered(client_profile.client_id, all_qualified, log)
        mark_contacts_as_delivered(client_profile.client_id, all_qualified, log)

        _complete_pipeline(
            run_id=run_id,
            log=log,
            sheet_url=sheet_url,
            csv_path=csv_path,
            recommendations=recommendations,
            qualified_count=len(all_qualified),
            partial_count=len(all_rejected),
        )

    except CostCapBreached as e:
        _handle_cost_cap_breach(
            run_id=run_id,
            client_profile=client_profile,
            exc=e,
            log=log,
            partial_leads=all_qualified,
            partial_rejected=all_rejected,
            sheet_name=request.output_sheet_name,
        )
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        log.error("PIPELINE FAILED: %s\n%s", error_msg, traceback.format_exc())
        run_tracker.fail_run(run_id, error_msg)


def _raise_if_breached() -> None:
    tracker = get_cost_tracker()
    if tracker:
        tracker.raise_if_breached()


def _attach_website_analyses(leads: list, website_analyses: dict) -> None:
    for lead in leads:
        lead.website_analysis = website_analyses.get(lead.company_name)


def _dedupe_leads_by_contact(leads: list, log) -> list:
    deduped: list = []
    seen: dict[str, object] = {}
    duplicates = 0

    for lead in leads:
        key = contact_key(lead)
        if not key:
            deduped.append(lead)
            continue
        if key in seen:
            duplicates += 1
            kept = seen[key]
            log.info(
                "Duplicate contact skipped: %s (%s) duplicates %s",
                lead.company_name,
                key,
                getattr(kept, "company_name", "earlier lead"),
            )
            continue
        seen[key] = lead
        deduped.append(lead)

    if duplicates:
        log.info("Contact deduplication removed %d duplicate lead(s)", duplicates)
    return deduped


def _complete_pipeline(
    *,
    run_id: str,
    log,
    sheet_url,
    csv_path,
    recommendations,
    qualified_count: int,
    partial_count: int,
) -> None:
    tracker = get_cost_tracker()
    total_cost = None
    if tracker:
        total_cost = tracker.run_summary()["run_totals"]["total_usd"]

    run_tracker.complete_run(
        run_id=run_id,
        sheet_url=sheet_url,
        csv_path=csv_path,
        recommendations=recommendations,
        qualified_count=qualified_count,
        partial_count=partial_count,
        apollo_credits=get_credits_used(),
        firecrawl_pages=get_pages_crawled(),
        llm_tokens=get_tokens_used(),
        total_cost_usd=total_cost,
    )

    log.info("=" * 60)
    log.info("PIPELINE COMPLETE")
    log.info("  Qualified leads: %d", qualified_count)
    log.info("  Rejected leads:  %d", partial_count)
    log.info("  Apollo credits:  %d", get_credits_used())
    log.info("  Firecrawl pages: %d", get_pages_crawled())
    log.info("  LLM tokens:      %d", get_tokens_used())
    if total_cost is not None:
        log.info("  Total cost USD:  $%.4f", total_cost)
    log.info("  Sheet: %s", sheet_url)
    log.info("  CSV:   %s", csv_path)
    log.info("=" * 60)


def _handle_cost_cap_breach(
    *,
    run_id: str,
    client_profile: ClientProfile,
    exc: CostCapBreached,
    log,
    partial_leads: list,
    partial_rejected: list,
    sheet_name: str | None,
) -> None:
    diagnostic = exc.diagnostic
    diag_path = persist_cost_diagnostic(diagnostic)

    log.error("=" * 60)
    log.error("COST CAP BREACHED — pipeline halted")
    log.error("  %s", diagnostic.get("message"))
    log.error(
        "  Trigger: %s ($%.4f)",
        diagnostic["trigger_lead"]["company_name"],
        diagnostic["trigger_lead"]["total_usd"],
    )
    log.error("  Diagnostic: %s", diag_path)
    for rec in diagnostic.get("recommendations", []):
        log.error("  → %s", rec)
    log.error("=" * 60)

    sheet_url = None
    csv_path = None
    if partial_leads:
        run_tracker.update_run(run_id, current_stage="report_partial")
        state = run_tracker.get_run(run_id)
        try:
            sheet_url, csv_path, _ = report.run(
                qualified_leads=partial_leads,
                partial_leads=partial_rejected,
                client_profile=client_profile,
                run_state=state,
                sheet_name=sheet_name,
                logger=log,
            )
        except Exception as report_err:
            log.error("Partial report failed after cost cap: %s", report_err)

    run_tracker.fail_run(
        run_id,
        diagnostic.get("message", "Lead cost cap breached"),
        cost_cap_triggered=True,
        cost_diagnostic_path=diag_path,
        total_cost_usd=diagnostic.get("run_totals", {}).get("total_usd"),
        apollo_credits_used=get_credits_used(),
        firecrawl_pages_crawled=get_pages_crawled(),
        llm_tokens_used=get_tokens_used(),
        qualified_count=len(partial_leads),
        partial_count=len(partial_rejected),
        sheet_url=sheet_url,
        csv_path=csv_path,
    )


async def _run_apollo_csv_pipeline(
    run_id: str,
    request: RunRequest,
    client_profile: ClientProfile,
    log,
) -> None:
    """Enrich + score leads from a manual Apollo CSV export (no company search)."""
    leads: list = []
    try:
        run_tracker.update_run(run_id, current_stage="apollo_csv_import")
        log.info("── Stage 1: Apollo CSV import ──")
        leads = apollo_csv.run(request.apollo_csv_path or "", client_profile, run_id, log)

        if not leads:
            run_tracker.fail_run(run_id, "Apollo CSV contained no rows with an email address")
            return

        leads = filter_seen_leads(client_profile.client_id, leads, log)
        leads = _dedupe_leads_by_contact(leads, log)
        leads = filter_seen_contacts(client_profile.client_id, leads, log)
        leads = leads[:request.max_leads]
        if not leads:
            run_tracker.fail_run(run_id, "No new CSV leads found — all previously delivered")
            return

        tracker = get_cost_tracker()
        if tracker:
            for lead in leads:
                tracker.begin_lead(lead.lead_id, lead.company_name)

        leads = contacts.enrich_linkedin_for_leads(leads, client_profile, run_id, log)
        _raise_if_breached()

        run_tracker.update_run(run_id, current_stage="website_enrichment")
        log.info("── Stage 2: Website enrichment ──")
        website_analyses = website.run(leads, client_profile, run_id, log)
        _attach_website_analyses(leads, website_analyses)
        _raise_if_breached()

        run_tracker.update_run(run_id, current_stage="keyword_score")
        log.info("── Stage 3: Keyword-overlap score ──")
        leads = score_leads(
            leads,
            client_profile.sender,
            hiring_first=client_profile.icp.website_analysis_mode == "hiring_first",
        )
        _raise_if_breached()

        run_tracker.update_run(run_id, current_stage="report")
        log.info("── Stage 4: Report ──")
        state = run_tracker.get_run(run_id)
        sheet_url, csv_path, recommendations = report.run(
            qualified_leads=leads,
            partial_leads=[],
            client_profile=client_profile,
            run_state=state,
            sheet_name=request.output_sheet_name,
            logger=log,
        )

        mark_leads_as_delivered(client_profile.client_id, leads, log)
        mark_contacts_as_delivered(client_profile.client_id, leads, log)
        _complete_pipeline(
            run_id=run_id,
            log=log,
            sheet_url=sheet_url,
            csv_path=csv_path,
            recommendations=recommendations,
            qualified_count=len(leads),
            partial_count=0,
        )
    except CostCapBreached as e:
        _handle_cost_cap_breach(
            run_id=run_id,
            client_profile=client_profile,
            exc=e,
            log=log,
            partial_leads=leads,
            partial_rejected=[],
            sheet_name=request.output_sheet_name,
        )
