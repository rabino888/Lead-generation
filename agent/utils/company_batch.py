"""
Batch companies by workflow step for mass Apify / API calls.

Input: list of company dicts (stage CSV rows or Lead-like objects).
Output: batches keyed by workflow step, ready for actors like gtgyani (up to 100 URLs/run).

Usage:
  from agent.utils.company_batch import load_companies_from_stage, batch_for_workflow

  companies = load_companies_from_stage(campaign, "apollo_contactable")
  batches = batch_for_workflow(companies, "linkedin_company_jobs", max_batch_size=100)
  for batch in batches:
      urls = batch.linkedin_company_urls()
      # pass urls to get_linkedin_company_jobs_batch(...)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlparse

from agent.utils.campaign_config import CampaignConfig
from agent.utils.icp_rules import stage_path


class WorkflowStep(str, Enum):
    """Funnel stages that gate which companies enter a batch operation."""

    RAW_SEEDS = "raw_seeds"
    ICP_MATCHED = "icp_matched"
    DEDUPED = "deduped"
    PRE_APOLLO = "pre_apollo"
    APOLLO_CONTACTABLE = "apollo_contactable"
    ENRICHED = "enriched"
    SCORED = "scored"
    QA_FLAGS = "qa_flags"

    @classmethod
    def from_stage_key(cls, key: str) -> "WorkflowStep":
        key = (key or "").strip().lower().replace("-", "_")
        aliases = {
            "01": "raw_seeds",
            "02": "icp_matched",
            "03": "deduped",
            "04": "apollo_contactable",
            "05": "enriched",
            "06": "scored",
            "07": "qa_flags",
            "apollo": "apollo_contactable",
            "contactable": "apollo_contactable",
            "linkedin_company_jobs": "apollo_contactable",
            "linkedin_company_profile": "apollo_contactable",
            "website_enrich": "apollo_contactable",
        }
        normalized = aliases.get(key, key)
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Unknown workflow step: {key}")


STAGE_KEY_MAP = {
    WorkflowStep.RAW_SEEDS: "raw_seeds",
    WorkflowStep.ICP_MATCHED: "icp_matched",
    WorkflowStep.DEDUPED: "deduped",
    WorkflowStep.APOLLO_CONTACTABLE: "apollo_contactable",
    WorkflowStep.ENRICHED: "enriched",
    WorkflowStep.SCORED: "scored",
    WorkflowStep.QA_FLAGS: "qa_flags",
}


@dataclass
class CompanyBatchItem:
    """One company row normalized for batch API calls."""

    company_name: str
    website: Optional[str] = None
    company_linkedin_url: Optional[str] = None
    decision_maker_email: Optional[str] = None
    lead_id: Optional[str] = None
    workflow_step: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_row(row: Mapping[str, Any], workflow_step: Optional[str] = None) -> "CompanyBatchItem":
        li = (
            row.get("company_linkedin")
            or row.get("company_linkedin_url")
            or row.get("linkedin_url")
            or ""
        ).strip()
        return CompanyBatchItem(
            company_name=(row.get("company_name") or "").strip(),
            website=(row.get("website") or "").strip() or None,
            company_linkedin_url=li or None,
            decision_maker_email=(row.get("decision_maker_email") or "").strip() or None,
            lead_id=(row.get("lead_id") or "").strip() or None,
            workflow_step=workflow_step,
            extra=dict(row),
        )

    def domain(self) -> str:
        if not self.website:
            return ""
        parsed = urlparse(
            self.website if self.website.startswith("http") else f"https://{self.website}"
        )
        return parsed.netloc.lower().removeprefix("www.")

    def normalized_linkedin_company_url(self) -> Optional[str]:
        url = (self.company_linkedin_url or "").strip()
        if not url:
            return None
        if not url.startswith("http"):
            url = f"https://{url}"
        url = url.split("?")[0].rstrip("/")
        if url.endswith("/jobs"):
            url = url[:-len("/jobs")]
        if "/company/" not in url.lower():
            return None
        return url


@dataclass
class CompanyBatch:
    """A slice of companies for one mass API / Apify actor run."""

    workflow_step: str
    operation: str
    items: list[CompanyBatchItem]
    batch_index: int = 0

    def websites(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in self.items:
            if item.website and item.website not in seen:
                seen.add(item.website)
                out.append(item.website)
        return out

    def linkedin_company_urls(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in self.items:
            url = item.normalized_linkedin_company_url()
            if url and url not in seen:
                seen.add(url)
                out.append(url)
        return out

    def by_linkedin_url(self) -> dict[str, CompanyBatchItem]:
        out: dict[str, CompanyBatchItem] = {}
        for item in self.items:
            url = item.normalized_linkedin_company_url()
            if url:
                out[url] = item
        return out


def _root_domain(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if url.startswith("http") else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def load_companies_from_stage(
    campaign: CampaignConfig,
    stage_key: str,
    *,
    require_email: bool = False,
    require_linkedin: bool = False,
) -> list[CompanyBatchItem]:
    """Read a stage CSV and return normalized batch items."""
    import csv

    step = WorkflowStep.from_stage_key(stage_key)
    csv_key = STAGE_KEY_MAP.get(step)
    if not csv_key:
        raise ValueError(f"No CSV mapping for workflow step {step}")
    path = stage_path(campaign, csv_key)
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    items: list[CompanyBatchItem] = []
    for row in rows:
        if not (row.get("company_name") or "").strip():
            continue
        if require_email and not (row.get("decision_maker_email") or "").strip():
            continue
        item = CompanyBatchItem.from_row(row, workflow_step=step.value)
        if require_linkedin and not item.normalized_linkedin_company_url():
            continue
        items.append(item)
    return items


def chunk_items(
    items: Sequence[CompanyBatchItem],
    max_batch_size: int,
) -> list[list[CompanyBatchItem]]:
    if max_batch_size <= 0:
        return [list(items)]
    chunks: list[list[CompanyBatchItem]] = []
    buf: list[CompanyBatchItem] = []
    for item in items:
        buf.append(item)
        if len(buf) >= max_batch_size:
            chunks.append(buf)
            buf = []
    if buf:
        chunks.append(buf)
    return chunks


def batch_for_workflow(
    items: Sequence[CompanyBatchItem | Mapping[str, Any]],
    operation: str,
    *,
    workflow_step: Optional[str] = None,
    max_batch_size: int = 100,
    unique_by: str = "linkedin_url",
) -> list[CompanyBatch]:
    """
    Group companies into batches for a mass operation.

    operation examples:
      - linkedin_company_jobs (gtgyani batch)
      - linkedin_company_profile (harvestapi batch — future)
      - website_crawl (Apify fallback — future)

    unique_by: dedupe batch members by linkedin_url | domain | company_name
    """
    step = WorkflowStep.from_stage_key(workflow_step or operation)
    normalized: list[CompanyBatchItem] = []
    for raw in items:
        if isinstance(raw, CompanyBatchItem):
            normalized.append(raw)
        else:
            normalized.append(CompanyBatchItem.from_row(raw, workflow_step=step.value))

    seen: set[str] = set()
    deduped: list[CompanyBatchItem] = []
    for item in normalized:
        if unique_by == "linkedin_url":
            key = item.normalized_linkedin_company_url() or ""
        elif unique_by == "domain":
            key = item.domain()
        else:
            key = item.company_name.lower()
        if not key:
            deduped.append(item)
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    chunks = chunk_items(deduped, max_batch_size)
    return [
        CompanyBatch(
            workflow_step=step.value,
            operation=operation,
            items=chunk,
            batch_index=i,
        )
        for i, chunk in enumerate(chunks)
    ]
