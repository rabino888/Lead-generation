"""
Stage 4 — Website Enrichment (Firecrawl + Gemini/OpenAI website + hiring signals)
Deep-crawls company websites and extracts structured analysis.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, Protocol

from agent.integrations.careers import (
    careers_index_needs_portal_hop,
    find_careers_page,
    pick_careers_portal_hop,
)
from agent.integrations.firecrawl import (
    crawl_website_with_fallback,
    map_website_urls,
    scrape_page,
    scrape_urls,
    website_crawler,
)
from agent.integrations.llm import call_llm, website_providers
from agent.models import Lead, WebsiteAnalysis
from agent.utils.concurrency import env_int, map_ordered
from agent.utils.cost_tracker import get_cost_tracker
from agent.utils.logger import get_run_logger
from agent.utils.website_markdown_cache import load_markdown, save_markdown
from agent.utils.website_outreach import (
    DEFAULT_OUTREACH_MAX_PAGES,
    extract_decision_maker_mentions,
    extract_social_links,
    merge_mentions,
    parse_blog_posts_from_llm,
    select_outreach_urls,
)


class _HasWebsite(Protocol):
    company_name: str
    website: Optional[str]


def _decision_maker_name(company: _HasWebsite) -> Optional[str]:
    dm = getattr(company, "decision_maker", None)
    name = getattr(dm, "name", None) if dm else None
    return str(name).strip() if name else None


def _apply_outreach_signals(
    analysis: WebsiteAnalysis,
    *,
    markdown: str,
    plan,
    careers_url: Optional[str],
    hiring: Optional[dict],
    dm_name: Optional[str],
) -> WebsiteAnalysis:
    social = extract_social_links(markdown)
    analysis.social_facebook = social.get("facebook") or analysis.social_facebook
    analysis.social_instagram = social.get("instagram") or analysis.social_instagram
    analysis.social_tiktok = social.get("tiktok") or analysis.social_tiktok
    analysis.social_x = social.get("x") or analysis.social_x
    if plan:
        analysis.blog_url = analysis.blog_url or plan.blog_url
        analysis.news_url = analysis.news_url or plan.news_url
    if analysis.has_blog is None:
        analysis.has_blog = bool(analysis.blog_url or analysis.news_url or analysis.blog_posts)
    mentions = extract_decision_maker_mentions(markdown, dm_name)
    analysis.decision_maker_mentions = merge_mentions(
        mentions, analysis.decision_maker_mentions
    )
    if careers_url:
        analysis.website_careers_url = careers_url
    if hiring:
        analysis.website_is_hiring = hiring.get("is_hiring")
        analysis.website_open_roles = hiring.get("open_roles") or []
        analysis.website_careers_url = (
            careers_url or hiring.get("careers_url") or analysis.website_careers_url
        )
    analysis.raw_markdown_length = len(markdown or "")
    return analysis


def _website_analysis_mode(client_profile) -> str:
    icp = getattr(client_profile, "icp", None)
    mode = getattr(icp, "website_analysis_mode", None) or "full"
    return mode if mode in ("full", "hiring_first") else "full"


def run(
    companies: list[_HasWebsite],
    client_profile,
    run_id: str,
    logger: Optional[logging.Logger] = None,
    *,
    campaign_dir: Optional[Path] = None,
    firecrawl: bool = True,
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
        "Stage 4 — Website enrichment (%d companies, concurrency=%d, mode=%s, "
        "crawl=%s, firecrawl_flag=%s)",
        len(companies),
        concurrency,
        mode,
        website_crawler() if firecrawl else "cache-only",
        firecrawl,
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
            log.info("No website for '%s' — skipping website crawl", company.company_name)
            return company.company_name, None

        def _enrich() -> Optional[WebsiteAnalysis]:
            plan = None
            careers_url: Optional[str] = None
            careers_markdown: Optional[str] = None
            combined = ""

            if not firecrawl and campaign_dir:
                cached = load_markdown(campaign_dir, company.website or "")
                if not cached:
                    log.warning(
                        "LLM-only repair: no markdown cache for '%s' (%s)",
                        company.company_name,
                        company.website,
                    )
                    return None
                combined = cached.markdown
                careers_url = cached.careers_url
                log.info(
                    "LLM-only repair: using cached markdown for %s (%d chars)",
                    company.company_name,
                    len(combined),
                )
            else:
                max_pages = env_int(
                    "FIRECRAWL_MAX_PAGES",
                    default=DEFAULT_OUTREACH_MAX_PAGES,
                    maximum=12,
                )
                log.info("Mapping site URLs: %s", company.website)
                sitemap_urls = map_website_urls(company.website, limit=100)
                plan = select_outreach_urls(
                    sitemap_urls, company.website, max_pages=max_pages
                )

                markdown = scrape_urls(plan.urls) if plan.urls else None
                crawl_source = f"{website_crawler()}_selected"
                if not markdown or len(markdown.strip()) < 200:
                    log.info(
                        "Selected scrape thin/empty for %s — fallback crawl",
                        company.website,
                    )
                    markdown, crawl_source = crawl_website_with_fallback(
                        company.website, max_pages=max_pages
                    )
                if not markdown:
                    log.warning(
                        "No website content for %s (apify/firecrawl failed) — careers probe only",
                        company.website,
                    )
                else:
                    log.info(
                        "Website crawl source for %s: %s (%d chars, %d urls)",
                        company.company_name,
                        crawl_source,
                        len(markdown),
                        len(plan.urls),
                    )

                careers_url = find_careers_page(
                    company.website,
                    markdown,
                    sitemap_urls=sitemap_urls,
                )
                already_scraped = bool(
                    careers_url and markdown and careers_url.rstrip("/") in markdown
                )
                if careers_url and not already_scraped:
                    careers_markdown = scrape_page(careers_url)
                    if not careers_markdown:
                        from agent.integrations.apify import scrape_website_apify

                        careers_markdown = scrape_website_apify(
                            careers_url, max_pages=1, max_depth=0
                        )

                # +1 hop: marketing /careers → real ATS / "see open roles" portal
                careers_body = careers_markdown or ""
                if (
                    not careers_body
                    and careers_url
                    and markdown
                    and careers_url.rstrip("/") in markdown
                ):
                    for section in markdown.split("\n\n---\n\n"):
                        if careers_url.rstrip("/") in section:
                            careers_body = section
                            break
                hop_source = careers_body or markdown or ""
                if careers_url and careers_index_needs_portal_hop(
                    hop_source, careers_url=careers_url
                ):
                    hop_url = pick_careers_portal_hop(
                        hop_source,
                        company.website or "",
                        current_url=careers_url,
                    )
                    if hop_url and hop_url.rstrip("/") != careers_url.rstrip("/"):
                        log.info(
                            "Careers portal hop for %s: %s → %s",
                            company.company_name,
                            careers_url,
                            hop_url,
                        )
                        hop_md = scrape_page(hop_url)
                        if not hop_md:
                            from agent.integrations.apify import scrape_website_apify

                            hop_md = scrape_website_apify(
                                hop_url, max_pages=1, max_depth=0
                            )
                        if hop_md:
                            careers_url = hop_url
                            careers_markdown = hop_md

                combined = markdown or ""
                if careers_markdown and careers_url:
                    already = careers_url.rstrip("/") in combined
                    if not already:
                        combined = (
                            f"{combined}\n\n---\n\n### [{careers_url}]\n{careers_markdown}"
                        ).strip()
                if campaign_dir and combined:
                    try:
                        save_markdown(
                            campaign_dir,
                            company.website or "",
                            combined,
                            careers_url=careers_url,
                        )
                    except Exception as e:
                        log.warning(
                            "Failed to cache markdown for %s: %s",
                            company.company_name,
                            e,
                        )

            hiring = _detect_website_hiring(
                combined,
                company.company_name,
                company.website,
                log,
                careers_url=careers_url,
                careers_markdown=careers_markdown,
                hiring_first=hiring_first,
            )

            dm_name = _decision_maker_name(company)
            analysis = None
            if combined:
                analysis = _analyse_outreach(
                    combined, company.company_name, log, dm_name=dm_name
                )
            if not analysis:
                if not (hiring or careers_markdown or combined):
                    return None
                analysis = WebsiteAnalysis(
                    website_summary="Thin site — hiring or page signals only"
                    if not combined
                    else None,
                )
            return _apply_outreach_signals(
                analysis,
                markdown=combined,
                plan=plan,
                careers_url=careers_url,
                hiring=hiring,
                dm_name=dm_name,
            )

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


def _analyse_outreach(
    markdown: str,
    company_name: str,
    log: logging.Logger,
    *,
    dm_name: Optional[str] = None,
) -> Optional[WebsiteAnalysis]:
    """Extract outreach fields: services, about, recent blog/news, DM mentions."""
    content = _select_content(markdown, per_page_chars=4000, total_chars=26000)
    dm_block = ""
    if dm_name:
        dm_block = f"""
DECISION MAKER: {dm_name}
If this person is named on about/team/leadership pages, copy the verbatim bio or mention into decision_maker_mentions (max 5). If they are not named, return an empty list.
"""

    prompt = f"""Extract outreach intelligence from this company website. Only use what is visible in the content.

COMPANY: {company_name}
{dm_block}
WEBSITE CONTENT (Markdown, pages separated by ---, each page headed by its source URL):
{content}

Return JSON only:
{{
  "services_offered": ["specific products/services, max 12"],
  "about_summary": "<2-4 sentences from About / Our story / Team: who they are, what they stand for, leadership if named>",
  "has_blog": <true if a blog, news, insights, or press section exists, else false>,
  "blog_url": "<blog index URL if visible, else null>",
  "news_url": "<news/press index URL if visible, else null>",
  "blog_posts": [
    {{
      "title": "<latest post title>",
      "published_at": "<date as written, preferably YYYY-MM-DD, else null>",
      "description": "<1-2 sentence summary or excerpt>",
      "url": "<post URL if visible, else null>"
    }}
  ],
  "website_summary": "<3-5 sentences for a salesperson: what they do, who they serve, notable hiring or news>",
  "decision_maker_mentions": ["verbatim quotes or bio lines about the decision maker if present"]
}}

Rules:
- blog_posts: up to 5 most recent posts or news items. Omit the array if none are visible.
- Do not invent dates, titles, or URLs.
- Do not extract tech stack, SEO, chatbot, or testimonials.
- about_summary should come from About / Team pages, not the homepage nav.
"""

    try:
        data = _normalize_llm_object(
            call_llm(
                prompt,
                max_tokens=1800,
                expect_json=True,
                providers=website_providers(),
            )
        )
        if not data:
            raise ValueError("LLM returned non-object JSON")
        mentions = data.get("decision_maker_mentions") or []
        if not isinstance(mentions, list):
            mentions = [str(mentions)]
        has_blog = data.get("has_blog")
        if isinstance(has_blog, str):
            has_blog = has_blog.strip().lower() in ("true", "yes", "1")
        def _opt_url(val) -> Optional[str]:
            if not val:
                return None
            text = str(val).strip()
            if text.lower() in ("null", "none", "n/a"):
                return None
            return text
        return WebsiteAnalysis(
            services_offered=data.get("services_offered") or [],
            about_summary=_opt_url(data.get("about_summary")),
            has_blog=has_blog if isinstance(has_blog, bool) else None,
            blog_url=_opt_url(data.get("blog_url")),
            news_url=_opt_url(data.get("news_url")),
            blog_posts=parse_blog_posts_from_llm(data.get("blog_posts")),
            decision_maker_mentions=[str(m).strip() for m in mentions if str(m).strip()],
            website_summary=_opt_url(data.get("website_summary")),
            raw_markdown_length=len(markdown),
        )
    except Exception as e:
        log.warning("Outreach website analysis failed for '%s': %s — plain-text fallback", company_name, e)
        try:
            summary = call_llm(
                f"Summarize in 3 sentences what {company_name} does based on this website content:\n\n{_select_content(markdown, per_page_chars=2000, total_chars=8000)}",
                system="You are a concise B2B analyst. Plain prose only, no JSON.",
                max_tokens=400,
                expect_json=False,
                providers=website_providers(),
            )
            if isinstance(summary, str) and summary.strip():
                return WebsiteAnalysis(
                    website_summary=summary.strip()[:1200],
                    raw_markdown_length=len(markdown),
                )
        except Exception as e2:
            log.warning("Plain-text fallback failed for '%s': %s", company_name, e2)
        excerpt = _select_content(markdown, per_page_chars=800, total_chars=800).strip()
        return WebsiteAnalysis(
            website_summary=excerpt[:500] if excerpt else f"Analysis failed: {str(e)[:80]}",
            raw_markdown_length=len(markdown),
        )


def _normalize_llm_object(data: Any) -> Optional[dict]:
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                return item
    return None


def _normalize_hiring_payload(data: Any) -> Optional[dict]:
    """LLM JSON may be a dict or a one-element list — normalize to dict."""
    return _normalize_llm_object(data)


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
  "open_roles": ["job titles if visible, max 12"],
  "careers_url": "<URL if a careers/jobs page URL appears in content, else null>"
}}

Use "yes" only when there is clear evidence of active hiring on the website.
A verified careers page listing open roles counts as clear evidence.
Use "no" when the site was crawled but shows no hiring signals.
Use "unknown" only if content is too thin to tell.
"""

    try:
        raw = call_llm(
            prompt,
            max_tokens=800 if hiring_first else 600,
            expect_json=True,
            providers=website_providers(),
        )
        return _normalize_hiring_payload(raw)
    except Exception as e:
        log.warning("Website hiring check failed for '%s': %s", company_name, e)
        return None
