"""
Campaign configuration loader.

Each campaign lives under data/campaigns/{campaign_id}/:
  campaign.json       — discovery, geo segments, seed targets (no client-specific copy)
  icp.json            — machine-readable ICP for deterministic seed matching
  seed_sources.json   — chosen scrapers/directories and per-source config
  run_payload.json    — sender + icp for pipeline runs
  seeds.csv           — curated company seeds (built or hand-maintained)
  stages/             — funnel stage artifacts (01_raw_seeds.csv … 07_qa_flags.csv)
  curated_supplement.json — optional manual seed additions
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGNS_ROOT = ROOT / "data" / "campaigns"

DEFAULT_MEGA_OUTSOURCER_NAME_PATTERN = (
    r"teleperformance|concentrix|foundever|ttec|alorica|cognizant|genpact|accenture|"
    r"convergys|sykes|wipro|infosys|tata consultancy|hcl\b|tech mahindra|capgemini|"
    r"ibm\b|huawei|softtek|cience|belkins"
)
DEFAULT_MEGA_OUTSOURCER_DOMAINS = [
    "teleperformance.com", "concentrix.com", "foundever.com", "ttec.com",
    "alorica.com", "cognizant.com", "genpact.com", "cience.com", "accenture.com",
]
DEFAULT_EXCLUDED_AGENCY_PATTERN = (
    r"debt collection|recruitment agency|staffing agency|marketing agency|"
    r"software development only|virtual assistant only"
)

SEED_HEADERS = [
    "seed_id", "company_name", "website", "location", "geo_segment",
    "source_url", "status", "notes",
]

ENRICHED_HEADERS = [
    "batch_id", "run_id", "lead_id", "seed_id", "company_name", "website",
    "location", "geo_segment", "industry", "company_size",
    "decision_maker_apollo_person_id", "decision_maker_name", "decision_maker_title",
    "decision_maker_email", "decision_maker_email_status", "decision_maker_linkedin",
    "decision_maker_direct_phone", "decision_maker_mobile_phone",
    "icp_match_score", "lead_score", "pain_points", "qualification_notes",
    "website_summary", "enriched_at",
]

BATCH_LOG_HEADERS = [
    "batch_id", "geo_segment", "run_id", "started_at", "status",
    "seeds_in_batch", "contactable_leads", "sheet_url", "csv_path",
]

# Deterministic funnel stage artifacts — written under data/campaigns/{id}/stages/
STAGE_RAW_SEEDS = "01_raw_seeds.csv"
STAGE_ICP_MATCHED = "02_icp_matched.csv"
STAGE_ICP_REJECTED = "02_icp_rejected.csv"
STAGE_DEDUPED = "03_deduped.csv"
STAGE_PRE_APOLLO_REJECTED = "03_pre_apollo_rejected.csv"
STAGE_APOLLO_CONTACTABLE = "04_apollo_contactable.csv"
STAGE_APOLLO_REJECTED = "04_apollo_rejected.csv"
STAGE_ENRICHED = "05_enriched.csv"
STAGE_SCORED = "06_scored.csv"
STAGE_QA_FLAGS = "07_qa_flags.csv"

STAGE_ARTIFACTS: tuple[str, ...] = (
    STAGE_RAW_SEEDS,
    STAGE_ICP_MATCHED,
    STAGE_ICP_REJECTED,
    STAGE_DEDUPED,
    STAGE_PRE_APOLLO_REJECTED,
    STAGE_APOLLO_CONTACTABLE,
    STAGE_APOLLO_REJECTED,
    STAGE_ENRICHED,
    STAGE_SCORED,
    STAGE_QA_FLAGS,
)

STAGE_REJECT_ARTIFACTS: frozenset[str] = frozenset({
    STAGE_ICP_REJECTED,
    STAGE_PRE_APOLLO_REJECTED,
    STAGE_APOLLO_REJECTED,
})


class ApolloQuery(BaseModel):
    location: str
    keyword: str


class GeoSegment(BaseModel):
    target_count: int = 0
    country_codes: dict[str, str] = Field(default_factory=dict)
    apollo_queries: list[ApolloQuery] = Field(default_factory=list)
    clutch_location_hints: list[str] = Field(default_factory=list)


class DiscoveryConfig(BaseModel):
    """
    Industry-agnostic discovery config — all industry-specific values
    (industries, keywords, name-signal patterns) belong in campaign.json,
    not in code defaults.
    """
    apollo_employee_ranges: list[str] = Field(default_factory=lambda: ["50,2000"])
    apollo_industries: list[str] = Field(default_factory=list)
    apollo_not_keywords: str = ""
    exclude_mega_outsourcers: bool = False
    excluded_domains: list[str] = Field(default_factory=list)
    excluded_industries: list[str] = Field(default_factory=list)
    excluded_website_suffixes: list[str] = Field(default_factory=list)
    excluded_name_patterns: str = ""
    # Require company name/industry to match name_signal_pattern to become a seed.
    # Legacy key "bpo_name_signal_required" still accepted; when the flag is on
    # but no pattern is set, the legacy BPO pattern applies for backwards compat.
    name_signal_required: bool = Field(
        default=False,
        validation_alias=AliasChoices("name_signal_required", "bpo_name_signal_required"),
    )
    name_signal_pattern: str = ""
    clutch_cache_glob: str | None = None

    def name_signal_re(self) -> re.Pattern[str] | None:
        """Compiled name-signal regex, or None when no filtering is required."""
        if not self.name_signal_required:
            return None
        if not (self.name_signal_pattern or "").strip():
            raise ValueError(
                "name_signal_required is true but name_signal_pattern is empty. "
                "Set discovery.name_signal_pattern in campaign.json (no BPO default)."
            )
        return re.compile(self.name_signal_pattern, re.I)

    def merged_excluded_name_pattern(self) -> str:
        parts = [self.excluded_name_patterns]
        if self.exclude_mega_outsourcers:
            parts.append(DEFAULT_MEGA_OUTSOURCER_NAME_PATTERN)
        return "|".join(f"({p})" for p in parts if p)

    def merged_excluded_domains(self) -> set[str]:
        domains = set(self.excluded_domains)
        if self.exclude_mega_outsourcers:
            domains.update(DEFAULT_MEGA_OUTSOURCER_DOMAINS)
        return domains


class RequiredSignals(BaseModel):
    """Name/industry patterns a seed must match before ICP acceptance."""
    name_signal_required: bool = Field(
        default=False,
        validation_alias=AliasChoices("name_signal_required", "bpo_name_signal_required"),
    )
    name_signal_pattern: str = ""

    def name_signal_re(self) -> re.Pattern[str] | None:
        if not self.name_signal_required:
            return None
        if not (self.name_signal_pattern or "").strip():
            raise ValueError(
                "required_signals.name_signal_required is true but name_signal_pattern "
                "is empty. Set the pattern in icp.json (no industry default)."
            )
        return re.compile(self.name_signal_pattern, re.I)


class CampaignGeo(BaseModel):
    """Geography for deterministic company matching."""
    primary_location: str = ""
    country_codes: dict[str, str] = Field(default_factory=dict)
    location_hints: list[str] = Field(default_factory=list)
    segments: list[str] = Field(default_factory=list)


class CampaignIcpConfig(BaseModel):
    """
    Machine-readable ICP for deterministic seed matching.
    Canonical source: data/campaigns/{campaign_id}/icp.json
    """
    schema_version: str = "1"
    campaign_id: str

    industries: list[str] = Field(default_factory=list)
    excluded_industries: list[str] = Field(default_factory=list)
    geo: CampaignGeo = Field(default_factory=CampaignGeo)
    company_size_min: int | None = None
    company_size_max: int | None = None
    employee_ranges: list[str] = Field(default_factory=list)

    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    excluded_name_patterns: str = ""
    excluded_domains: list[str] = Field(default_factory=list)
    excluded_website_suffixes: list[str] = Field(default_factory=list)
    exclude_mega_outsourcers: bool = False

    required_signals: RequiredSignals = Field(default_factory=RequiredSignals)

    job_titles: list[str] = Field(default_factory=list)
    contact_locations: list[str] = Field(default_factory=list)

    prefilter_threshold: int = Field(default=40, ge=0, le=100)
    # Opt-in weak-seed heuristics (off by default — campaign-agnostic)
    apply_weak_seed_checks: bool = False
    weak_seed_rule_pack: str = ""
    weak_seed_extra_patterns: list[str] = Field(default_factory=list)
    # Pitch-intelligence campaigns: careers probe + open roles over SEO/blog/chatbot LLM.
    website_analysis_mode: str = "full"

    def merged_excluded_name_pattern(self) -> str:
        parts = [self.excluded_name_patterns]
        if self.exclude_mega_outsourcers:
            parts.append(DEFAULT_MEGA_OUTSOURCER_NAME_PATTERN)
        return "|".join(f"({p})" for p in parts if p)

    def merged_excluded_domains(self) -> set[str]:
        domains = set(self.excluded_domains)
        if self.exclude_mega_outsourcers:
            domains.update(DEFAULT_MEGA_OUTSOURCER_DOMAINS)
        return domains

    def to_icp_profile(self):
        """Convert to runtime ICPProfile for pipeline /run requests."""
        from agent.models import ICPProfile

        primary_industry = self.industries[0] if self.industries else None
        return ICPProfile(
            industry=primary_industry,
            location=self.geo.primary_location or None,
            company_size_min=self.company_size_min,
            company_size_max=self.company_size_max,
            job_titles=list(self.job_titles),
            contact_locations=list(self.contact_locations),
            keywords=list(self.include_keywords),
            excluded_keywords=list(self.exclude_keywords),
            prefilter_threshold=self.prefilter_threshold,
            website_analysis_mode=self.website_analysis_mode or "full",
        )


class SeedSourceLastRun(BaseModel):
    run_id: str | None = None
    at: str | None = None
    seeds_added: int | None = None
    notes: str = ""


class SeedSourceConfig(BaseModel):
    id: str
    type: str
    name: str
    why_chosen: str = ""
    enabled: bool = True
    tier: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    last_run_ref: SeedSourceLastRun | None = None


class SeedSourcesConfig(BaseModel):
    """Chosen seed scrapers/directories for a campaign."""
    schema_version: str = "1"
    campaign_id: str
    sources: list[SeedSourceConfig] = Field(default_factory=list)

    def enabled_sources(self) -> list[SeedSourceConfig]:
        return [s for s in self.sources if s.enabled]

    def source_by_id(self, source_id: str) -> SeedSourceConfig | None:
        for source in self.sources:
            if source.id == source_id:
                return source
        return None


class CampaignConfig(BaseModel):
    campaign_id: str
    client_id: str
    client_name: str
    display_name: str
    seed_target: int = 50
    seed_id_prefix: str
    segment_order: list[str]
    segments: dict[str, GeoSegment]
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)

    @property
    def campaign_dir(self) -> Path:
        return CAMPAIGNS_ROOT / self.campaign_id

    def path(self, filename: str) -> Path:
        return self.campaign_dir / filename

    @property
    def seeds_csv(self) -> Path:
        return self.path("seeds.csv")

    @property
    def run_payload_path(self) -> Path:
        return self.path("run_payload.json")

    @property
    def campaign_sheet_meta_path(self) -> Path:
        return self.path("campaign_sheet.json")

    @property
    def batch_state_path(self) -> Path:
        return self.path("batch_state.json")

    @property
    def enrichment_lock_path(self) -> Path:
        return self.path(".enrichment.lock")

    @property
    def curated_supplement_path(self) -> Path:
        return self.path("curated_supplement.json")

    @property
    def icp_path(self) -> Path:
        return self.path("icp.json")

    @property
    def seed_sources_path(self) -> Path:
        return self.path("seed_sources.json")

    @property
    def stages_dir(self) -> Path:
        return self.campaign_dir / "stages"

    def stage_artifact_path(self, filename: str) -> Path:
        """Path for a funnel stage CSV under stages/ (use STAGE_* constants)."""
        return self.stages_dir / filename

    def segment_targets(self) -> dict[str, int]:
        return {
            name: self.segments[name].target_count
            for name in self.segment_order
            if name in self.segments
        }

    def all_country_codes(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for seg in self.segments.values():
            merged.update(seg.country_codes)
        return merged

    def infer_segment_from_text(self, text: str) -> str:
        lower = (text or "")[:8000].lower()
        for segment_name in self.segment_order:
            seg = self.segments.get(segment_name)
            if not seg:
                continue
            for hint in seg.clutch_location_hints:
                if hint.lower() in lower:
                    return segment_name
            for country in seg.country_codes:
                if country.lower() in lower:
                    return segment_name
        return ""


def campaigns_root() -> Path:
    return CAMPAIGNS_ROOT


def load_campaign(campaign_id: str) -> CampaignConfig:
    config_path = CAMPAIGNS_ROOT / campaign_id / "campaign.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Campaign config not found: {config_path}\n"
            f"Create data/campaigns/{campaign_id}/campaign.json first."
        )
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if "campaign_id" not in data:
        data["campaign_id"] = campaign_id
    return CampaignConfig(**data)


def load_run_payload(campaign: CampaignConfig) -> dict:
    path = campaign.run_payload_path
    if not path.exists():
        raise FileNotFoundError(
            f"Run payload not found: {path}\n"
            "Add run_payload.json with sender + icp for this campaign."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"{label} not found: {path}\n"
            f"Create data/campaigns/{{campaign_id}}/{path.name} first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_campaign_icp(campaign_id: str) -> CampaignIcpConfig:
    path = CAMPAIGNS_ROOT / campaign_id / "icp.json"
    data = _read_json(path, "Campaign ICP")
    if "campaign_id" not in data:
        data["campaign_id"] = campaign_id
    return CampaignIcpConfig(**data)


def load_icp(campaign: CampaignConfig) -> CampaignIcpConfig:
    data = _read_json(campaign.icp_path, "Campaign ICP")
    if "campaign_id" not in data:
        data["campaign_id"] = campaign.campaign_id
    return CampaignIcpConfig(**data)


def load_seed_sources(campaign_id: str) -> SeedSourcesConfig:
    path = CAMPAIGNS_ROOT / campaign_id / "seed_sources.json"
    data = _read_json(path, "Seed sources")
    if "campaign_id" not in data:
        data["campaign_id"] = campaign_id
    return SeedSourcesConfig(**data)


def load_seed_sources_for(campaign: CampaignConfig) -> SeedSourcesConfig:
    data = _read_json(campaign.seed_sources_path, "Seed sources")
    if "campaign_id" not in data:
        data["campaign_id"] = campaign.campaign_id
    return SeedSourcesConfig(**data)


def compile_excluded_name_re(campaign: CampaignConfig) -> re.Pattern[str]:
    return re.compile(campaign.discovery.merged_excluded_name_pattern(), re.I)


def compile_icp_excluded_name_re(icp: CampaignIcpConfig) -> re.Pattern[str]:
    return re.compile(icp.merged_excluded_name_pattern(), re.I)


LEGACY_BPO_NAME_SIGNAL_PATTERN = (
    r"call\s*center|contact\s*center|bpo|outsourc|customer\s*(service|support|care)|"
    r"business\s*process|offshore|nearshore|telemarket|help\s*desk|atención al cliente|"
    r"centro de contacto"
)
# Deprecated export — do not use as a silent fallback. Kept for reference only.
BPO_SIGNAL = re.compile(LEGACY_BPO_NAME_SIGNAL_PATTERN, re.I)

CLUTCH_BLOCK_RE = re.compile(
    r"### \[([^\]]+)\]\(https://clutch\.co/profile/([a-z0-9-]+)\)"
    r".*?\[Visit Website\]\((https://r\.clutch\.co/redirect[^)]+)\)",
    re.DOTALL,
)
