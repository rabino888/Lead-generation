"""
Stage 4 — Website Enrichment (Firecrawl + Gemini/OpenAI website + hiring signals)
Deep-crawls company websites and extracts structured analysis.
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol

from agent.integrations.careers import find_careers_page
from agent.integrations.firecrawl import (
    crawl_website_with_fallback,
    map_website_urls,
    scrape_page,
)
from agent.integrations.llm import WEBSITE_PROVIDERS, call_llm
from agent.models import Lead, WebsiteAnalysis
from agent.utils.concurrency import env_int, map_ordered
from agent.utils.cost_tracker import get_cost_tracker
from agent.utils.logger import get_run_logger


class _HasWebsite(Protocol):
    company_name: str
    website: Optional[str]


def _website_analysis_mode(client_profile) -> str:
    icp = getattr(client_profile, "icp", None)
    mode = getattr(icp, "website_analysis_mode", None) or "full"
    return mode if mode in ("full", "hiring_first") else "full"


def run(
    companies: list[_HasWebsite],
    client_profile,
    run_id: str,
    logger: Optional[logging.Logger] = None,
) -> dict[str, Optional[WebsiteAnalysis]]:
    """
    Returns a dict mapping company_name → WebsiteAnalysis (or None if failed).
    Accepts RawCompany or Lead objects (both expose company_name + website).
    """
    log = logger or get_run_logger(run_id, client_profile.client_id)
    concurrency = env_int("WEBSITE_ENRICHMENT_CONCURRENCY", default=4)
    mode = _website_analysis_mode(client_profile)
    hiring_first = mode == "hiring_first"
    log.info(
        "Stage 4 — Website enrichment (%d companies, concurrency=%d, mode=%s)",
        len(companies),
        concurrency,
        mode,
    )

    def _process(company: _HasWebsite) -> tuple[str, Optional[WebsiteAnalysis]]:
        tracker = get_cost_tracker()
        if tracker and tracker.should_halt():
            log.warning(
                "Cost cap breached — skipping website enrichment for '%s'",
                company.company_name,
            )
            return company.company_name, None

        if not company.website:
            log.info("No website for '%s' — skipping Firecrawl", company.company_name)
            return company.company_name, None

        def _enrich() -> Optional[WebsiteAnalysis]:
            log.info("Mapping site URLs: %s", company.website)
            sitemap_urls = map_website_urls(company.website, limit=80)

            log.info("Crawling: %s", company.website)
            markdown, crawl_source = crawl_website_with_fallback(company.website)
            if not markdown:
                log.warning(
                    "No website content for %s (firecrawl+apify failed) — careers probe only",
                    company.website,
                )
            else:
                log.info(
                    "Website crawl source for %s: %s (%d chars)",
                    company.company_name,
                    crawl_source,
                    len(markdown),
                )

            careers_url = find_careers_page(
                company.website,
                markdown,
                sitemap_urls=sitemap_urls,
            )
            careers_markdown = scrape_page(careers_url) if careers_url else None
            if not careers_markdown and careers_url:
                from agent.integrations.apify import scrape_website_apify

                careers_markdown = scrape_website_apify(careers_url, max_pages=1)

            hiring = _detect_website_hiring(
                markdown or "",
                company.company_name,
                company.website,
                log,
                careers_url=careers_url,
                careers_markdown=careers_markdown,
                hiring_first=hiring_first,
            )

            analysis = None
            if markdown and not hiring_first:
                analysis = _analyse_website(markdown, company.company_name, log)
            elif markdown and hiring_first:
                analysis = _analyse_website_pitch(markdown, company.company_name, log)

            if analysis:
                analysis.website_careers_url = careers_url
                if hiring:
                    analysis.website_is_hiring = hiring.get("is_hiring")
                    analysis.website_hiring_signals = hiring.get("hiring_signals") or []
                    analysis.website_open_roles = hiring.get("open_roles") or []
                    analysis.website_careers_url = careers_url or hiring.get("careers_url")
                return analysis
            if hiring or careers_markdown:
                return WebsiteAnalysis(
                    website_is_hiring=hiring.get("is_hiring") if hiring else None,
                    website_hiring_signals=(hiring.get("hiring_signals") or []) if hiring else [],
                    website_open_roles=(hiring.get("open_roles") or []) if hiring else [],
                    website_careers_url=careers_url or (hiring.get("careers_url") if hiring else None),
                    website_summary=(
                        "Thin site — hiring signals from careers page only"
                    ),
                    raw_markdown_length=len(markdown or ""),
                )
            return None

        if isinstance(company, Lead) and tracker:
            with tracker.lead_context(company.lead_id, company.company_name):
                analysis = _enrich()
                record = tracker.accumulate_lead_cost(
                    company.lead_id,
                    company.company_name,
                    checkpoint="post-website",
                )
                company.enrichment_cost_usd = record["total_usd"]
        else:
            analysis = _enrich()

        return company.company_name, analysis

    pairs = map_ordered(
        companies,
        _process,
        concurrency=concurrency,
        label="Website enrichment",
    )
    results = dict(pairs)
    success = sum(1 for v in results.values() if v is not None)
    log.info("Stage 4 complete — %d/%d websites analysed", success, len(companies))
    return results


def _select_content(markdown: str, per_page_chars: int = 4500, total_chars: int = 22000) -> str:
    """
    Sample content from every crawled page instead of only the start of the
    combined markdown. Pages are joined by crawl_website with "\\n\\n---\\n\\n"
    separators; a blind prefix would silently drop pages 2-5.
    """
    pages = markdown.split("\n\n---\n\n")
    sections: list[str] = []
    used = 0
    for page in pages:
        if used >= total_chars:
            break
        chunk = page.strip()[: min(per_page_chars, total_chars - used)]
        if chunk:
            sections.append(chunk)
            used += len(chunk)
    return "\n\n---\n\n".join(sections)


def _analyse_website_pitch(
    markdown: str,
    company_name: str,
    log: logging.Logger,
) -> Optional[WebsiteAnalysis]:
    """Short pitch-oriented website summary (hiring_first mode — no SEO/blog/chatbot)."""
    content = _select_content(markdown, per_page_chars=3500, total_chars=14000)

    prompt = f"""Analyse this company website for sales pitch intelligence.

COMPANY: {company_name}

WEBSITE CONTENT (Markdown, pages separated by ---):
{content}

Extract and return JSON only:
{{
  "services_offered": ["main products/services — be specific, max 12 items"],
  "website_summary": "<3-4 sentences: what they build/sell, who they serve, stage/size signals if visible>"
}}

Focus on what they do and who they sell to. Do not analyse SEO, blog, or chatbot.
"""

    try:
        data = call_llm(
            prompt,
            max_tokens=700,
            expect_json=True,
            providers=WEBSITE_PROVIDERS,
        )
        return WebsiteAnalysis(
            services_offered=data.get("services_offered") or [],
            website_summary=data.get("website_summary"),
            raw_markdown_length=len(markdown),
        )
    except Exception as e:
        log.warning("Pitch website analysis failed for '%s': %s", company_name, e)
        return WebsiteAnalysis(
            website_summary=f"Pitch analysis failed: {str(e)[:100]}",
            raw_markdown_length=len(markdown),
        )


def _analyse_website(
    markdown: str,
    company_name: str,
    log: logging.Logger,
) -> Optional[WebsiteAnalysis]:
    """Extract structured website fields from Firecrawl markdown (Gemini → OpenAI)."""
    content = _select_content(markdown)

    prompt = f"""Analyse this company website content and extract structured information.

COMPANY: {company_name}

WEBSITE CONTENT (Markdown, multiple pages separated by ---, each page headed by its source URL):
{content}

Extract and return a JSON object with these exact fields:
{{
  "tech_stack_detected": ["list of technologies/platforms detected, e.g. WordPress, Shopify, HubSpot, etc."],
  "services_offered": ["list of main services/products this company offers — be exhaustive, include service lines, industries served, languages, and locations/hubs when stated"],
  "content_quality_score": <integer 1-10, where 10 is excellent>,
  "seo_health": ["list of SEO issues detected, e.g. 'missing meta description', 'no blog', 'thin content'"],
  "has_chatbot": <true/false/null>,
  "has_blog": <true/false>,
  "last_blog_post_date": "<date string if found, else null>",
  "social_proof": <true if testimonials/case studies/reviews are present, false otherwise>,
  "website_summary": "<4-6 sentence summary: what this company does, who its clients are, office/delivery locations, languages or markets served, any growth/expansion/news signals, and their online presence>"
}}

Be specific and evidence-based. Only include what you can actually see in the content.
"""

    try:
        data = call_llm(
            prompt,
            max_tokens=1500,
            expect_json=True,
            providers=WEBSITE_PROVIDERS,
        )
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


def _detect_website_hiring(
    markdown: str,
    company_name: str,
    website: Optional[str],
    log: logging.Logger,
    careers_url: Optional[str] = None,
    careers_markdown: Optional[str] = None,
    *,
    hiring_first: bool = False,
) -> Optional[dict]:
    """Detect hiring signals from crawled website content (Gemini → OpenAI)."""
    content = _select_content(markdown, per_page_chars=2500, total_chars=10000)

    careers_section = ""
    if careers_url:
        careers_section = f"\nVERIFIED CAREERS PAGE (confirmed reachable): {careers_url}\n"
        if careers_markdown:
            careers_section += f"""
CAREERS PAGE CONTENT (Markdown):
{careers_markdown[:8000]}
"""

    pitch_note = ""
    if hiring_first:
        pitch_note = """
This campaign uses hiring intelligence to pitch services — open roles are the highest-value output.
List every visible job title on the careers page. Infer what skills or teams they are building.
"""

    prompt = f"""Analyse this company website content for hiring and recruitment signals.

COMPANY: {company_name}
WEBSITE: {website or "unknown"}
{pitch_note}
{careers_section}
Many companies post open roles on careers/jobs pages or subdomains instead of LinkedIn. Look for:
- Careers, jobs, join-us, work-with-us, vacancies pages
- Explicit "we're hiring" / "now hiring" language
- Named open roles or job titles (list as many as visible, up to 12)
- Recruitment CTAs or talent acquisition mentions

WEBSITE CONTENT (Markdown):
{content}

Return JSON only:
{{
  "is_hiring": "yes" | "no" | "unknown",
  "hiring_signals": ["short evidence-based phrases, max 5"],
  "open_roles": ["job titles if visible, max 12"],
  "careers_url": "<URL if a careers/jobs page URL appears in content, else null>"
}}

Use "yes" only when there is clear evidence of active hiring on the website.
A verified careers page listing open roles counts as clear evidence.
Use "no" when the site was crawled but shows no hiring signals.
Use "unknown" only if content is too thin to tell.
"""

    try:
        return call_llm(
            prompt,
            max_tokens=800 if hiring_first else 600,
            expect_json=True,
            providers=WEBSITE_PROVIDERS,
        )
    except Exception as e:
        log.warning("Website hiring check failed for '%s': %s", company_name, e)
        return None
