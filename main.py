"""
Lead Generation Agent — FastAPI Application
Endpoints: POST /run | GET /runs/{run_id} | POST /feedback | GET /health
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

load_dotenv()

from agent.models import (
    ClientProfile,
    FeedbackEntry,
    FeedbackRequest,
    InputMode,
    RunRequest,
    RunStatus,
)
from agent.pipeline import run_pipeline
from agent.utils.auth import get_current_client, invalidate_cache
from agent.utils.deduplication import clear_registry, get_seen_count
from agent.utils.logger import log
from agent.utils.run_tracker import create_run, get_run

app = FastAPI(
    title="TBOmedia Lead Generation Agent",
    description="Fully qualified B2B lead generation — Apollo + Firecrawl + Claude",
    version="1.0.0",
)


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Railway health check."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ── Start a Run ────────────────────────────────────────────────────────────────

@app.post("/run")
async def start_run(
    request: RunRequest,
    background_tasks: BackgroundTasks,
    client: ClientProfile = Depends(get_current_client),
):
    """
    Start a lead generation run (async).
    Returns run_id immediately — poll /runs/{run_id} for status and results.
    """
    # Validate request
    if request.mode == InputMode.KEYWORD and not request.keyword:
        raise HTTPException(status_code=422, detail="keyword is required for mode='keyword'")
    if request.mode == InputMode.ICP and not request.icp:
        raise HTTPException(status_code=422, detail="icp is required for mode='icp'")
    if request.mode == InputMode.COMPANY_LIST and not request.company_list:
        raise HTTPException(status_code=422, detail="company_list is required for mode='company_list'")

    # Create run state
    run_state = create_run(
        client_id=client.client_id,
        input_mode=request.mode,
        max_leads=request.max_leads,
        keyword=request.keyword,
    )

    log.info(
        "Run started — id: %s | client: %s | mode: %s | max_leads: %d",
        run_state.run_id, client.client_id, request.mode.value, request.max_leads
    )

    # Launch pipeline as background task
    background_tasks.add_task(run_pipeline, run_state.run_id, request, client)

    return {
        "run_id": run_state.run_id,
        "status": "started",
        "message": f"Run started. Poll GET /runs/{run_state.run_id} for status and results.",
    }


# ── Poll Run Status ────────────────────────────────────────────────────────────

@app.get("/runs/{run_id}")
async def get_run_status(
    run_id: str,
    client: ClientProfile = Depends(get_current_client),
):
    """
    Check the status of a lead generation run.
    Returns full results when status == 'complete'.
    """
    state = get_run(run_id)

    if not state:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    # Security: clients can only see their own runs
    if state.client_id != client.client_id:
        raise HTTPException(status_code=403, detail="Access denied")

    response: dict = {
        "run_id": state.run_id,
        "status": state.status.value,
        "input_mode": state.input_mode.value,
        "current_stage": state.current_stage,
        "started_at": state.started_at.isoformat(),
    }

    if state.status == RunStatus.RUNNING:
        response["message"] = f"Pipeline running — current stage: {state.current_stage}"

    elif state.status == RunStatus.COMPLETE:
        response.update({
            "completed_at": state.completed_at.isoformat() if state.completed_at else None,
            "qualified_leads": state.qualified_count,
            "partial_leads": state.partial_count,
            "sheet_url": state.sheet_url,
            "csv_path": state.csv_path,
            "recommendations": state.recommendations,
            "usage": {
                "apollo_credits_used": state.apollo_credits_used,
                "firecrawl_pages_crawled": state.firecrawl_pages_crawled,
                "llm_tokens_used": state.llm_tokens_used,
            },
        })

    elif state.status == RunStatus.FAILED:
        response["error"] = state.error

    return response


# ── Feedback ───────────────────────────────────────────────────────────────────

@app.post("/feedback")
async def submit_feedback(
    feedback: FeedbackRequest,
    client: ClientProfile = Depends(get_current_client),
):
    """
    Submit lead outcome feedback. Updates the per-client feedback log
    and may adjust the ICP pre-filter threshold over time.
    """
    entry = FeedbackEntry(
        run_id=feedback.run_id,
        lead_id=feedback.lead_id,
        outcome=feedback.outcome,
        notes=feedback.notes,
    )

    # Append to per-client feedback log
    feedback_dir = Path("data") / "clients" / client.client_id
    feedback_dir.mkdir(parents=True, exist_ok=True)
    feedback_path = feedback_dir / "feedback_log.json"

    existing: list = []
    if feedback_path.exists():
        try:
            with open(feedback_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    existing.append(entry.model_dump(mode="json"))

    with open(feedback_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, default=str)

    # Analyse recent feedback to auto-tune prefilter threshold
    _maybe_tune_threshold(client.client_id, existing, log)

    log.info(
        "Feedback saved — client: %s | lead: %s | outcome: %s",
        client.client_id, feedback.lead_id, feedback.outcome.value
    )

    return {"saved": True, "total_feedback_entries": len(existing)}


# ── Feedback Threshold Tuning ──────────────────────────────────────────────────

# ── Admin Endpoints ────────────────────────────────────────────────────────────
# Protected by ADMIN_SECRET env var (set this in Railway + .env)

def _check_admin(x_admin_secret: str = Header(None)) -> None:
    """Dependency: validate admin secret header."""
    admin_secret = os.environ.get("ADMIN_SECRET", "")
    if not admin_secret:
        raise HTTPException(status_code=500, detail="ADMIN_SECRET not configured")
    if x_admin_secret != admin_secret:
        raise HTTPException(status_code=401, detail="Invalid admin secret")


@app.post("/admin/clients")
async def create_client(
    name: str,
    client_id: str,
    client_email: str = "",
    _: None = Depends(_check_admin),
):
    """
    Generate a new client API key and write the row to the Clients Google Sheet.
    Returns the generated API key — save it, it won't be shown again.

    Headers: X-Admin-Secret: <your ADMIN_SECRET>
    Params:  name (display name), client_id (slug), client_email (optional)
    """
    from agent.integrations.sheets import _get_gspread_client

    api_key = f"lgk-{secrets.token_urlsafe(32)}"

    try:
        gc = _get_gspread_client()
        sheet_id = os.environ["CLIENTS_SHEET_ID"]
        ws = gc.open_by_key(sheet_id).sheet1

        # Append new client row (matches sheet column order)
        new_row = [
            client_id,      # client_id
            name,           # name
            api_key,        # api_key
            "",             # icp_industry
            "",             # icp_location
            "",             # icp_size_min
            "",             # icp_size_max
            "",             # icp_job_titles
            client_email,   # client_email
            "TRUE",         # active
        ]
        ws.append_row(new_row, value_input_option="USER_ENTERED")
        invalidate_cache()

        log.info("Admin: created client '%s' (id: %s)", name, client_id)

    except Exception as e:
        log.error("Admin: failed to create client: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to write to Clients sheet: {e}")

    return {
        "created": True,
        "client_id": client_id,
        "name": name,
        "api_key": api_key,
        "message": "Save this API key — it will not be shown again.",
    }


@app.get("/admin/clients/{client_id}/dedup")
async def get_dedup_status(
    client_id: str,
    _: None = Depends(_check_admin),
):
    """Return how many unique companies have been delivered to a client."""
    count = get_seen_count(client_id)
    return {
        "client_id": client_id,
        "total_delivered_companies": count,
        "message": f"{count} unique companies have been delivered and will not appear again.",
    }


@app.delete("/admin/clients/{client_id}/dedup")
async def reset_dedup(
    client_id: str,
    _: None = Depends(_check_admin),
):
    """
    Wipe the seen_leads registry for a client.
    Use with care — this allows re-delivery of previously seen companies.
    """
    clear_registry(client_id)
    log.warning("Admin: cleared dedup registry for client %s", client_id)
    return {"cleared": True, "client_id": client_id}


def _maybe_tune_threshold(client_id: str, feedback_entries: list, logger) -> None:
    """
    After every 10 feedback entries, analyse conversion patterns
    and update the ICP prefilter threshold in icp_profile.json.
    """
    if len(feedback_entries) < 10 or len(feedback_entries) % 10 != 0:
        return

    recent = feedback_entries[-20:]  # Analyse last 20 entries
    converted = sum(1 for e in recent if e.get("outcome") == "converted")
    wrong_fit = sum(1 for e in recent if e.get("outcome") == "wrong_fit")
    total = len(recent)

    conversion_rate = converted / total
    wrong_fit_rate = wrong_fit / total

    profile_path = Path("data") / "clients" / client_id / "icp_profile.json"

    # Load existing profile or create default
    profile: dict = {"prefilter_threshold": 40}
    if profile_path.exists():
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = json.load(f)
        except Exception:
            pass

    current_threshold = profile.get("prefilter_threshold", 40)
    new_threshold = current_threshold

    # Tighten if too many wrong-fit leads
    if wrong_fit_rate > 0.4 and current_threshold < 70:
        new_threshold = min(current_threshold + 5, 70)
        logger.info(
            "Client %s: wrong_fit rate %.0f%% — tightening threshold %d → %d",
            client_id, wrong_fit_rate * 100, current_threshold, new_threshold
        )

    # Loosen if conversion rate is high (we're being too selective)
    elif conversion_rate > 0.5 and current_threshold > 25:
        new_threshold = max(current_threshold - 5, 25)
        logger.info(
            "Client %s: conversion rate %.0f%% — loosening threshold %d → %d",
            client_id, conversion_rate * 100, current_threshold, new_threshold
        )

    if new_threshold != current_threshold:
        profile["prefilter_threshold"] = new_threshold
        profile["last_updated"] = datetime.utcnow().isoformat()
        profile["conversion_rate"] = round(conversion_rate, 2)
        profile["wrong_fit_rate"] = round(wrong_fit_rate, 2)

        profile_path.parent.mkdir(parents=True, exist_ok=True)
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2)
