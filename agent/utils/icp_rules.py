"""
Adapters between campaign icp.json (CampaignIcpConfig) and matcher IcpRules.

Canonical schema: agent.utils.campaign_config.CampaignIcpConfig
Stage path helpers prefer CampaignConfig.stage_artifact_path / STAGE_* constants.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from agent.utils.campaign_config import (
    STAGE_APOLLO_CONTACTABLE,
    STAGE_APOLLO_REJECTED,
    STAGE_DEDUPED,
    STAGE_ENRICHED,
    STAGE_ICP_MATCHED,
    STAGE_ICP_REJECTED,
    STAGE_PRE_APOLLO_REJECTED,
    STAGE_QA_FLAGS,
    STAGE_RAW_SEEDS,
    STAGE_SCORED,
    CampaignConfig,
    CampaignIcpConfig,
    SeedSourceConfig,
    SeedSourceLastRun,
    SeedSourcesConfig,
    load_icp,
    load_run_payload,
    load_seed_sources_for,
)


class IcpRules(BaseModel):
    """Flat rules object consumed by agent.utils.icp_match (no LLM)."""

    industry: Optional[str] = None
    industries: list[str] = Field(default_factory=list)
    location: Optional[str] = None
    location_hints: list[str] = Field(default_factory=list)
    company_size_min: Optional[int] = None
    company_size_max: Optional[int] = None
    keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    excluded_name_patterns: str = ""
    excluded_domains: list[str] = Field(default_factory=list)
    excluded_industries: list[str] = Field(default_factory=list)
    excluded_website_suffixes: list[str] = Field(default_factory=list)
    exclude_mega_outsourcers: bool = False
    name_signal_required: bool = False
    name_signal_pattern: str = ""
    job_titles: list[str] = Field(default_factory=list)
    contact_locations: list[str] = Field(default_factory=list)
    apply_weak_seed_checks: bool = False
    weak_seed_rule_pack: str = ""
    weak_seed_extra_patterns: list[str] = Field(default_factory=list)
    require_include_keywords: bool = False
    require_industry_match: bool = False
    require_location_match: bool = False

    def industries_list(self) -> list[str]:
        out = list(self.industries)
        if self.industry and self.industry not in out:
            out.insert(0, self.industry)
        return out

    def merged_excluded_name_pattern(self) -> str:
        from agent.utils.campaign_config import DEFAULT_MEGA_OUTSOURCER_NAME_PATTERN

        parts = [self.excluded_name_patterns]
        if self.exclude_mega_outsourcers:
            parts.append(DEFAULT_MEGA_OUTSOURCER_NAME_PATTERN)
        return "|".join(f"({p})" for p in parts if p)

    def merged_excluded_domains(self) -> set[str]:
        from agent.utils.campaign_config import DEFAULT_MEGA_OUTSOURCER_DOMAINS

        domains = {d.lower().removeprefix("www.") for d in self.excluded_domains}
        if self.exclude_mega_outsourcers:
            domains.update(DEFAULT_MEGA_OUTSOURCER_DOMAINS)
        return domains

    def name_signal_re(self):
        import re

        if not self.name_signal_required:
            return None
        if not (self.name_signal_pattern or "").strip():
            raise ValueError(
                "name_signal_required is true but name_signal_pattern is empty. "
                "Set required_signals.name_signal_pattern in icp.json (no industry default)."
            )
        return re.compile(self.name_signal_pattern, re.I)


STAGE_FILES = {
    "raw_seeds": STAGE_RAW_SEEDS,
    "icp_matched": STAGE_ICP_MATCHED,
    "icp_rejected": STAGE_ICP_REJECTED,
    "deduped": STAGE_DEDUPED,
    "pre_apollo_rejected": STAGE_PRE_APOLLO_REJECTED,
    "apollo_contactable": STAGE_APOLLO_CONTACTABLE,
    "apollo_rejected": STAGE_APOLLO_REJECTED,
    "enriched": STAGE_ENRICHED,
    "scored": STAGE_SCORED,
    "qa_flags": STAGE_QA_FLAGS,
}


def stages_dir(campaign: CampaignConfig) -> Path:
    return campaign.stages_dir


def stage_path(campaign: CampaignConfig, key: str) -> Path:
    if key not in STAGE_FILES:
        raise KeyError(f"Unknown stage key: {key}. Known: {sorted(STAGE_FILES)}")
    return campaign.stage_artifact_path(STAGE_FILES[key])


def ensure_stages_dir(campaign: CampaignConfig) -> Path:
    path = campaign.stages_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def icp_config_to_rules(icp: CampaignIcpConfig) -> IcpRules:
    return IcpRules(
        industry=icp.industries[0] if icp.industries else None,
        industries=list(icp.industries),
        location=icp.geo.primary_location or None,
        location_hints=list(icp.geo.location_hints),
        company_size_min=icp.company_size_min,
        company_size_max=icp.company_size_max,
        keywords=list(icp.include_keywords),
        excluded_keywords=list(icp.exclude_keywords),
        excluded_name_patterns=icp.excluded_name_patterns or "",
        excluded_domains=list(icp.excluded_domains),
        excluded_industries=list(icp.excluded_industries),
        excluded_website_suffixes=list(icp.excluded_website_suffixes),
        exclude_mega_outsourcers=bool(icp.exclude_mega_outsourcers),
        name_signal_required=bool(icp.required_signals.name_signal_required),
        name_signal_pattern=icp.required_signals.name_signal_pattern or "",
        job_titles=list(icp.job_titles),
        contact_locations=list(icp.contact_locations),
        apply_weak_seed_checks=bool(getattr(icp, "apply_weak_seed_checks", False)),
        weak_seed_rule_pack=getattr(icp, "weak_seed_rule_pack", "") or "",
        weak_seed_extra_patterns=list(getattr(icp, "weak_seed_extra_patterns", None) or []),
        require_include_keywords=False,
        require_industry_match=False,
        require_location_match=False,
    )


def load_icp_rules(campaign: CampaignConfig) -> IcpRules:
    """Load from icp.json via CampaignIcpConfig, or derive from discovery + run_payload."""
    if campaign.icp_path.exists():
        return icp_config_to_rules(load_icp(campaign))
    return derive_icp_rules(campaign)


def derive_icp_rules(campaign: CampaignConfig) -> IcpRules:
    disc = campaign.discovery
    payload: dict[str, Any] = {}
    try:
        payload = load_run_payload(campaign)
    except FileNotFoundError:
        pass
    icp = payload.get("icp") or {}

    location_hints: list[str] = []
    for seg in campaign.segments.values():
        location_hints.extend(seg.clutch_location_hints)
        location_hints.extend(seg.country_codes.keys())

    return IcpRules(
        industry=icp.get("industry"),
        industries=list(disc.apollo_industries or []),
        location=icp.get("location"),
        location_hints=sorted(set(h for h in location_hints if h)),
        company_size_min=icp.get("company_size_min"),
        company_size_max=icp.get("company_size_max"),
        keywords=list(icp.get("keywords") or []),
        excluded_keywords=list(icp.get("excluded_keywords") or []),
        excluded_name_patterns=disc.excluded_name_patterns or "",
        excluded_domains=list(disc.excluded_domains or []),
        excluded_industries=list(disc.excluded_industries or []),
        excluded_website_suffixes=list(disc.excluded_website_suffixes or []),
        exclude_mega_outsourcers=bool(disc.exclude_mega_outsourcers),
        name_signal_required=bool(disc.name_signal_required),
        name_signal_pattern=disc.name_signal_pattern or "",
        job_titles=list(icp.get("job_titles") or []),
        contact_locations=list(icp.get("contact_locations") or []),
        apply_weak_seed_checks=False,
    )


def load_seed_sources(campaign: CampaignConfig) -> SeedSourcesConfig:
    """Load seed_sources.json; return empty config if missing."""
    if not campaign.seed_sources_path.exists():
        return SeedSourcesConfig(campaign_id=campaign.campaign_id, sources=[])
    return load_seed_sources_for(campaign)


def save_seed_sources(campaign: CampaignConfig, config: SeedSourcesConfig) -> None:
    path = campaign.seed_sources_path
    path.write_text(
        json.dumps(config.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )


def touch_seed_source_run(
    campaign: CampaignConfig,
    source_id: str,
    *,
    ref: str,
    seeds_added: int | None = None,
    why_chosen: str = "",
    source_type: str | None = None,
    config_update: dict[str, Any] | None = None,
) -> None:
    """Update last_run_ref for a source; create entry if missing."""
    from datetime import datetime, timezone

    cfg = load_seed_sources(campaign)
    cfg.campaign_id = campaign.campaign_id
    entry = cfg.source_by_id(source_id)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if entry is None:
        entry = SeedSourceConfig(
            id=source_id,
            type=source_type or source_id,
            name=source_id,
            why_chosen=why_chosen,
        )
        cfg.sources.append(entry)
    if why_chosen and not entry.why_chosen:
        entry.why_chosen = why_chosen
    if config_update:
        entry.config = {**entry.config, **config_update}
    entry.last_run_ref = SeedSourceLastRun(
        run_id=None,
        at=now,
        seeds_added=seeds_added,
        notes=ref,
    )
    save_seed_sources(campaign, cfg)
