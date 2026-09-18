"""
HTTP serving for the lead-list cost dashboard.

Mount via main.py or scripts/serve_cost_dashboard.py:
  GET /dashboard              — master portal (static dashboard.html + hash routes)
  GET /dashboard/api            — JSON index
  GET /dashboard/campaigns/{id} — redirect to #/campaign/{id}
  GET /dashboard/clients/{id}   — redirect to #/client/{id}
  POST /dashboard/rebuild       — refresh index JSON (admin secret)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from agent.dashboard.static_mount import STATIC_DIR
from agent.utils.logger import log

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _data_root() -> Path:
    return Path(os.environ.get("DATA_ROOT", "data"))


def _dashboard_html() -> Path:
    return STATIC_DIR / "dashboard.html"


def _dashboard_index() -> Path:
    return _data_root() / "cost_dashboard_index.json"


def _check_dashboard_access(
    x_dashboard_secret: Optional[str] = Header(None, alias="X-Dashboard-Secret"),
    key: Optional[str] = Query(None),
) -> None:
    """Optional read guard — set DASHBOARD_SECRET in .env to enable."""
    expected = (os.environ.get("DASHBOARD_SECRET") or "").strip()
    if not expected:
        return
    provided = (x_dashboard_secret or key or "").strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid dashboard secret")


def _check_admin(x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret")) -> None:
    admin_secret = (os.environ.get("ADMIN_SECRET") or "").strip()
    if not admin_secret:
        raise HTTPException(status_code=500, detail="ADMIN_SECRET not configured")
    if (x_admin_secret or "").strip() != admin_secret:
        raise HTTPException(status_code=401, detail="Invalid admin secret")


def rebuild_dashboard_files(data_root: Optional[Path] = None) -> dict:
    """Rebuild cost_dashboard_index.json (+ optional offline HTML). No per-entity HTML."""
    from agent.utils.cost_dashboard import build_dashboard_index
    from agent.utils.cost_dashboard_html import render_document

    root = data_root or _data_root()
    campaigns_root = root / "campaigns"
    index = build_dashboard_index(campaigns_root)

    index_path = root / "cost_dashboard_index.json"
    html_path = root / "cost_dashboard.html"
    index_path.write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")
    # Offline single-file export (file://); live UI uses static/dashboard.html + /dashboard/api.
    html_path.write_text(render_document(index), encoding="utf-8")

    totals = index.get("totals") or {}
    log.info(
        "Cost dashboard rebuilt — %s campaigns, $%.2f total (master template; no scoped pages)",
        totals.get("campaign_count", 0),
        float(totals.get("usd_total") or 0),
    )
    return {
        "index_path": str(index_path),
        "html_path": str(html_path),
        "portal_html": str(_dashboard_html()),
        "scoped_pages": 0,
        "paths": {"main": str(html_path), "portal": str(_dashboard_html())},
        "totals": totals,
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard_home(_: None = Depends(_check_dashboard_access)):
    path = _dashboard_html()
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="dashboard.html missing under agent/dashboard/static/",
        )
    return FileResponse(path, media_type="text/html; charset=utf-8")


@router.get("/api")
async def dashboard_api(
    refresh: int = Query(0),
    _: None = Depends(_check_dashboard_access),
):
    from agent.utils.cost_dashboard import load_and_sync_dashboard_index

    root = _data_root()
    path = _dashboard_index()
    # Full rebuild only when missing or explicitly requested (?refresh=1).
    # Always cheap-sync clients from disk so new clients appear without a 30s rebuild.
    if (not path.is_file()) or refresh:
        rebuild_dashboard_files(root)
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Dashboard index missing. Run: python scripts/build_cost_dashboard.py",
        )
    index = load_and_sync_dashboard_index(root)
    return JSONResponse(index)


@router.get("/campaigns/{campaign_id}")
async def dashboard_campaign(
    campaign_id: str,
    _: None = Depends(_check_dashboard_access),
):
    return RedirectResponse(url=f"/dashboard#/campaign/{quote(campaign_id, safe='')}", status_code=307)


def _campaign_dir(campaign_id: str) -> Path:
    from agent.utils.client_store import resolve_campaign_dir

    root = _data_root()
    path = resolve_campaign_dir(root, campaign_id)
    if path is None or not path.is_dir():
        # Compat: legacy data/campaigns/{id} only
        legacy = (root / "campaigns" / campaign_id).resolve()
        campaigns_root = (root / "campaigns").resolve()
        if str(legacy).startswith(str(campaigns_root)) and legacy.is_dir():
            return legacy
        raise HTTPException(status_code=404, detail=f"Campaign not found: {campaign_id}")
    # Allow client-canonical paths and legacy junctions under data/campaigns/
    data_root = root.resolve()
    resolved = path.resolve()
    if not str(resolved).startswith(str(data_root)):
        raise HTTPException(status_code=400, detail="Invalid campaign id")
    return resolved


@router.get("/api/campaigns/{campaign_id}/leads")
async def dashboard_leads_summary(
    campaign_id: str,
    stage: str = Query("auto"),
    preview: int = Query(25, ge=0, le=200),
    run_id: Optional[str] = Query(None),
    view: str = Query("summary"),
    _: None = Depends(_check_dashboard_access),
):
    from agent.utils.lead_export import lead_summary

    try:
        return lead_summary(
            _campaign_dir(campaign_id),
            stage=stage,
            preview=preview,
            run_id=run_id,
            view=view,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/campaigns/{campaign_id}/leads.csv")
async def dashboard_leads_csv(
    campaign_id: str,
    stage: str = Query("auto"),
    run_id: Optional[str] = Query(None),
    _: None = Depends(_check_dashboard_access),
):
    from agent.utils.lead_export import export_leads_csv

    try:
        path = export_leads_csv(
            _campaign_dir(campaign_id),
            stage=stage,
            run_id=run_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="text/csv; charset=utf-8",
        filename=path.name,
    )


@router.get("/clients/{client_id}")
async def dashboard_client(
    client_id: str,
    _: None = Depends(_check_dashboard_access),
):
    return RedirectResponse(url=f"/dashboard#/client/{quote(client_id, safe='')}", status_code=307)


@router.post("/rebuild")
async def dashboard_rebuild(_: None = Depends(_check_admin)):
    try:
        result = rebuild_dashboard_files(_data_root())
    except Exception as exc:
        log.exception("Dashboard rebuild failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, **result}
