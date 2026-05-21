"""
Pipeline Orchestrator
Runs all 7 stages end-to-end, updates run state after each stage,
and handles partial failures gracefully.
"""
from __future__ import annotations

import traceback
from typing import Optional

from agent.integrations.apollo import get_credits_used
from agent.integrations.firecrawl import get_pages_crawled
from agent.integrations.llm import get_tokens_used
from agent.models import ClientProfile, RunRequest, RunStatus
from agent.stages import contacts, discovery, hooks, prefilter, qualify, report, website
from agent.utils import run_tracker
from agent.utils.logger import get_run_logger


async def run_pipeline(
    run_id: str,
    request: RunRequest,
    client_profile: ClientProfile,
) -> None:
    """
    Full async pipeline. Updates run_tracker state throughout.
    This runs as a FastAPI background task — errors are caught and logged.
    """
    log = get_run_logger(run_id, client_profile.client_id)
    log.info("=" * 60)
    log.info("PIPELINE START — run_id: %s | client: %s", run_id, client_profile.client_id)
    log.info("Mode: %s | max_leads: %d", request.mode.value, request.max_leads)
    log.info("=" * 60)

    run_tracker.update_run(run_id, status=RunStatus.RUNNING)

    try:
        # ── Stage 1: Discovery ────────────────────────────────────────────────
        run_tracker.update_run(run_id, current_stage="discovery")
        log.info("── Stage 1: Discovery ──")
        companies = discovery.run(request, client_profile, run_id, log)

        if not companies:
            log.warning("No companies found — ending pipeline early")
            run_tracker.fail_run(run_id, "No companies discovered")
            return

        # ── Stage 2: Pre-filter ───────────────────────────────────────────────
        run_tracker.update_run(run_id, current_stage="prefilter")
        log.info("── Stage 2: Pre-filter ──")
        filtered_companies = prefilter.run(companies, client_profile, run_id, log)

        if not filtered_companies:
            log.warning("All companies filtered out — try relaxing ICP threshold")
            run_tracker.fail_run(run_id, "All companies filtered below ICP threshold")
            return

        # Limit to a reasonable processing window
        # (3x max_leads so we have enough to hit the qualified target)
        processing_window = filtered_companies[:request.max_leads * 3]
        log.info("Processing window: %d companies", len(processing_window))

        # ── Stage 3: Website Enrichment ───────────────────────────────────────
        run_tracker.update_run(run_id, current_stage="website_enrichment")
        log.info("── Stage 3: Website Enrichment ──")
        website_analyses = website.run(processing_window, client_profile, run_id, log)

        # ── Stage 4: Contact Enrichment ───────────────────────────────────────
        # Loop: keep finding more leads until we hit max_leads qualified
        run_tracker.update_run(run_id, current_stage="contact_enrichment")
        log.info("── Stage 4: Contact Enrichment ──")

        all_qualified: list = []
        all_partial: list = []

        qualified_batch, partial_batch = contacts.run(
            companies=processing_window,
            website_analyses=website_analyses,
            client_profile=client_profile,
            input_mode=request.mode,
            run_id=run_id,
            logger=log,
        )
        all_qualified.extend(qualified_batch)
        all_partial.extend(partial_batch)

        # If we haven't hit max_leads yet and there are more filtered companies
        # available beyond the initial window, process more in batches
        remaining = filtered_companies[request.max_leads * 3:]
        batch_size = 20

        while len(all_qualified) < request.max_leads and remaining:
            extra_batch = remaining[:batch_size]
            remaining = remaining[batch_size:]

            log.info(
                "Need more qualified leads (%d/%d) — processing %d more companies",
                len(all_qualified), request.max_leads, len(extra_batch)
            )

            extra_website = website.run(extra_batch, client_profile, run_id, log)
            extra_qualified, extra_partial = contacts.run(
                companies=extra_batch,
                website_analyses=extra_website,
                client_profile=client_profile,
                input_mode=request.mode,
                run_id=run_id,
                logger=log,
            )
            all_qualified.extend(extra_qualified)
            all_partial.extend(extra_partial)

        # Trim to max_leads
        all_qualified = all_qualified[:request.max_leads]
        run_tracker.update_run(
            run_id,
            qualified_count=len(all_qualified),
            partial_count=len(all_partial),
        )

        log.info(
            "After contact enrichment: %d qualified, %d partial",
            len(all_qualified), len(all_partial)
        )

        # ── Stage 5: Full Qualification ───────────────────────────────────────
        run_tracker.update_run(run_id, current_stage="qualification")
        log.info("── Stage 5: Full Qualification ──")
        all_qualified = qualify.run(all_qualified, client_profile, run_id, log)
        # Also qualify partial leads (lower priority)
        all_partial = qualify.run(all_partial, client_profile, run_id, log)

        # ── Stage 6: Hook Generation ──────────────────────────────────────────
        run_tracker.update_run(run_id, current_stage="hook_generation")
        log.info("── Stage 6: Hook Generation ──")
        all_qualified = hooks.run(all_qualified, client_profile, run_id, log)
        # Hooks for partial leads too (they still get outreach copy)
        all_partial = hooks.run(all_partial, client_profile, run_id, log)

        # ── Stage 7: Report ───────────────────────────────────────────────────
        run_tracker.update_run(run_id, current_stage="report")
        log.info("── Stage 7: Report ──")

        state = run_tracker.get_run(run_id)
        sheet_url, csv_path, recommendations = report.run(
            qualified_leads=all_qualified,
            partial_leads=all_partial,
            client_profile=client_profile,
            run_state=state,
            sheet_name=request.output_sheet_name,
            logger=log,
        )

        # ── Complete ──────────────────────────────────────────────────────────
        run_tracker.complete_run(
            run_id=run_id,
            sheet_url=sheet_url,
            csv_path=csv_path,
            recommendations=recommendations,
            qualified_count=len(all_qualified),
            partial_count=len(all_partial),
            apollo_credits=get_credits_used(),
            firecrawl_pages=get_pages_crawled(),
            llm_tokens=get_tokens_used(),
        )

        log.info("=" * 60)
        log.info("PIPELINE COMPLETE")
        log.info("  Qualified leads: %d", len(all_qualified))
        log.info("  Partial leads:   %d", len(all_partial))
        log.info("  Apollo credits:  %d", get_credits_used())
        log.info("  Firecrawl pages: %d", get_pages_crawled())
        log.info("  LLM tokens:      %d", get_tokens_used())
        log.info("  Sheet: %s", sheet_url)
        log.info("  CSV:   %s", csv_path)
        log.info("=" * 60)

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        log.error("PIPELINE FAILED: %s\n%s", error_msg, traceback.format_exc())
        run_tracker.fail_run(run_id, error_msg)
