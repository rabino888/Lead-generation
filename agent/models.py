"""
Pydantic models for the Lead Generation Agent.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class InputMode(str, Enum):
    CURATED_SEEDS = "curated_seeds"
    APOLLO_CSV = "apollo_csv"


class LeadSource(str, Enum):
    APOLLO = "apollo"
    APIFY = "apify"
    MANUAL = "manual"


class EnrichmentStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class FeedbackOutcome(str, Enum):
    CONVERTED = "converted"
    NOT_INTERESTED = "not_interested"
    WRONG_FIT = "wrong_fit"
    NO_RESPONSE = "no_response"


# ── ICP & Client ──────────────────────────────────────────────────────────────

class ICPProfile(BaseModel):
    """Structured Ideal Customer Profile for pipeline /run requests.

    Campaign-level deterministic matching config lives in
    data/campaigns/{campaign_id}/icp.json (see CampaignIcpConfig).
    """
    industry: Optional[str] = None
    location: Optional[str] = None
    company_size_min: Optional[int] = None
    company_size_max: Optional[int] = None
    job_titles: list[str] = Field(default_factory=lambda: ["CEO", "Founder", "Director", "Manager"])
    # Where the decision-maker PERSON must be located (Apollo person_locations[]).
    # Without this, Apollo matches titles globally — a Spain-hub campaign can get
    # a Philippines-based director at the same company. Empty = no filter.
    contact_locations: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    prefilter_threshold: int = Field(default=40, ge=0, le=100)
    # Website enrich: "full" = tech/SEO/blog + hiring; "hiring_first" = careers + open roles primary.
    website_analysis_mode: str = "full"


class SenderProfile(BaseModel):
    """Business context for the person/company requesting leads."""
    client_id: str = "local"
    name: str = "Local Client"
    client_email: Optional[str] = None
    business_description: Optional[str] = None
    core_offer: Optional[str] = None
    services: list[str] = Field(default_factory=list)
    proof_points: list[str] = Field(default_factory=list)
    target_pain_points: list[str] = Field(default_factory=list)
    positioning_notes: Optional[str] = None


class ClientProfile(BaseModel):
    """Client context for a run."""
    client_id: str
    name: str
    api_key: str = "local"
    client_email: Optional[str] = None   # shared with this email as viewer on their Drive folder
    icp: ICPProfile = Field(default_factory=ICPProfile)
    sender: SenderProfile = Field(default_factory=SenderProfile)
    active: bool = True


class CompanySeed(BaseModel):
    """Curated company seed that skips website lookup."""
    company_name: str
    website: Optional[str] = None
    industry: Optional[str] = None
    location: Optional[str] = None
    company_size: Optional[str] = None
    company_linkedin_url: Optional[str] = None
    source_url: Optional[str] = None
    notes: Optional[str] = None


# ── Run Request ───────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    """Body for POST /run (curated seeds or Apollo CSV import only)."""
    mode: InputMode = InputMode.CURATED_SEEDS

    icp: Optional[ICPProfile] = None
    sender: Optional[SenderProfile] = None

    # Mode: curated_seeds
    company_seeds: Optional[list[CompanySeed]] = None
    company_seeds_csv: Optional[str] = None

    # Mode: apollo_csv
    apollo_csv_path: Optional[str] = None

    max_leads: int = Field(default=50, ge=1, le=500)
    output_sheet_name: Optional[str] = None


# ── Raw Company (post-discovery, pre-enrichment) ──────────────────────────────

class ApolloSignals(BaseModel):
    """Apollo intelligence signals — premium features, may be empty on basic plan."""
    intent_topics: list[str] = Field(default_factory=list)
    intent_strength: Optional[str] = None           # low | medium | high
    technologies_used: list[str] = Field(default_factory=list)
    funding_round: Optional[str] = None
    hiring_signals: list[str] = Field(default_factory=list)
    growth_signals: Optional[str] = None
    job_change_alert: bool = False
    apollo_lead_score: Optional[int] = None


class RawCompany(BaseModel):
    """Company record after discovery, before full enrichment."""
    apollo_org_id: Optional[str] = None
    company_name: str
    website: Optional[str] = None
    industry: Optional[str] = None
    location: Optional[str] = None
    company_size: Optional[str] = None
    founded_year: Optional[int] = None
    company_linkedin_url: Optional[str] = None
    lead_source: LeadSource = LeadSource.APOLLO
    apollo_signals: ApolloSignals = Field(default_factory=ApolloSignals)
    prefilter_score: Optional[int] = None


# ── Website Analysis ──────────────────────────────────────────────────────────

class WebsiteAnalysis(BaseModel):
    """Output of website enrichment — Firecrawl crawl + LLM JSON mapped to these fields."""
    tech_stack_detected: list[str] = Field(default_factory=list)
    services_offered: list[str] = Field(default_factory=list)
    content_quality_score: Optional[int] = Field(default=None, ge=1, le=10)
    seo_health: list[str] = Field(default_factory=list)
    has_chatbot: Optional[bool] = None
    has_blog: Optional[bool] = None
    last_blog_post_date: Optional[str] = None
    social_proof: Optional[bool] = None
    website_summary: Optional[str] = None
    raw_markdown_length: Optional[int] = None
    website_is_hiring: Optional[str] = None  # yes | no | None
    website_hiring_signals: list[str] = Field(default_factory=list)
    website_open_roles: list[str] = Field(default_factory=list)
    website_careers_url: Optional[str] = None


# ── Contact ───────────────────────────────────────────────────────────────────

class Contact(BaseModel):
    """Decision-maker contact from Apollo enrichment."""
    apollo_person_id: Optional[str] = None
    name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    email_status: Optional[str] = None
    location: Optional[str] = None  # the person's own location (city, country) from Apollo
    linkedin_url: Optional[str] = None
    direct_phone: Optional[str] = None
    mobile_phone: Optional[str] = None
    linkedin_posts: list[dict[str, Any]] = Field(default_factory=list)
    linkedin_interests: list[str] = Field(default_factory=list)
    linkedin_post_summary: Optional[str] = None
    linkedin_about: Optional[str] = None
    linkedin_headline: Optional[str] = None
    is_hiring: Optional[str] = None  # yes | no | None


# ── Full Lead Record ──────────────────────────────────────────────────────────

class Lead(BaseModel):
    """Complete lead record — all stages combined."""
    lead_id: str

    # Discovery
    apollo_org_id: Optional[str] = None
    company_name: str
    website: Optional[str] = None
    industry: Optional[str] = None
    location: Optional[str] = None
    company_size: Optional[str] = None
    founded_year: Optional[int] = None
    company_linkedin_url: Optional[str] = None
    company_linkedin_description: Optional[str] = None
    company_is_hiring: Optional[str] = None  # yes | no | None
    company_open_jobs_count: Optional[int] = None
    company_open_jobs_summary: Optional[str] = None
    lead_source: LeadSource = LeadSource.APOLLO
    apollo_signals: ApolloSignals = Field(default_factory=ApolloSignals)

    # Contacts
    decision_maker: Optional[Contact] = None
    company_phone: Optional[str] = None
    company_generic_email: Optional[str] = None

    # Website analysis
    website_analysis: Optional[WebsiteAnalysis] = None

    # Qualification / scoring
    icp_match_score: Optional[int] = Field(default=None, ge=0, le=100)
    lead_score: Optional[int] = Field(default=None, ge=0, le=100)
    # Deterministic path: "keyword_overlap". Future: "llm_pain_point".
    score_method: Optional[str] = None
    matched_terms: list[str] = Field(default_factory=list)
    score_evidence: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    pain_point_evidence: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    qualification_notes: Optional[str] = None
    # Deterministic keyword-overlap scorer (agent/utils/keyword_score.py)
    matched_terms: list[str] = Field(default_factory=list)
    score_evidence: list[str] = Field(default_factory=list)
    score_method: Optional[str] = None
    score_reasons: list[str] = Field(default_factory=list)

    # Cost tracking
    enrichment_cost_usd: Optional[float] = None

    # Meta
    run_id: str
    client_id: str
    input_mode: InputMode
    enrichment_status: EnrichmentStatus = EnrichmentStatus.COMPLETE
    partial_reason: Optional[str] = None
    scraped_at: datetime = Field(default_factory=datetime.utcnow)


# ── Run State ─────────────────────────────────────────────────────────────────

class RunState(BaseModel):
    """Tracked in memory + persisted to JSON on completion."""
    run_id: str
    client_id: str
    status: RunStatus = RunStatus.PENDING
    input_mode: InputMode
    keyword: Optional[str] = None
    max_leads: int = 50
    current_stage: Optional[str] = None
    qualified_count: int = 0
    partial_count: int = 0
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    sheet_url: Optional[str] = None
    csv_path: Optional[str] = None
    recommendations: Optional[str] = None
    error: Optional[str] = None
    # Cost tracking
    apollo_credits_used: int = 0
    firecrawl_pages_crawled: int = 0
    llm_tokens_used: int = 0
    total_cost_usd: Optional[float] = None
    cost_cap_triggered: bool = False
    cost_diagnostic_path: Optional[str] = None


# ── Feedback ──────────────────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    """Body for POST /feedback."""
    run_id: str
    lead_id: str
    outcome: FeedbackOutcome
    notes: Optional[str] = None


class FeedbackEntry(BaseModel):
    """Single feedback record stored in feedback_log.json."""
    run_id: str
    lead_id: str
    outcome: FeedbackOutcome
    notes: Optional[str] = None
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    # Snapshot of key lead signals for future model training
    lead_score: Optional[int] = None
    icp_match_score: Optional[int] = None
    industry: Optional[str] = None
    company_size: Optional[str] = None
    apollo_intent_strength: Optional[str] = None
