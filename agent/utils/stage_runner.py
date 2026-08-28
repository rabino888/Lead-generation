"""
Shared helpers for independently runnable funnel stage CLIs.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from agent.models import (
    ClientProfile,
    CompanySeed,
    ICPProfile,
    Lead,
    LeadSource,
    RawCompany,
    SenderProfile,
    ApolloSignals,
)
from agent.utils.campaign_config import CampaignConfig, load_campaign, load_icp, load_run_payload
from agent.utils.icp_rules import load_icp_rules, stage_path
from agent.utils.stage_artifacts import read_stage_csv, write_stage_csv


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def resolve_input_csv(
    campaign: CampaignConfig,
    *,
    input_path: str | None,
    default_stage_key: str,
) -> Path:
    if input_path:
        path = Path(input_path)
        if not path.exists():
            raise FileNotFoundError(f"Input CSV not found: {path}")
        return path
    default = stage_path(campaign, default_stage_key)
    if not default.exists():
        raise FileNotFoundError(
            f"Default stage input missing: {default}\n"
            f"Pass --input PATH or run the previous stage first."
        )
    return default


def rows_to_company_seeds(rows: list[dict[str, Any]]) -> list[CompanySeed]:
    seeds: list[CompanySeed] = []
    for row in rows:
        name = (row.get("company_name") or "").strip()
        if not name:
            continue
        seeds.append(
            CompanySeed(
                company_name=name,
                website=(row.get("website") or "").strip() or None,
                industry=(row.get("industry") or "").strip() or None,
                location=(row.get("location") or "").strip() or None,
                company_size=(row.get("company_size") or "").strip() or None,
                company_linkedin_url=(row.get("company_linkedin_url") or "").strip() or None,
                source_url=(row.get("source_url") or "").strip() or None,
                notes=(row.get("notes") or "").strip() or None,
            )
        )
    return seeds


def rows_to_raw_companies(rows: list[dict[str, Any]], icp: ICPProfile) -> list[RawCompany]:
    out: list[RawCompany] = []
    for seed in rows_to_company_seeds(rows):
        website = seed.website
        if website and not website.startswith("http"):
            website = f"https://{website}"
        out.append(
            RawCompany(
                company_name=seed.company_name,
                website=website,
                industry=seed.industry or icp.industry,
                location=seed.location or icp.location,
                company_size=seed.company_size,
                company_linkedin_url=seed.company_linkedin_url,
                lead_source=LeadSource.MANUAL,
                apollo_signals=ApolloSignals(),
            )
        )
    return out


def load_client_profile(campaign: CampaignConfig, *, client_id_override: str | None = None) -> ClientProfile:
    payload = load_run_payload(campaign)
    sender = SenderProfile(**payload["sender"])
    icp_data = dict(payload.get("icp") or {})
    rules = load_icp_rules(campaign)
    if rules.job_titles:
        icp_data["job_titles"] = rules.job_titles
    if rules.contact_locations:
        icp_data["contact_locations"] = rules.contact_locations
    if campaign.icp_path.exists():
        icp_cfg = load_icp(campaign)
        icp_data.setdefault("website_analysis_mode", icp_cfg.website_analysis_mode)
    icp = ICPProfile(**icp_data)
    if client_id_override:
        sender.client_id = client_id_override
    return ClientProfile(
        client_id=sender.client_id,
        name=sender.name,
        sender=sender,
        icp=icp,
    )


def leads_to_rows(leads: list[Lead]) -> list[dict[str, Any]]:
    from agent.utils.stage_artifacts import lead_to_stage_row

    return [lead_to_stage_row(lead) for lead in leads]


def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return logging.getLogger(name)


def campaign_arg_load(campaign_id: str) -> CampaignConfig:
    return load_campaign(campaign_id)


# Re-export for stage scripts
__all__ = [
    "campaign_arg_load",
    "get_logger",
    "leads_to_rows",
    "load_client_profile",
    "read_csv_rows",
    "read_stage_csv",
    "resolve_input_csv",
    "rows_to_company_seeds",
    "rows_to_raw_companies",
    "write_stage_csv",
]
