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
    KEYWORD = "keyword"
    ICP = "icp"
    COMPANY_LIST = "company_list"


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
    """Structured Ideal Customer Profile."""
    industry: Optional[str] = None
    location: Optional[str] = None
    company_size_min: Optional[int] = None
    company_size_max: Optional[int] = None
    job_titles: list[str] = Field(default_factory=lambda: ["CEO", "Founder", "Director", "Manager"])
    keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    prefilter_threshold: int = Field(default=40, ge=0, le=100)


class ClientProfile(BaseModel):
    """Loaded from the LeadGen Clients Google Sheet."""
    client_id: str
    name: str
    api_key: str
    client_email: Optional[str] = None   # shared with this email as viewer on their Drive folder
    icp: ICPProfile = Field(default_factory=ICPProfile)
    active: bool = True


# ── Run Request ───────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    """Body for POST /run."""
    mode: InputMode = InputMode.KEYWORD

    # Mode: keyword
    keyword: Optional[str] = None

    # Mode: icp
    icp: Optional[ICPProfile] = None

    # Mode: company_list
    company_list: Optional[list[str]] = None

    # Run config
    max_leads: int = Field(default=50, ge=1, le=500)
    output_sheet_name: Optional[str] = None  # defaults to "{date} - {keyword}"


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
    """Output of Stage 3 — Firecrawl + Claude analysis."""
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


# ── Contact ───────────────────────────────────────────────────────────────────

class Contact(BaseModel):
    """Decision-maker contact from Apollo enrichment."""
    name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    direct_phone: Optional[str] = None


# ── Full Lead Record ──────────────────────────────────────────────────────────

class Lead(BaseModel):
    """Complete lead record — all stages combined."""
    lead_id: str

    # Discovery
    company_name: str
    website: Optional[str] = None
    industry: Optional[str] = None
    location: Optional[str] = None
    company_size: Optional[str] = None
    founded_year: Optional[int] = None
    company_linkedin_url: Optional[str] = None
    lead_source: LeadSource = LeadSource.APOLLO
    apollo_signals: ApolloSignals = Field(default_factory=ApolloSignals)

    # Contacts
    decision_maker: Optional[Contact] = None
    company_phone: Optional[str] = None
    company_generic_email: Optional[str] = None

    # Website analysis
    website_analysis: Optional[WebsiteAnalysis] = None

    # Qualification
    icp_match_score: Optional[int] = Field(default=None, ge=0, le=100)
    lead_score: Optional[int] = Field(default=None, ge=0, le=100)
    pain_points: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    qualification_notes: Optional[str] = None

    # Outreach
    personalized_hook: Optional[str] = None
    recommended_first_service: Optional[str] = None

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
