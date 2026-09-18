"""
Modular campaign-create prefill (dashboard Generate campaign → builder form).

Single source of truth for:
  - create URLs (?client=&new=1&icp=&type=)
  - resolving client mode (existing vs new) for ICP-only clients
  - selecting a ready library ICP/ITP
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from agent.utils.client_store import (
    find_icp_dir,
    list_client_icps,
    list_clients,
    load_client_meta,
    load_icp_meta,
)

_VALID_TYPES = frozenset({"company_outreach", "talent_search"})


def normalize_campaign_type(value: Optional[str], *, fallback: str = "company_outreach") -> str:
    raw = (value or "").strip()
    return raw if raw in _VALID_TYPES else fallback


def build_campaign_create_url(
    *,
    client_id: str,
    icp_id: str = "",
    campaign_type: str = "company_outreach",
    path: str = "/builder",
) -> str:
    """Canonical Generate-campaign link used by the client dashboard."""
    cid = (client_id or "").strip()
    if not cid:
        raise ValueError("client_id is required")
    params: dict[str, str] = {
        "client": cid,
        "new": "1",
        "type": normalize_campaign_type(campaign_type),
    }
    iid = (icp_id or "").strip()
    if iid:
        params["icp"] = iid
    return f"{path}?{urlencode(params)}"


def merge_client_options(
    disk_clients: list[dict[str, Any]],
    campaign_rows: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, str]]:
    """
    Union of disk clients + any client_id seen on campaigns.
    Sorted by display name. ICP-only clients are included.
    """
    by_id: dict[str, str] = {}
    for row in disk_clients or []:
        cid = str(row.get("client_id") or "").strip()
        if not cid:
            continue
        by_id[cid] = str(row.get("client_name") or cid)
    for row in campaign_rows or []:
        cid = str(row.get("client_id") or "").strip()
        if not cid:
            continue
        if cid not in by_id:
            by_id[cid] = str(row.get("client_name") or cid)
    return [
        {"client_id": cid, "client_name": by_id[cid]}
        for cid in sorted(by_id.keys(), key=lambda x: by_id[x].lower())
    ]


def resolve_create_prefill(
    root: Path,
    *,
    client_id: str = "",
    icp_id: str = "",
    campaign_type: str = "",
) -> dict[str, Any]:
    """
    Resolve how the builder create form should be prefilled.

    Returns a JSON-serialisable dict consumed by the builder UI.
    """
    root = Path(root)
    cid = (client_id or "").strip()
    iid = (icp_id or "").strip()
    ctype_hint = normalize_campaign_type(campaign_type) if campaign_type else ""

    disk = list_clients(root)
    disk_ids = {c["client_id"] for c in disk}
    client_meta = load_client_meta(root, cid) if cid else {}
    client_name = str(client_meta.get("client_name") or cid or "")
    client_known = bool(cid) and (
        cid in disk_ids or (root / "clients" / cid).is_dir()
    )
    errors: list[str] = []
    warnings: list[str] = []

    if not cid:
        return {
            "ok": False,
            "errors": ["client_id is required"],
            "client_id": "",
            "client_name": "",
            "client_mode": "new",
            "campaign_type": ctype_hint or "company_outreach",
            "icp_mode": "new",
            "icp_id": None,
            "icp_display_name": None,
            "icp_ready": None,
            "suggested_label": "",
            "create_url": "",
            "clients": merge_client_options(disk),
            "ready_icps": [],
        }

    if not client_known:
        warnings.append(f"Client folder not found for {cid!r}; form will use create-new-client mode.")
        create_url = build_campaign_create_url(
            client_id=cid,
            icp_id=iid,
            campaign_type=ctype_hint or "company_outreach",
        )
        return {
            "ok": True,
            "errors": errors,
            "warnings": warnings,
            "client_id": cid,
            "client_name": client_name or cid,
            "client_mode": "new",
            "campaign_type": ctype_hint or "company_outreach",
            "icp_mode": "new",
            "icp_id": None,
            "icp_display_name": None,
            "icp_ready": None,
            "suggested_label": "",
            "create_url": create_url,
            "clients": merge_client_options(disk),
            "ready_icps": [],
        }

    # Prefer type from linked ICP meta when present
    resolved_type = ctype_hint or "company_outreach"
    icp_summary_row: Optional[dict[str, Any]] = None
    ready_icps = [
        {
            "icp_id": row["icp_id"],
            "display_name": row.get("display_name") or row["icp_id"],
            "campaign_type": row.get("campaign_type") or "company_outreach",
            "ready": True,
        }
        for row in list_client_icps(root, cid)
        if row.get("ready") is not False
    ]

    if iid:
        path = find_icp_dir(root, cid, iid)
        if path is None:
            errors.append(f"ICP/ITP not found: {iid}")
        else:
            meta = load_icp_meta(root, cid, iid)
            for row in list_client_icps(root, cid):
                if row.get("icp_id") == iid:
                    icp_summary_row = row
                    break
            resolved_type = normalize_campaign_type(
                (icp_summary_row or {}).get("campaign_type")
                or meta.get("campaign_type")
                or ctype_hint
            )
            ready = True if icp_summary_row is None else (icp_summary_row.get("ready") is not False)
            if not ready:
                errors.append(
                    f"ICP/ITP {iid!r} is incomplete — finish targeting before generating a campaign."
                )
            else:
                icp_summary_row = icp_summary_row or {
                    "icp_id": iid,
                    "display_name": meta.get("display_name") or meta.get("name") or iid,
                    "campaign_type": resolved_type,
                    "ready": True,
                }

    display = None
    suggested = ""
    icp_ready = None
    icp_mode = "new"
    selected_icp = None
    if iid and icp_summary_row and not errors:
        icp_mode = "existing"
        selected_icp = iid
        display = str(icp_summary_row.get("display_name") or iid)
        suggested = f"{display} (new list)"
        icp_ready = True
    elif iid and errors:
        icp_mode = "existing"
        selected_icp = None
        icp_ready = False

    create_url = build_campaign_create_url(
        client_id=cid,
        icp_id=iid if selected_icp else "",
        campaign_type=resolved_type,
    )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "client_id": cid,
        "client_name": client_name or cid,
        "client_mode": "existing",
        "campaign_type": resolved_type,
        "icp_mode": icp_mode,
        "icp_id": selected_icp,
        "icp_display_name": display,
        "icp_ready": icp_ready,
        "suggested_label": suggested,
        "create_url": create_url,
        "clients": merge_client_options(disk),
        "ready_icps": ready_icps,
    }


def assert_prefill_for_all_ready_icps(root: Path) -> dict[str, Any]:
    """
    Operator/test helper: resolve prefill for every ready ICP across all clients.
    Returns {checked, failures:[{client_id, icp_id, errors}]}.
    """
    checked = 0
    failures: list[dict[str, Any]] = []
    for client in list_clients(root):
        cid = client["client_id"]
        for row in list_client_icps(root, cid):
            if row.get("ready") is False:
                continue
            checked += 1
            pre = resolve_create_prefill(
                root,
                client_id=cid,
                icp_id=row["icp_id"],
                campaign_type=str(row.get("campaign_type") or ""),
            )
            if not pre.get("ok") or pre.get("client_mode") != "existing" or pre.get("icp_id") != row["icp_id"]:
                failures.append(
                    {
                        "client_id": cid,
                        "icp_id": row["icp_id"],
                        "errors": pre.get("errors") or ["prefill mismatch"],
                        "prefill": {
                            "ok": pre.get("ok"),
                            "client_mode": pre.get("client_mode"),
                            "icp_id": pre.get("icp_id"),
                        },
                    }
                )
    return {"checked": checked, "failures": failures}
