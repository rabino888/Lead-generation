"""Write the campaign folder the existing funnel already knows how to read."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from agent.utils.client_store import client_campaign_dir
from agent.utils.enrichment_plan import save_plan


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def campaign_dir(data_root: Path, campaign_id: str) -> Path:
    return Path(data_root) / "campaigns" / campaign_id


def write_campaign_files(data_root: Path, plan: dict[str, Any]) -> Path:
    """Refresh campaign JSON from the approved (or draft) plan. Stages are kept."""
    root = Path(data_root)
    campaign_id = plan["campaign_id"]
    client_id = plan["client_id"]
    dest = campaign_dir(root, campaign_id)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "stages").mkdir(exist_ok=True)

    icp = dict(plan.get("icp") or {})
    icp["campaign_id"] = campaign_id
    icp["schema_version"] = str(icp.get("schema_version") or "1")
    _dump(dest / "icp.json", icp)

    seed = plan.get("seed_source") or {}
    seed_doc = {
        "schema_version": "1",
        "campaign_id": campaign_id,
        "sources": [
            {
                "id": seed.get("id"),
                "type": seed.get("type") or seed.get("id"),
                "name": seed.get("id"),
                "why_chosen": seed.get("why") or "",
                "enabled": True,
                "tier": "primary",
                "config": seed.get("config") or {},
                "last_run_ref": None,
            }
        ],
    }
    _dump(dest / "seed_sources.json", seed_doc)

    target = int(plan.get("target_leads") or 25)
    meta = {
        "campaign_id": campaign_id,
        "name": plan.get("company_name") or campaign_id,
        "display_name": plan.get("company_name") or campaign_id,
        "client_id": client_id,
        "client_name": plan.get("company_name") or client_id,
        "campaign_type": "company_outreach",
        "brief": (plan.get("notes") or "")[:4000],
        "icp_id": f"{client_id}_buyers",
        "seed_target": target,
        "seed_id_prefix": (campaign_id.replace("_", "")[:12] or "seed"),
        "segment_order": ["default"],
        "segments": {"default": {"target_count": target}},
        "autopilot": True,
    }
    _dump(dest / "campaign.json", meta)

    offer = plan.get("offer") or {}
    sender = {
        "client_id": client_id,
        "name": plan.get("company_name") or client_id,
        "business_description": (offer.get("description") or "")[:500],
        "core_offer": offer.get("title") or "",
        "services": [offer.get("title")] if offer.get("title") else [],
        "proof_points": [],
        "target_pain_points": [],
        "positioning_notes": (plan.get("outreach_strategy") or {}).get("angle") or "",
    }
    icp_runtime = {
        "industry": (icp.get("industries") or [""])[0],
        "location": (icp.get("geo") or {}).get("primary_location") or "",
        "company_size_min": icp.get("company_size_min"),
        "company_size_max": icp.get("company_size_max"),
        "job_titles": icp.get("job_titles") or [],
        "contact_locations": icp.get("contact_locations") or [],
        "prefilter_threshold": 30,
        "website_analysis_mode": icp.get("website_analysis_mode") or "full",
    }
    _dump(
        dest / "run_payload.json",
        {
            "sender": sender,
            "icp": icp_runtime,
            "mode": "curated_seeds",
            "campaign_id": campaign_id,
        },
    )
    save_plan(
        dest,
        {
            "campaign_id": campaign_id,
            "campaign_type": "company_outreach",
            "target_leads": target,
            "website_crawler": (plan.get("enrichment") or {}).get("website_crawler") or "apify",
            "modules": (plan.get("enrichment") or {}).get("modules") or {},
            "notes": "Written by autopilot. Module flags are not turned off to pass an audit.",
        },
    )
    (dest / "plan.md").write_text(plan.get("markdown") or "", encoding="utf-8")
    _write_client_library(root, plan)
    _mirror_client_campaign(root, client_id, campaign_id, dest)
    return dest


def _write_client_library(data_root: Path, plan: dict[str, Any]) -> None:
    client_id = plan["client_id"]
    base = data_root / "clients" / client_id
    base.mkdir(parents=True, exist_ok=True)
    (base / "company_brief.md").write_text(_brief(plan), encoding="utf-8")
    offer = plan.get("offer") or {}
    icp_id = f"{client_id}_buyers"
    offer_doc = {
        "id": 1,
        "icp_id": icp_id,
        "title": offer.get("title") or "Offer",
        "description": offer.get("description") or "",
        "deliverability_note": offer.get("deliverability_note") or "",
    }
    _dump(base / "offers" / "1.json", offer_doc)
    magnet = dict(offer.get("lead_magnet") or {})
    magnet.setdefault("id", 1)
    magnet.setdefault("offer_id", 1)
    magnet["icp_id"] = icp_id
    if plan.get("approved"):
        if magnet.get("status") == "proposed":
            magnet["status"] = "approved"
    _dump(base / "lead_magnets" / "1.json", magnet)
    icp = dict(plan.get("icp") or {})
    icp_dir = base / "icps" / icp_id
    icp_dir.mkdir(parents=True, exist_ok=True)
    _dump(icp_dir / "icp.json", icp)
    _dump(
        icp_dir / "meta.json",
        {
            "icp_id": icp_id,
            "client_id": client_id,
            "client_name": plan.get("company_name") or client_id,
            "display_name": plan.get("company_name") or client_id,
            "name": icp_id,
            "brief": "Autopilot draft from the company URL.",
            "campaign_type": "company_outreach",
        },
    )


def _brief(plan: dict[str, Any]) -> str:
    offer = plan.get("offer") or {}
    magnet = offer.get("lead_magnet") or {}
    icp = plan.get("icp") or {}
    client_id = plan["client_id"]
    return "\n".join(
        [
            f"# Company brief — {client_id}",
            "",
            "## Sender",
            "",
            f"- Name: {plan.get('company_name')}",
            f"- Site: {plan.get('company_url')}",
            "- Email: [PLACEHOLDER]",
            "",
            "## What they do",
            "",
            offer.get("description") or "(not read)",
            "",
            "## Claims (company-stated)",
            "",
            "Inferred from the public homepage. Treat numbers as unconfirmed.",
            "",
            "## Core paid offer",
            "",
            offer.get("title") or "",
            "",
            "## ICPs",
            "",
            f"| icp_id | audience | offer id | lead magnet ids | proof id |",
            f"| --- | --- | --- | --- | --- |",
            f"| {client_id}_buyers | {', '.join(icp.get('industries') or []) or 'unspecified'} | 1 | 1 | — |",
            "",
            "## Offer by ICP",
            "",
            f"See offers/1.json. {offer.get('description') or ''}",
            "",
            "## Free lead magnets",
            "",
            f"| id | offer_id | icp_id | name | what they get | pain themes |",
            f"| --- | --- | --- | --- | --- | --- |",
            f"| 1 | 1 | {client_id}_buyers | {magnet.get('title')} | {magnet.get('description')} | |",
            "",
            "## Related companies",
            "",
            "None yet.",
            "",
            "## Voice",
            "",
            "Plain, specific, no claims that are not on the site.",
            "",
            "## Compliance",
            "",
            magnet.get("compliance") or "none noted",
            "",
            "## Sources",
            "",
            plan.get("company_url") or "",
            "",
        ]
    )


def _mirror_client_campaign(data_root: Path, client_id: str, campaign_id: str, source: Path) -> None:
    mirror = client_campaign_dir(data_root, client_id, campaign_id)
    if mirror.resolve() == source.resolve():
        return
    mirror.mkdir(parents=True, exist_ok=True)
    for name in (
        "campaign.json",
        "icp.json",
        "seed_sources.json",
        "enrichment_plan.json",
        "run_payload.json",
        "plan.md",
    ):
        src = source / name
        if src.is_file():
            shutil.copy2(src, mirror / name)
    (mirror / "stages").mkdir(exist_ok=True)


def read_disk_overrides(data_root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """If the operator edited icp.json or seed_sources.json, keep those edits."""
    dest = campaign_dir(Path(data_root), plan["campaign_id"])
    updated = json.loads(json.dumps(plan))
    icp_path = dest / "icp.json"
    if icp_path.is_file():
        try:
            disk_icp = json.loads(icp_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            disk_icp = None
        if isinstance(disk_icp, dict) and disk_icp != plan.get("icp"):
            updated["icp"] = disk_icp
    seed_path = dest / "seed_sources.json"
    if seed_path.is_file():
        try:
            disk_seed = json.loads(seed_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            disk_seed = None
        sources = (disk_seed or {}).get("sources") if isinstance(disk_seed, dict) else None
        if sources:
            first = sources[0]
            updated.setdefault("seed_source", {})
            updated["seed_source"]["id"] = first.get("id") or updated["seed_source"].get("id")
            updated["seed_source"]["type"] = first.get("type") or first.get("id")
            if isinstance(first.get("config"), dict):
                updated["seed_source"]["config"] = first["config"]
    plan_path = dest / "enrichment_plan.json"
    if plan_path.is_file():
        try:
            disk_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            disk_plan = None
        modules = (disk_plan or {}).get("modules") if isinstance(disk_plan, dict) else None
        if isinstance(modules, dict):
            updated.setdefault("enrichment", {})["modules"] = modules
    return updated
