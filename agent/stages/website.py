"""
Stage 3 — Website Enrichment (Firecrawl + Claude)
Deep-crawls company websites and extracts structured analysis.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from agent.integrations.firecrawl import crawl_website
from agent.integrations.llm import call_llm
from agent.models import RawCompany, WebsiteAnalysis
from agent.utils.logger import get_run_logger


def run(
    companies: list[RawCompany],
    client_profile,
    run_id: str,
    logger: Optional[logging.Logger] = None,
) -> dict[str, Optional[WebsiteAnalysis]]:
    """
    Returns a dict mapping company_name → WebsiteAnalysis (or None if failed).
    """
    log = logger or get_run_logger(run_id, client_profile.client_id)
    log.info("Stage 3 — Website enrichment (%d companies)", len(companies))

    results: dict[str, Optional[WebsiteAnalysis]] = {}

    for company in companies:
        if not company.website:
            log.info("No website for '%s' — skipping Firecrawl", company.company_name)
            results[company.company_name] = None
            continue

        log.info("Crawling: %s", company.website)
        markdown = crawl_website(company.website)

        if not markdown:
            log.warning("Firecrawl returned nothing for %s", company.website)
            results[company.company_name] = None
            continue

        analysis = _analyse_website(markdown, company.company_name, log)
        results[company.company_name] = analysis

    success = sum(1 for v in results.values() if v is not None)
    log.info("Stage 3 complete — %d/%d websites analysed", success, len(companies))
    return results


def _analyse_website(
    markdown: str,
    company_name: str,
    log: logging.Logger,
) -> Optional[WebsiteAnalysis]:
    """
    Use Claude to extract structured analysis from website Markdown.
    """
    # Truncate to avoid token overflow (keep first ~6000 chars — enough context)
    content = markdown[:6000]

    prompt = f"""Analyse this company website content and extract structured information.

COMPANY: {company_name}

WEBSITE CONTENT (Markdown):
{content}

Extract and return a JSON object with these exact fields:
{{
  "tech_stack_detected": ["list of technologies/platforms detected, e.g. WordPress, Shopify, HubSpot, etc."],
  "services_offered": ["list of main services/products this company offers"],
  "content_quality_score": <integer 1-10, where 10 is excellent>,
  "seo_health": ["list of SEO issues detected, e.g. 'missing meta description', 'no blog', 'thin content'"],
  "has_chatbot": <true/false/null>,
  "has_blog": <true/false>,
  "last_blog_post_date": "<date string if found, else null>",
  "social_proof": <true if testimonials/case studies/reviews are present, false otherwise>,
  "website_summary": "<2-3 sentence summary of what this company does and their online presence>"
}}

Be specific and evidence-based. Only include what you can actually see in the content.
"""

    try:
        data = call_llm(prompt, max_tokens=800, expect_json=True)
        return WebsiteAnalysis(
            tech_stack_detected=data.get("tech_stack_detected") or [],
            services_offered=data.get("services_offered") or [],
            content_quality_score=data.get("content_quality_score"),
            seo_health=data.get("seo_health") or [],
            has_chatbot=data.get("has_chatbot"),
            has_blog=data.get("has_blog"),
            last_blog_post_date=str(data.get("last_blog_post_date")) if data.get("last_blog_post_date") else None,
            social_proof=data.get("social_proof"),
            website_summary=data.get("website_summary"),
            raw_markdown_length=len(markdown),
        )
    except Exception as e:
        log.warning("Website analysis failed for '%s': %s", company_name, e)
        return WebsiteAnalysis(
            website_summary=f"Analysis failed: {str(e)[:100]}",
            raw_markdown_length=len(markdown),
        )
