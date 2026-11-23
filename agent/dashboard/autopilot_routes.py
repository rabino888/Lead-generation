"""Plan review for the autopilot. Approval does not send email."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.dashboard.static_mount import STATIC_DIR

router = APIRouter(prefix="/autopilot", tags=["autopilot"])


def _data_root() -> Path:
    return Path(os.environ.get("DATA_ROOT", "data"))


def _check_access(
    x_dashboard_secret: Optional[str] = Header(None, alias="X-Dashboard-Secret"),
    key: Optional[str] = Query(None),
) -> None:
    expected = (os.environ.get("DASHBOARD_SECRET") or "").strip()
    if not expected:
        return
    provided = (x_dashboard_secret or key or "").strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid dashboard secret")


class StartBody(BaseModel):
    url: str
    client_id: str = ""
    notes: str = ""
    target_leads: int = Field(default=25, ge=1, le=500)
    max_usd: float = Field(default=25.0, ge=0)
    seed_source: str = ""
    contact_provider: str = ""
    csv_path: str = ""


class ApproveBody(BaseModel):
    confirm: bool = False
    gate: str = ""
    and_run: bool = True
    generate_sequence: bool = False
    notes: str = ""


class RejectBody(BaseModel):
    notes: str = ""
    rerun: bool = False


def _public_state(state: dict) -> dict:
    """Drop the raw page extract from list payloads; the plan markdown stays."""
    research = state.get("research") or {}
    return {
        "run_id": state.get("run_id"),
        "phase": state.get("phase"),
        "client_id": state.get("client_id"),
        "campaign_id": state.get("campaign_id"),
        "company_url": state.get("company_url"),
        "company_name": state.get("company_name"),
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
        "revision": state.get("revision"),
        "spent_usd": state.get("spent_usd"),
        "halt_reason": state.get("halt_reason"),
        "failed_stage": state.get("failed_stage"),
        "completed_stages": state.get("completed_stages") or [],
        "generate_sequence": bool(state.get("generate_sequence")),
        "sequence_skipped": bool(state.get("sequence_skipped")),
        "approval": state.get("approval"),
        "approvals": state.get("approvals") or {},
        "assistant": state.get("assistant") or {},
        "plan": state.get("plan"),
        "log": state.get("log") or [],
        "research": {
            "url": research.get("url"),
            "company_name": research.get("company_name"),
            "description": research.get("description"),
        },
    }


@router.get("", response_class=FileResponse)
@router.get("/", response_class=FileResponse)
async def autopilot_home(_: None = Depends(_check_access)):
    path = STATIC_DIR / "autopilot.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="autopilot.html missing")
    return FileResponse(path, media_type="text/html; charset=utf-8")


@router.get("/api/runs")
async def autopilot_list(_: None = Depends(_check_access)):
    from agent.autopilot.store import list_states

    rows = [_public_state(row) for row in list_states(_data_root())]
    return {"runs": rows, "count": len(rows)}


@router.get("/api/runs/{run_id}")
async def autopilot_get(run_id: str, _: None = Depends(_check_access)):
    from agent.autopilot.orchestrator import status

    try:
        state = status(run_id, data_root=_data_root())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _public_state(state)


@router.post("/api/runs")
async def autopilot_start(body: StartBody, _: None = Depends(_check_access)):
    from agent.autopilot.orchestrator import start

    try:
        state = start(
            body.url,
            data_root=_data_root(),
            client_id=body.client_id,
            notes=body.notes,
            target_leads=body.target_leads,
            max_usd=body.max_usd,
            seed_source=body.seed_source,
            contact_provider=body.contact_provider,
            csv_path=body.csv_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "run": _public_state(state)}


@router.post("/api/runs/{run_id}/approve")
async def autopilot_approve(run_id: str, body: ApproveBody, _: None = Depends(_check_access)):
    from agent.autopilot.orchestrator import approve

    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true")
    try:
        state = approve(
            run_id,
            data_root=_data_root(),
            notes=body.notes,
            gate=body.gate,
            and_run=body.and_run,
            generate_sequence=body.generate_sequence,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "run": _public_state(state)}


@router.post("/api/runs/{run_id}/reject")
async def autopilot_reject(run_id: str, body: RejectBody, _: None = Depends(_check_access)):
    from agent.autopilot.orchestrator import reject

    try:
        state = reject(run_id, body.notes, data_root=_data_root(), rerun=body.rerun)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "run": _public_state(state)}
