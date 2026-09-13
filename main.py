"""
Lead Generation Agent — FastAPI Application
Endpoints: POST /run | GET /runs/{run_id} | POST /feedback | POST /webhooks/apollo-phone/{secret} | GET /health
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from agent.webhooks.apollo_phone import (
    load_payloads,
    payload_stats,
    resolve_apollo_phone_webhook_url,
    store_payload,
    verify_webhook_secret,
)

load_dotenv(override=True)

from agent.models import (
    ClientProfile,
    FeedbackEntry,
    FeedbackRequest,
    ICPProfile,
    InputMode,
    RunRequest,
    RunStatus,
    SenderProfile,
)
from agent.pipeline import run_pipeline
from agent.dashboard.routes import router as dashboard_router
from agent.dashboard.builder_routes import router as builder_router
from agent.utils.auth import invalidate_cache
from agent.utils.deduplication import clear_registry, get_seen_count
from agent.utils.logger import log
from agent.utils.run_tracker import create_run, get_run

app = FastAPI(
    title="TBOmedia Lead Generation Agent",
    description="Fully qualified B2B lead generation — Apollo + Firecrawl + Claude",
    version="1.0.0",
)

app.include_router(dashboard_router)
app.include_router(builder_router)

from agent.dashboard.static_mount import mount_portal_static

mount_portal_static(app)


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Railway health check."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ── Apollo Phone Webhook ───────────────────────────────────────────────────────

@app.post("/webhooks/apollo-phone/{secret}")
async def apollo_phone_webhook(secret: str, request: Request):
    """
    Receive async phone reveal callbacks from Apollo.io.
    URL must match APOLLO_PHONE_WEBHOOK_URL (includes APOLLO_PHONE_WEBHOOK_SECRET).
    """
    if not verify_webhook_secret(secret):
        raise HTTPException(status_code=404, detail="Not found")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    store_payload(payload)
    people = payload.get("people")
    person_count = len(people) if isinstance(people, list) else 0
    log.info("Apollo phone webhook received — people: %d | status: %s", person_count, payload.get("status"))
    return {"received": True, "people": person_count}


# ── Start a Run ────────────────────────────────────────────────────────────────

@app.post("/run")
async def start_run(
    request: RunRequest,
    background_tasks: BackgroundTasks,
):
    """
    Start a deterministic lead generation run (async).
    Modes: curated_seeds (default) or apollo_csv.
    """
    if request.mode == InputMode.CURATED_SEEDS:
        if not (request.company_seeds or request.company_seeds_csv):
            raise HTTPException(
                status_code=422,
                detail="company_seeds or company_seeds_csv is required for mode='curated_seeds'",
            )
        if request.company_seeds_csv and not Path(request.company_seeds_csv).exists():
            raise HTTPException(
                status_code=422,
                detail=f"company_seeds_csv file not found: {request.company_seeds_csv}",
            )
    elif request.mode == InputMode.APOLLO_CSV:
        if not request.apollo_csv_path:
            raise HTTPException(status_code=422, detail="apollo_csv_path is required for mode='apollo_csv'")
    else:
        raise HTTPException(status_code=422, detail=f"Unsupported mode: {request.mode.value}")

    if not request.sender:
        raise HTTPException(status_code=422, detail="sender is required and must describe the offer/business")

    client = _client_from_request(request)

    run_state = create_run(
        client_id=client.client_id,
        input_mode=request.mode,
        max_leads=request.max_leads,
        keyword=request.apollo_csv_path or "curated_seeds",
        campaign_id=request.campaign_id,
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
):
    """
    Check the status of a lead generation run.
    Returns full results when status == 'complete'.
    """
    state = get_run(run_id)

    if not state:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

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
            "rejected_leads": state.partial_count,
            "partial_leads": 0,
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
    state = get_run(feedback.run_id)
    client_id = state.client_id if state else "local"

    feedback_dir = Path("data") / "clients" / client_id
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

    log.info(
        "Feedback saved — client: %s | lead: %s | outcome: %s",
        client_id, feedback.lead_id, feedback.outcome.value
    )

    return {"saved": True, "total_feedback_entries": len(existing)}


def _client_from_request(request: RunRequest) -> ClientProfile:
    """Build run client context directly from the request payload."""
    sender = request.sender or SenderProfile()
    return ClientProfile(
        client_id=sender.client_id,
        name=sender.name,
        api_key="local",
        client_email=sender.client_email,
        icp=request.icp or ICPProfile(),
        sender=sender,
        active=True,
    )


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


@app.get("/admin/webhooks/apollo-phone")
async def list_apollo_phone_webhooks(
    since: str | None = None,
    _: None = Depends(_check_admin),
):
    """Return stored Apollo phone webhook payloads (for local import scripts)."""
    from agent.webhooks.apollo_phone import PAYLOADS_FILE

    payloads = load_payloads(since=since)
    return {
        "count": len(payloads),
        "payloads": payloads,
        "webhook_url": resolve_apollo_phone_webhook_url(),
        "storage_path": str(PAYLOADS_FILE),
    }


@app.get("/admin/webhooks/apollo-phone/url")
async def get_apollo_phone_webhook_url(_: None = Depends(_check_admin)):
    """Return the webhook URL to configure in Apollo / APOLLO_PHONE_WEBHOOK_URL."""
    url = resolve_apollo_phone_webhook_url()
    if not url:
        raise HTTPException(
            status_code=500,
            detail="Set APOLLO_PHONE_WEBHOOK_URL or PUBLIC_BASE_URL + APOLLO_PHONE_WEBHOOK_SECRET",
        )
    return {"webhook_url": url, **payload_stats()}
