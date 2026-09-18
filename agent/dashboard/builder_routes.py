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

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
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
from agent.utils.list_run import (
    ACTIVE_PHASES,
    approve_full_batch,
    assert_can_start_smoke_gate,
    load_list_run,
    start_smoke_gate,
    update_list_run,
)
from agent.utils.client_store import (
    client_campaign_dir,
    client_dir,
    ensure_client,
    extract_icp_from_campaign,
    find_icp_dir,
    icp_summary,
    library_folder,
    list_client_icps,
    load_client_meta,
    load_icp_doc,
    load_icp_meta,
    list_clients,
    profile_folder,
    resolve_campaign_dir,
    save_icp,
    set_campaign_display_name,
    snapshot_icp_into_campaign,
    sync_campaign_config_to_client,
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
    # pending | approved | deferred — confirm step after enrichment modules
    list_build_status: Optional[str] = None


class ListRunStartRequest(BaseModel):
    smoke_leads: int = Field(2, ge=1, le=10)
    target_leads: int = Field(50, ge=1, le=500)


class ListRunApproveRequest(BaseModel):
    confirm: bool = True


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


@router.get("/api/create-prefill")
async def builder_create_prefill(
    client: str = Query(""),
    icp: str = Query(""),
    type: str = Query(""),
    _: None = Depends(_check_access),
):
    """Resolve Generate-campaign form prefill for any client (incl. ICP-only)."""
    from agent.utils.create_prefill import resolve_create_prefill

    return resolve_create_prefill(
        _data_root(),
        client_id=(client or "").strip(),
        icp_id=(icp or "").strip(),
        campaign_type=(type or "").strip(),
    )


@router.get("/api/clients")
async def builder_list_clients(_: None = Depends(_check_access)):
    """Clients on disk (including ICP-only — not derived from campaigns)."""
    items = list_clients(_data_root())
    return {"clients": items, "count": len(items)}


@router.get("/api/clients/{client_id}/icps")
async def builder_list_client_icps(
    client_id: str,
    ready_only: int = Query(0),
    _: None = Depends(_check_access),
):
    cid = (client_id or "").strip()
    if not _CLIENT_ID_RE.match(cid):
        raise HTTPException(status_code=400, detail="Invalid client id")
    items = list_client_icps(_data_root(), cid)
    if ready_only:
        items = [x for x in items if x.get("ready")]
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


class ClientPut(BaseModel):
    client_name: str = ""
    brief: Optional[str] = None


class IcpMetaPut(BaseModel):
    display_name: str = ""
    brief: Optional[str] = None


@router.put("/api/clients/{client_id}")
async def builder_put_client(
    client_id: str,
    body: ClientPut,
    _: None = Depends(_check_access),
):
    """Create/update client.json (name + optional brief for new-client intake)."""
    cid = (client_id or "").strip()
    if not _CLIENT_ID_RE.match(cid):
        raise HTTPException(status_code=400, detail="Invalid client id")
    name = (body.client_name or "").strip()
    ensure_client(_data_root(), cid, name, brief=body.brief)
    try:
        from agent.utils.cost_dashboard import load_and_sync_dashboard_index

        load_and_sync_dashboard_index(_data_root())
    except Exception:
        pass
    return {"ok": True, "client": load_client_meta(_data_root(), cid)}


@router.post("/api/clients/from-source")
async def builder_client_from_source(
    client_name: str = Form(...),
    notes: str = Form(""),
    source_url: str = Form(""),
    file: Optional[UploadFile] = File(None),
    _: None = Depends(_check_access),
):
    """Create a client profile from an uploaded doc and/or public URL (no paid LLM)."""
    from agent.utils.client_intake import create_client_from_source
    from agent.utils.cost_dashboard import load_and_sync_dashboard_index

    name = (client_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="client_name is required")
    url = (source_url or "").strip()
    upload_bytes: Optional[bytes] = None
    upload_name = ""
    if file is not None and (file.filename or "").strip():
        upload_name = Path(file.filename).name
        upload_bytes = await file.read()
        if not upload_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if not upload_bytes and not url and not (notes or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Provide a file, a URL, or notes to create a client profile",
        )
    try:
        result = create_client_from_source(
            _data_root(),
            client_name=name,
            upload_bytes=upload_bytes,
            upload_filename=upload_name,
            source_url=url,
            notes=notes or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Intake failed: {exc}") from exc
    try:
        load_and_sync_dashboard_index(_data_root())
    except Exception:
        pass
    return {"ok": True, **result}


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
    path = find_icp_dir(_data_root(), cid, iid)
    if path is None or not path.is_dir():
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


@router.delete("/api/clients/{client_id}/icps/{icp_id}")
async def builder_delete_client_icp(
    client_id: str,
    icp_id: str,
    force: int = Query(0),
    _: None = Depends(_check_access),
):
    """Delete a library ICP. Blocked when campaigns still reference it (unless force=1)."""
    from agent.utils.client_store import delete_client_icp

    cid = (client_id or "").strip()
    iid = (icp_id or "").strip()
    if not _CLIENT_ID_RE.match(cid) or not _CAMPAIGN_ID_RE.match(iid):
        raise HTTPException(status_code=400, detail="Invalid client or icp id")
    try:
        return delete_client_icp(_data_root(), cid, iid, force=bool(force))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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

    # Prefer request brief; fall back to client.json brief when creating a new ICP
    if not (brief or "").strip():
        brief = str(load_client_meta(data_root, client_id).get("brief") or "")

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

    # Canonical home: data/clients/{client}/{profile}/campaigns/{campaign}
    path = client_campaign_dir(data_root, client_id, campaign_id, campaign_type=ctype)
    if path.exists():
        raise HTTPException(status_code=409, detail=f"Campaign already exists: {campaign_id}")
    path.mkdir(parents=True, exist_ok=False)
    (path / "stages").mkdir(exist_ok=True)

    display = label or client_name or campaign_id
    from agent.utils.brief_icp import parse_brief_to_icp

    if existing_icp:
        if find_icp_dir(data_root, client_id, existing_icp) is None:
            raise HTTPException(status_code=404, detail=f"ICP not found: {existing_icp}")
        from agent.utils.icp_readiness import assess_icp_depth

        lib = load_icp_doc(data_root, client_id, existing_icp)
        meta_icp = load_icp_meta(data_root, client_id, existing_icp)
        depth = assess_icp_depth(
            lib,
            campaign_type=(meta_icp.get("campaign_type") or ctype),
        )
        if not depth["ready"]:
            raise HTTPException(
                status_code=400,
                detail=depth["remedy"]
                or "ICP is incomplete — finish Automata-depth icp.json before creating a list.",
            )
        icp_id = existing_icp
        icp_stub = lib
        if not (brief or "").strip():
            brief = str(meta_icp.get("brief") or "")
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
        from agent.utils.client_store import unique_icp_id

        icp_id = unique_icp_id(
            data_root, client_id, icp_preferred, campaign_type=ctype
        )
        save_icp(
            data_root,
            client_id,
            icp_id,
            icp_data=icp_stub,
            display_name=display,
            brief=brief,
            campaign_type=ctype,
            client_name=client_name,
            create_new=True,
        )

    campaign_meta = {
        "campaign_id": campaign_id,
        "name": display,
        "display_name": display,
        "client_id": client_id,
        "client_name": client_name,
        "campaign_type": ctype,
        "brief": brief,
        "icp_id": icp_id,
        "seed_target": 50,
        "seed_id_prefix": _slug_part(campaign_id, fallback="seed")[:12] or "seed",
        "segment_order": ["default"],
        "segments": {"default": {"target_count": 50}},
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

    try:
        from agent.dashboard.routes import rebuild_dashboard_files

        rebuild_dashboard_files(data_root)
    except Exception:
        pass

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
    """Rename campaign / update brief / list-build confirmation (folder id unchanged)."""
    path = _campaign_dir(campaign_id)
    name = (body.display_name or "").strip()
    meta = _read_json_file(path / "campaign.json", {})
    if not isinstance(meta, dict):
        meta = {}
    if name:
        try:
            meta = set_campaign_display_name(path, name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif not meta.get("display_name") and not meta.get("name"):
        raise HTTPException(status_code=400, detail="display_name is required")
    dirty = bool(name)
    if (body.client_name or "").strip():
        meta["client_name"] = body.client_name.strip()
        dirty = True
    if body.brief is not None:
        meta["brief"] = body.brief
        dirty = True
        # Keep library ICP/ITP meta.json description in sync
        client_id = (meta.get("client_id") or "").strip()
        icp_id = (meta.get("icp_id") or "").strip()
        if client_id and icp_id:
            lib = find_icp_dir(_data_root(), client_id, icp_id)
            if lib is not None:
                icp_meta = load_icp_meta(_data_root(), client_id, icp_id)
                icp_meta["brief"] = body.brief
                _write_json(lib / "meta.json", icp_meta)
    if body.list_build_status is not None:
        status = (body.list_build_status or "").strip().lower()
        if status and status not in ("pending", "approved", "deferred"):
            raise HTTPException(
                status_code=400,
                detail="list_build_status must be pending, approved, or deferred",
            )
        meta["list_build_status"] = status or "pending"
        dirty = True
    if dirty:
        _write_json_file(path / "campaign.json", meta)
        sync_campaign_config_to_client(path)
        try:
            from agent.dashboard.routes import rebuild_dashboard_files

            rebuild_dashboard_files(_data_root())
        except Exception:
            pass
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
    mirror = None
    if client_id and icp_id:
        ctype = str(meta.get("campaign_type") or "company_outreach")
        mirror = (
            f"clients/{client_id}/{profile_folder(ctype)}/"
            f"{library_folder(ctype)}/{icp_id}/"
        )
    return {
        "ok": True,
        "path": "icp.json",
        "icp_id": icp_id or None,
        "client_mirror": mirror,
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
            "registered_types": [
                "csv_ingest",
                "linkedin_companies",
                "google_maps",
                "people_csv_ingest (talent T01)",
                "linkedin_people (talent T01; default harvestapi/linkedin-profile-search)",
            ],
            "operator_note": (
                "Company discovery: csv_ingest (free), linkedin_companies (ICP → company "
                "search + Apify), or google_maps. Talent: people_csv_ingest or "
                "linkedin_people → stages/T01_raw_people.csv. Confirm / Start in /builder "
                "runs the gated talent funnel (T01→T05 smoke, then Approve for full batch)."
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


@router.get("/api/campaigns/{campaign_id}/run")
async def builder_get_list_run(campaign_id: str, _: None = Depends(_check_access)):
    path = _campaign_dir(campaign_id)
    state = load_list_run(path, campaign_id)
    # Detect talent vs company for preview hydration
    ctype = "company_outreach"
    try:
        from agent.utils.talent_list_run import resolve_campaign_type

        ctype = resolve_campaign_type(path, campaign_id)
    except Exception:
        meta = _read_json_file(path / "campaign.json", {})
        if isinstance(meta, dict) and (meta.get("campaign_type") or "").strip():
            ctype = str(meta["campaign_type"]).strip()

    # Backfill seed/smoke previews so dashboard shows list + smoke together
    if not state.get("seed_preview"):
        try:
            if ctype == "talent_search":
                from agent.utils.talent_list_run import people_seed_preview
                from agent.utils.talent_people import STAGE_T01_RAW_PEOPLE

                preview = people_seed_preview(path)
                if preview:
                    state["seed_preview"] = preview
                    counts = dict(state.get("counts") or {})
                    if "seeds" not in counts:
                        t01 = path / "stages" / STAGE_T01_RAW_PEOPLE
                        if t01.is_file():
                            import csv as _csv

                            with t01.open(encoding="utf-8-sig", newline="") as f:
                                counts["seeds"] = sum(
                                    1
                                    for r in _csv.DictReader(f)
                                    if (r.get("linkedin_url") or r.get("full_name") or "").strip()
                                )
                        else:
                            counts["seeds"] = len(preview)
                    state["counts"] = counts
            else:
                from agent.utils.list_run import _seed_preview_rows

                preview = _seed_preview_rows(campaign_id)
                if preview:
                    state["seed_preview"] = preview
                    counts = dict(state.get("counts") or {})
                    if "seeds" not in counts:
                        # Full seed count from stage file when available
                        stages = path / "stages" / "01_raw_seeds.csv"
                        if stages.is_file():
                            import csv as _csv

                            with stages.open(encoding="utf-8-sig", newline="") as f:
                                counts["seeds"] = sum(
                                    1 for r in _csv.DictReader(f)
                                    if (r.get("company_name") or "").strip()
                                )
                        else:
                            counts["seeds"] = len(preview)
                    state["counts"] = counts
        except Exception:
            pass
    if not state.get("smoke_preview"):
        try:
            if ctype == "talent_search":
                from agent.utils.talent_list_run import people_smoke_preview
                from agent.utils.talent_people import (
                    STAGE_T03_ICP_MATCHED,
                    STAGE_T05_APOLLO_CONTACTABLE,
                )

                t05 = path / "stages" / STAGE_T05_APOLLO_CONTACTABLE
                preview = people_smoke_preview(t05) if t05.is_file() else []
                if not preview and (
                    state.get("smoke_run_id") or state.get("phase") in (
                        "awaiting_approval", "completed", "smoking"
                    )
                ):
                    t03 = path / "stages" / STAGE_T03_ICP_MATCHED
                    smoke_n = int(state.get("smoke_leads") or 0) or 2
                    if t03.is_file():
                        import csv as _csv

                        rows: list[dict[str, str]] = []
                        with t03.open(encoding="utf-8-sig", newline="") as f:
                            for i, r in enumerate(_csv.DictReader(f)):
                                if i >= smoke_n:
                                    break
                                rows.append(dict(r))
                        preview = people_smoke_preview(rows)
                if preview:
                    state["smoke_preview"] = preview
                    if t05.is_file():
                        state["smoke_csv_path"] = str(t05)
            else:
                from agent.utils.list_run import (
                    _smoke_leads_preview,
                    _smoke_preview_from_run_log,
                    find_latest_smoke_run_log,
                )

                meta = _read_json_file(path / "campaign.json", {})
                cid = (meta.get("client_id") if isinstance(meta, dict) else None) or ""
                rid = (state.get("smoke_run_id") or "").strip()
                phase = (state.get("phase") or "").strip()
                smoke_active = bool(rid) or phase in (
                    "awaiting_approval",
                    "error",
                    "smoking",
                )
                if smoke_active:
                    candidates = [path / "stages" / "04_smoke_contactable.csv"]
                    if cid and rid:
                        candidates.extend(
                            [
                                Path("data") / "clients" / cid / "smoke" / "runs" / f"{rid}.csv",
                                Path("data") / "clients" / cid / "runs" / f"{rid}.csv",
                                # Legacy mis-routed smoke client (…-smoketest)
                                Path("data")
                                / "clients"
                                / f"{cid}-smoketest"
                                / "runs"
                                / f"{rid}.csv",
                            ]
                        )
                    for cand in candidates:
                        if cand.is_file():
                            state["smoke_preview"] = _smoke_leads_preview(cand)
                            state["smoke_csv_path"] = str(cand)
                            break
                    # Interrupted smoke (no CSV): recover Apollo Qualified/Rejected from log
                    if not state.get("smoke_preview") and cid:
                        import re as _re

                        err = state.get("error") or ""
                        msg = state.get("message") or ""
                        m = _re.search(
                            r"(run_\d{8}_\d{6}_[a-f0-9]+)", f"{err} {msg} {rid}"
                        )
                        log_rid = m.group(1) if m else rid
                        # Only fall back to "latest log" when this campaign is in error/smoking
                        allow_latest = phase in ("error", "smoking")
                        log_path = find_latest_smoke_run_log(
                            cid, run_id=log_rid if log_rid else ("" if not allow_latest else "")
                        )
                        if log_path is None and allow_latest and not log_rid:
                            log_path = find_latest_smoke_run_log(cid)
                        if log_path is not None:
                            preview = _smoke_preview_from_run_log(log_path)
                            if preview:
                                state["smoke_preview"] = preview
                                if not rid:
                                    state["smoke_run_id"] = log_path.stem
                                counts = dict(state.get("counts") or {})
                                counts.setdefault("smoke_seeds", len(preview))
                                counts.setdefault(
                                    "smoke_contactable",
                                    sum(
                                        1
                                        for r in preview
                                        if (r.get("decision_maker_email") or "").strip()
                                        and r.get("decision_maker_email") != "(no email)"
                                    ),
                                )
                                state["counts"] = counts
                    # Zero-contactable / no-log: still surface smoked companies from deduped
                    if not state.get("smoke_preview"):
                        deduped = path / "stages" / "03_deduped.csv"
                        smoke_n = int(state.get("smoke_leads") or 0) or 2
                        if deduped.is_file():
                            import csv as _csv

                            rows = []
                            with deduped.open(encoding="utf-8-sig", newline="") as f:
                                for r in _csv.DictReader(f):
                                    rows.append(
                                        {
                                            "company_name": (
                                                r.get("company_name") or ""
                                            ).strip(),
                                            "website": (r.get("website") or "").strip(),
                                            "decision_maker_name": "",
                                            "decision_maker_email": "(no email)",
                                            "decision_maker_title": "",
                                        }
                                    )
                                    if len(rows) >= smoke_n:
                                        break
                            if rows:
                                state["smoke_preview"] = rows
        except Exception:
            pass
    return {"ok": True, "list_run": state}


@router.post("/api/campaigns/{campaign_id}/run")
async def builder_start_list_run(
    campaign_id: str,
    body: ListRunStartRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(_check_access),
):
    """Start gated run: seeds → ICP/dedupe → paid smoke → awaiting_approval."""
    path = _campaign_dir(campaign_id)
    state = load_list_run(path, campaign_id)
    if state.get("phase") in ACTIVE_PHASES:
        raise HTTPException(
            status_code=409,
            detail=f"Run already in progress (phase={state.get('phase')})",
        )

    try:
        assert_can_start_smoke_gate(campaign_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Persist intent on campaign.json
    meta = _read_json_file(path / "campaign.json", {})
    if not isinstance(meta, dict):
        meta = {}
    meta["list_build_status"] = "approved"
    _write_json_file(path / "campaign.json", meta)
    sync_campaign_config_to_client(path)

    smoke = int(body.smoke_leads)
    target = int(body.target_leads)
    update_list_run(
        path,
        campaign_id=campaign_id,
        phase="seeding",
        smoke_leads=smoke,
        target_leads=target,
        message="Queued — starting seed list…",
        error="",
        smoke_run_id="",
        full_run_id="",
    )

    background_tasks.add_task(
        start_smoke_gate,
        path,
        campaign_id,
        smoke_leads=smoke,
        target_leads=target,
    )
    return {
        "ok": True,
        "started": True,
        "list_run": load_list_run(path, campaign_id),
        "campaign": campaign_summary(path),
    }


@router.post("/api/campaigns/{campaign_id}/run/approve")
async def builder_approve_full_batch(
    campaign_id: str,
    background_tasks: BackgroundTasks,
    body: ListRunApproveRequest = ListRunApproveRequest(),
    _: None = Depends(_check_access),
):
    """After smoke review — run the full batch up to target_leads."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true")
    path = _campaign_dir(campaign_id)
    state = load_list_run(path, campaign_id)
    phase = state.get("phase")
    if phase not in ("awaiting_approval",):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Full batch only after smoke approval (phase={phase}). "
                "Start the gated run first and wait for awaiting_approval."
            ),
        )
    if state.get("phase") in ACTIVE_PHASES:
        raise HTTPException(status_code=409, detail="Run already in progress")

    update_list_run(
        path,
        phase="running_full",
        message="Queued — starting full batch…",
        error="",
    )
    background_tasks.add_task(approve_full_batch, path, campaign_id)
    return {
        "ok": True,
        "started": True,
        "list_run": load_list_run(path, campaign_id),
        "campaign": campaign_summary(path),
    }
