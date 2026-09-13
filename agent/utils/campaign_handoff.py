"""
Campaign ICP + sender context for run outputs, cost dashboard, and outreach handoff.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Optional

from agent.utils.campaign_config import CAMPAIGNS_ROOT


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_campaign_output_context(
    campaign_id: str,
    *,
    root: Path | None = None,
) -> Optional[dict[str, Any]]:
    """
    Load Step-1 campaign config: icp.json (machine rules) + run_payload sender/offer.
    Returns None when the campaign directory does not exist.
    """
    campaigns_root = root or CAMPAIGNS_ROOT
    campaign_dir = campaigns_root / campaign_id
    if not campaign_dir.is_dir():
        return None

    icp_path = campaign_dir / "icp.json"
    payload_path = campaign_dir / "run_payload.json"
    icp_data = _read_json_if_exists(icp_path)
    payload = _read_json_if_exists(payload_path)

    geo = icp_data.get("geo") or {}
    return {
        "campaign_id": campaign_id,
        "icp": icp_data,
        "sender": payload.get("sender") or {},
        "run_payload_icp": payload.get("icp") or {},
        "paths": {
            "campaign_dir": str(campaign_dir),
            "icp_json": str(icp_path) if icp_path.exists() else None,
            "run_payload_json": str(payload_path) if payload_path.exists() else None,
        },
        "summary": _build_summary(icp_data, payload.get("sender") or {}, geo),
    }


def _build_summary(
    icp: dict[str, Any],
    sender: dict[str, Any],
    geo: dict[str, Any],
) -> dict[str, Any]:
    size_min = icp.get("company_size_min")
    size_max = icp.get("company_size_max")
    size_band = ""
    if size_min is not None or size_max is not None:
        size_band = f"{size_min or '?'}–{size_max or '?'} employees"

    return {
        "client_name": sender.get("name") or sender.get("client_id"),
        "core_offer": sender.get("core_offer"),
        "primary_geo": geo.get("primary_location") or icp.get("location"),
        "industries": icp.get("industries") or [],
        "excluded_industries": icp.get("excluded_industries") or [],
        "company_size_band": size_band,
        "job_titles": icp.get("job_titles") or [],
        "contact_locations": icp.get("contact_locations") or [],
        "include_keywords": icp.get("include_keywords") or [],
        "exclude_keywords": icp.get("exclude_keywords") or [],
        "website_analysis_mode": icp.get("website_analysis_mode") or "full",
        "services": sender.get("services") or [],
        "target_pain_points": sender.get("target_pain_points") or [],
    }


def _join_list(items: Any) -> str:
    if not items:
        return ""
    if isinstance(items, list):
        return ", ".join(str(x) for x in items if str(x).strip())
    return str(items)


def format_icp_html(context: Optional[dict[str, Any]]) -> str:
    """HTML block for cost_dashboard.html."""
    if not context:
        return ""

    summary = context.get("summary") or {}
    sender = context.get("sender") or {}
    rows = [
        ("Campaign", context.get("campaign_id")),
        ("Client", summary.get("client_name")),
        ("Core offer", summary.get("core_offer") or sender.get("core_offer")),
        ("Primary geo", summary.get("primary_geo")),
        ("Company size", summary.get("company_size_band")),
        ("Industries", _join_list(summary.get("industries"))),
        ("Excluded industries", _join_list(summary.get("excluded_industries"))),
        ("Job titles", _join_list(summary.get("job_titles"))),
        ("Contact locations (DM)", _join_list(summary.get("contact_locations"))),
        ("Include keywords", _join_list(summary.get("include_keywords"))),
        ("Exclude keywords", _join_list(summary.get("exclude_keywords"))),
        ("Website mode", summary.get("website_analysis_mode")),
        ("Services", _join_list(summary.get("services"))),
        ("Target pain points", _join_list(summary.get("target_pain_points"))),
    ]

    body = ""
    for label, value in rows:
        if not value:
            continue
        body += f"""
        <tr>
          <th>{escape(str(label))}</th>
          <td>{escape(str(value))}</td>
        </tr>"""

    paths = context.get("paths") or {}
    payload_path = paths.get("run_payload_json")
    outreach_note = (
        f"Outreach handoff: pass <code>--csv</code> + "
        f"<code>--payload {escape(payload_path)}</code>"
        if payload_path
        else "Outreach handoff: pass run CSV + campaign run_payload.json"
    )

    return f"""
  <h2>Campaign ICP (Step 1 intake)</h2>
  <table>
    <tbody>{body}</tbody>
  </table>
  <p class="meta">{outreach_note}</p>
"""


def format_icp_markdown(context: Optional[dict[str, Any]]) -> list[str]:
    if not context:
        return []
    summary = context.get("summary") or {}
    lines = [
        "## Campaign ICP (Step 1 intake)",
        "",
        f"- Campaign: `{context.get('campaign_id')}`",
    ]
    if summary.get("client_name"):
        lines.append(f"- Client: **{summary['client_name']}**")
    if summary.get("core_offer"):
        lines.append(f"- Core offer: {summary['core_offer']}")
    if summary.get("primary_geo"):
        lines.append(f"- Primary geo: {summary['primary_geo']}")
    if summary.get("company_size_band"):
        lines.append(f"- Company size: {summary['company_size_band']}")
    if summary.get("industries"):
        lines.append(f"- Industries: {_join_list(summary['industries'])}")
    if summary.get("job_titles"):
        lines.append(f"- Job titles: {_join_list(summary['job_titles'])}")
    if summary.get("contact_locations"):
        lines.append(f"- Contact locations: {_join_list(summary['contact_locations'])}")
    if summary.get("website_analysis_mode"):
        lines.append(f"- Website analysis mode: `{summary['website_analysis_mode']}`")
    paths = context.get("paths") or {}
    if paths.get("run_payload_json"):
        lines.append(f"- Outreach payload: `{paths['run_payload_json']}`")
    lines.append("")
    return lines


def build_outreach_handoff(
    *,
    campaign_id: Optional[str],
    run_id: str,
    csv_path: str,
    sender: Optional[dict[str, Any]] = None,
    icp_runtime: Optional[dict[str, Any]] = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """
    JSON artifact for downstream outreach-content-agent (alongside run CSV).
    """
    ctx = load_campaign_output_context(campaign_id, root=root) if campaign_id else None
    payload_path = (ctx or {}).get("paths", {}).get("run_payload_json")

    handoff: dict[str, Any] = {
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "campaign_id": campaign_id,
        "run_id": run_id,
        "leads_csv": csv_path,
        "sender": sender or (ctx or {}).get("sender") or {},
        "icp_machine": (ctx or {}).get("icp") or {},
        "icp_runtime": icp_runtime or (ctx or {}).get("run_payload_icp") or {},
        "outreach_cli": {
            "csv": csv_path,
            "payload": payload_path,
            "hint": (
                "python scripts/cli.py kit propose --campaign {campaign} "
                "--csv {csv} --payload {payload}"
            ).format(
                campaign=campaign_id or "{campaign_id}",
                csv=csv_path,
                payload=payload_path or "data/campaigns/{campaign_id}/run_payload.json",
            ),
        },
    }
    if ctx:
        handoff["paths"] = ctx.get("paths")
        handoff["summary"] = ctx.get("summary")
    return handoff


def write_outreach_handoff(path: Path, handoff: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(handoff, indent=2, default=str), encoding="utf-8")
    return path
