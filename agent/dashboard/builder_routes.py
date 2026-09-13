"""
List-building control dashboard (pre-run plan + cost estimate).

SPEC-dashboard.md · SPEC-talent-search.md

  GET  /builder                         — UI
  GET  /builder/api/campaigns           — list campaigns
  POST /builder/api/campaigns          — create campaign folder
  GET  /builder/api/campaigns/{id}      — summary + plan
  POST /builder/api/campaigns/{id}/estimate
  PUT  /builder/api/campaigns/{id}/plan
  GET/PUT /builder/api/campaigns/{id}/icp
  GET/PUT /builder/api/campaigns/{id}/seed_sources
  GET  /builder/api/rates
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from agent.utils.enrichment_plan import (
    campaign_summary,
    default_plan,
    estimate_plan,
    list_campaign_dirs,
    load_plan,
    normalize_plan,
    save_plan,
    unit_rates,
    validate_plan,
)
from agent.utils.client_store import (
    client_campaign_dir,
    ensure_client,
    extract_icp_from_campaign,
    icp_dir,
    icp_summary,
    list_client_icps,
    load_icp_doc,
    load_icp_meta,
    resolve_campaign_dir,
    save_icp,
    set_campaign_display_name,
    snapshot_icp_into_campaign,
    sync_campaign_config_to_client,
    unique_id as unique_store_id,
    _try_junction,
    _write_json,
)

router = APIRouter(prefix="/builder", tags=["builder"])

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_CAMPAIGN_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_CLIENT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def _data_root() -> Path:
    return Path(os.environ.get("DATA_ROOT", "data"))


def _campaigns_root() -> Path:
    return _data_root() / "campaigns"


def _campaign_dir(campaign_id: str) -> Path:
    if not _CAMPAIGN_ID_RE.match(campaign_id):
        raise HTTPException(status_code=400, detail="Invalid campaign id")
    path = resolve_campaign_dir(_data_root(), campaign_id)
    if path is None or not path.is_dir():
        raise HTTPException(status_code=404, detail=f"Campaign not found: {campaign_id}")
    return path


def _campaign_id_taken(campaign_id: str) -> bool:
    return resolve_campaign_dir(_data_root(), campaign_id) is not None


def _unique_campaign_id(preferred: str) -> str:
    base = preferred[:64] or "campaign"
    if not _campaign_id_taken(base):
        return base
    for n in range(2, 100):
        cand = f"{base}_{n}"[:64]
        if not _campaign_id_taken(cand):
            return cand
    raise HTTPException(status_code=409, detail="Could not allocate a unique campaign id")


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


class EstimateRequest(BaseModel):
    plan: dict[str, Any] = Field(default_factory=dict)


class PlanPutRequest(BaseModel):
    plan: dict[str, Any]


class CampaignMetaPut(BaseModel):
    display_name: str = ""
    client_name: str = ""
    # None = leave unchanged; empty string clears the brief
    brief: Optional[str] = None


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def builder_home(
    request: Request,
    _: None = Depends(_check_access),
):
    """Builder is create/edit only — client browse lives on the cost ledger."""
    q = request.query_params
    client = (q.get("client") or "").strip()
    campaign = (q.get("campaign") or "").strip()
    is_new = q.get("new") == "1"
    if not client and not campaign and not is_new:
        return RedirectResponse(url="/dashboard", status_code=302)
    # Client portal is the ledger — do not duplicate it under /builder?client=
    if client and not is_new and not campaign:
        return RedirectResponse(
            url=f"/dashboard/clients/{client}",
            status_code=302,
        )
    html_path = _STATIC_DIR / "builder.html"
    if not html_path.is_file():
        raise HTTPException(status_code=404, detail="builder.html missing")
    return FileResponse(html_path, media_type="text/html; charset=utf-8")


@router.get("/api/rates")
async def builder_rates(_: None = Depends(_check_access)):
    return {"confidence": "est", "rates": unit_rates()}


class CreateCampaignRequest(BaseModel):
    """Register outreach under a client. campaign_id is optional — auto from client + label."""

    client_id: str
    client_name: str = ""
    label: str = ""
    brief: str = ""
    campaign_type: str = "company_outreach"
    # Link an existing library ICP (data/clients/{client}/icps/{icp_id})
    icp_id: Optional[str] = None
    # Optional override; omit from the UI — folder id is assigned as {client}_{label}.
    campaign_id: Optional[str] = None


def _slug_part(value: str, *, fallback: str = "list") -> str:
    raw = re.sub(r"[^a-zA-Z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return (raw[:40] or fallback)


@router.get("/api/clients/{client_id}/icps")
async def builder_list_client_icps(client_id: str, _: None = Depends(_check_access)):
    cid = (client_id or "").strip()
    if not _CLIENT_ID_RE.match(cid):
        raise HTTPException(status_code=400, detail="Invalid client id")
    items = list_client_icps(_data_root(), cid)
    return {"client_id": cid, "icps": items, "count": len(items)}


@router.get("/api/clients/{client_id}/icps/{icp_id}")
async def builder_get_client_icp(
    client_id: str,
    icp_id: str,
    _: None = Depends(_check_access),
):
    cid = (client_id or "").strip()
    iid = (icp_id or "").strip()
    if not _CLIENT_ID_RE.match(cid) or not _CAMPAIGN_ID_RE.match(iid):
        raise HTTPException(status_code=400, detail="Invalid client or icp id")
    summary = icp_summary(_data_root(), cid, iid)
    if not summary:
        raise HTTPException(status_code=404, detail=f"ICP not found: {iid}")
    return {
        "ok": True,
        "icp_id": iid,
        "client_id": cid,
        "meta": load_icp_meta(_data_root(), cid, iid),
        "data": load_icp_doc(_data_root(), cid, iid),
        "summary": summary,
        "disk_path": summary.get("disk_path"),
    }


class IcpMetaPut(BaseModel):
    display_name: str = ""
    brief: Optional[str] = None


@router.put("/api/clients/{client_id}/icps/{icp_id}/meta")
async def builder_put_client_icp_meta(
    client_id: str,
    icp_id: str,
    body: IcpMetaPut,
    _: None = Depends(_check_access),
):
    cid = (client_id or "").strip()
    iid = (icp_id or "").strip()
    if not _CLIENT_ID_RE.match(cid) or not _CAMPAIGN_ID_RE.match(iid):
        raise HTTPException(status_code=400, detail="Invalid client or icp id")
    path = icp_dir(_data_root(), cid, iid)
    if not path.is_dir():
        raise HTTPException(status_code=404, detail=f"ICP not found: {iid}")
    name = (body.display_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="display_name is required")
    meta = load_icp_meta(_data_root(), cid, iid)
    meta["display_name"] = name
    meta["name"] = name
    if body.brief is not None:
        meta["brief"] = body.brief
    _write_json(path / "meta.json", meta)
    return {"ok": True, "summary": icp_summary(_data_root(), cid, iid)}


@router.get("/api/campaigns")
async def builder_list_campaigns(_: None = Depends(_check_access)):
    items = [campaign_summary(p) for p in list_campaign_dirs(_campaigns_root())]
    return {"campaigns": items, "count": len(items)}


@router.post("/api/campaigns")
async def builder_create_campaign(
    body: CreateCampaignRequest,
    _: None = Depends(_check_access),
):
    client_id = (body.client_id or "").strip()
    client_name = (body.client_name or "").strip() or client_id
    ctype = (body.campaign_type or "company_outreach").strip()
    label = (body.label or "").strip()
    brief = body.brief or ""
    existing_icp = (body.icp_id or "").strip()
    if ctype not in ("company_outreach", "talent_search"):
        raise HTTPException(status_code=400, detail="campaign_type must be company_outreach or talent_search")
    if not _CLIENT_ID_RE.match(client_id):
        raise HTTPException(
            status_code=400,
            detail="client_id must be 1–64 chars: letters, digits, _ or -",
        )

    data_root = _data_root()
    ensure_client(data_root, client_id, client_name)
    (_campaigns_root()).mkdir(parents=True, exist_ok=True)

    explicit = (body.campaign_id or "").strip()
    if explicit:
        if not _CAMPAIGN_ID_RE.match(explicit):
            raise HTTPException(
                status_code=400,
                detail="campaign_id must be 1–64 chars: letters, digits, _ or -",
            )
        if _campaign_id_taken(explicit):
            raise HTTPException(status_code=409, detail=f"Campaign already exists: {explicit}")
        campaign_id = explicit
    else:
        suffix = "talent" if ctype == "talent_search" else "outreach"
        if label:
            preferred = f"{_slug_part(client_id)}_{_slug_part(label, fallback=suffix)}"
        else:
            preferred = f"{_slug_part(client_id)}_{suffix}"
        campaign_id = _unique_campaign_id(preferred)

    # Canonical home: data/clients/{client}/campaigns/{campaign}
    path = client_campaign_dir(data_root, client_id, campaign_id)
    if path.exists():
        raise HTTPException(status_code=409, detail=f"Campaign already exists: {campaign_id}")
    path.mkdir(parents=True, exist_ok=False)
    (path / "stages").mkdir(exist_ok=True)

    display = label or client_name or campaign_id
    from agent.utils.brief_icp import parse_brief_to_icp

    if existing_icp:
        if not icp_dir(data_root, client_id, existing_icp).is_dir():
            raise HTTPException(status_code=404, detail=f"ICP not found: {existing_icp}")
        icp_id = existing_icp
        icp_stub = load_icp_doc(data_root, client_id, icp_id)
    else:
        icp_stub = parse_brief_to_icp(
            brief,
            campaign_id=campaign_id,
            campaign_type=ctype,
        )
        icp_stub.setdefault("job_titles", [])
        icp_stub.setdefault("contact_locations", [])
        icp_stub.setdefault("include_keywords", [])
        icp_stub.setdefault("exclude_keywords", [])
        if ctype != "talent_search":
            icp_stub.setdefault("company_size_min", None)
            icp_stub.setdefault("company_size_max", None)
            icp_stub.setdefault("website_analysis_mode", "full")
        icp_preferred = _slug_part(label or display, fallback=campaign_id)
        icp_id = unique_store_id(data_root / "clients" / client_id / "icps", icp_preferred)
        save_icp(
            data_root,
            client_id,
            icp_id,
            icp_data=icp_stub,
            display_name=display,
            brief=brief,
            campaign_type=ctype,
            client_name=client_name,
        )

    campaign_meta = {
        "name": display,
        "display_name": display,
        "client_id": client_id,
        "client_name": client_name,
        "campaign_type": ctype,
        "brief": brief,
        "icp_id": icp_id,
    }
    _write_json_file(path / "campaign.json", campaign_meta)
    snapshot_icp_into_campaign(data_root, client_id, icp_id, path)
    _write_json_file(path / "seed_sources.json", {"campaign_id": campaign_id, "sources": []})
    plan = default_plan(campaign_id, campaign_type=ctype)
    save_plan(path, plan)

    # Legacy path for scripts that still open data/campaigns/{id}
    legacy = _campaigns_root() / campaign_id
    if not legacy.exists():
        _try_junction(legacy, path)

    return {"ok": True, "campaign": campaign_summary(path), "icp_id": icp_id}


@router.get("/api/campaigns/{campaign_id}")
async def builder_get_campaign(campaign_id: str, _: None = Depends(_check_access)):
    return campaign_summary(_campaign_dir(campaign_id))


@router.post("/api/campaigns/{campaign_id}/estimate")
async def builder_estimate(
    campaign_id: str,
    body: EstimateRequest,
    _: None = Depends(_check_access),
):
    path = _campaign_dir(campaign_id)
    base = load_plan(path, campaign_id)
    locked_type = base.get("campaign_type") or "company_outreach"
    incoming = dict(body.plan or {})
    # campaign_type is set at ICP/campaign creation and cannot change later
    incoming["campaign_type"] = locked_type
    merged = {**base, **incoming, "campaign_id": campaign_id}
    if "modules" in (body.plan or {}):
        modules = dict(base.get("modules") or {})
        modules.update(body.plan.get("modules") or {})
        merged["modules"] = modules
    plan = normalize_plan(merged, campaign_id=campaign_id)
    return estimate_plan(plan)


@router.put("/api/campaigns/{campaign_id}/plan")
async def builder_save_plan(
    campaign_id: str,
    body: PlanPutRequest,
    _: None = Depends(_check_access),
):
    path = _campaign_dir(campaign_id)
    existing = load_plan(path, campaign_id)
    locked_type = existing.get("campaign_type") or "company_outreach"
    incoming = dict(body.plan or {})
    incoming["campaign_type"] = locked_type
    plan = normalize_plan({**incoming, "campaign_id": campaign_id}, campaign_id=campaign_id)
    errors = validate_plan(plan)
    if errors:
        raise HTTPException(status_code=400, detail=errors)
    try:
        saved = save_plan(path, plan)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    sync_campaign_config_to_client(path)
    return {"ok": True, "plan": saved}


def _read_json_file(path: Path, default: dict[str, Any] | list[Any]) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Invalid JSON in {path.name}: {exc}") from exc


def _write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class JsonDocPut(BaseModel):
    data: Any


@router.get("/api/campaigns/{campaign_id}/icp")
async def builder_get_icp(campaign_id: str, _: None = Depends(_check_access)):
    path = _campaign_dir(campaign_id)
    return {
        "campaign_id": campaign_id,
        "path": "icp.json",
        "data": _read_json_file(path / "icp.json", {}),
        "help": {
            "no_llm": True,
            "intake_template": "docs/ICP-INTAKE-TEMPLATE.md",
            "operator_note": (
                "Edit deterministic ICP rules here (local disk). "
                "Prefer Cursor planning chat (icp-draft skill / Copy planning prompt) "
                "to build Automata-depth icp.json — never Gemini/OpenAI/Apollo for ICP setup."
            ),
        },
    }


@router.put("/api/campaigns/{campaign_id}/meta")
async def builder_put_campaign_meta(
    campaign_id: str,
    body: CampaignMetaPut,
    _: None = Depends(_check_access),
):
    """Rename campaign / ICP display label and/or update brief (folder id unchanged)."""
    path = _campaign_dir(campaign_id)
    name = (body.display_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="display_name is required")
    try:
        meta = set_campaign_display_name(path, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dirty = False
    if (body.client_name or "").strip():
        meta["client_name"] = body.client_name.strip()
        dirty = True
    if body.brief is not None:
        meta["brief"] = body.brief
        dirty = True
    if dirty:
        _write_json_file(path / "campaign.json", meta)
        sync_campaign_config_to_client(path)
    return {"ok": True, "campaign": campaign_summary(path)}


@router.put("/api/campaigns/{campaign_id}/icp")
async def builder_put_icp(
    campaign_id: str,
    body: JsonDocPut,
    _: None = Depends(_check_access),
):
    if not isinstance(body.data, dict):
        raise HTTPException(status_code=400, detail="icp.json must be a JSON object")
    camp = _campaign_dir(campaign_id)
    meta = _read_json_file(camp / "campaign.json", {})
    if not isinstance(meta, dict):
        meta = {}
    client_id = (meta.get("client_id") or "").strip()
    data = dict(body.data)
    data.setdefault("campaign_id", campaign_id)
    icp_id = (meta.get("icp_id") or data.get("icp_id") or "").strip()
    if client_id:
        if not icp_id:
            icp_id = extract_icp_from_campaign(_data_root(), camp) or ""
        if icp_id:
            data["icp_id"] = icp_id
            save_icp(
                _data_root(),
                client_id,
                icp_id,
                icp_data=data,
                display_name=str(meta.get("display_name") or meta.get("name") or icp_id),
                brief=str(meta.get("brief") or ""),
                campaign_type=str(meta.get("campaign_type") or "company_outreach"),
                client_name=str(meta.get("client_name") or client_id),
            )
            meta["icp_id"] = icp_id
            _write_json_file(camp / "campaign.json", meta)
            snapshot_icp_into_campaign(_data_root(), client_id, icp_id, camp)
        else:
            _write_json_file(camp / "icp.json", data)
    else:
        _write_json_file(camp / "icp.json", data)
    sync_campaign_config_to_client(camp)
    return {
        "ok": True,
        "path": "icp.json",
        "icp_id": icp_id or None,
        "client_mirror": f"clients/{client_id}/icps/{icp_id}/" if client_id and icp_id else None,
        "data": data,
    }


@router.get("/api/campaigns/{campaign_id}/seed_sources")
async def builder_get_seeds(campaign_id: str, _: None = Depends(_check_access)):
    path = _campaign_dir(campaign_id)
    return {
        "campaign_id": campaign_id,
        "path": "seed_sources.json",
        "data": _read_json_file(path / "seed_sources.json", {"sources": []}),
        "help": {
            "no_llm": True,
            "registered_types": ["csv_ingest", "linkedin_jobs", "linkedin_people (talent, planned)"],
            "operator_note": (
                "Choose how seeds enter the funnel. csv_ingest is free and precise; "
                "linkedin_jobs spends Apify. Identify sources with the IDE agent from "
                "directories / notes — no in-app LLM."
            ),
        },
    }


@router.put("/api/campaigns/{campaign_id}/seed_sources")
async def builder_put_seeds(
    campaign_id: str,
    body: JsonDocPut,
    _: None = Depends(_check_access),
):
    if not isinstance(body.data, (dict, list)):
        raise HTTPException(status_code=400, detail="seed_sources.json must be JSON object or array")
    camp = _campaign_dir(campaign_id)
    path = camp / "seed_sources.json"
    _write_json_file(path, body.data)
    sync_campaign_config_to_client(camp)
    return {"ok": True, "path": "seed_sources.json", "data": body.data}
