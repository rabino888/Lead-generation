"""
Talent optional module: crawl employer website and extract brief-driven findings.

Uses CLIENT.md / client.json brief / ICP keywords so Decoracel lists surface
advertised properties, while other clients get whatever their brief cares about.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from agent.utils.logger import log
from agent.utils.talent_people import (
    STAGE_T05_APOLLO_CONTACTABLE,
    read_people_csv,
    talent_stage_path,
)
from agent.utils.talent_profile import _write_profile_csv

CrawlFn = Callable[..., str]
AnalyseFn = Callable[..., dict[str, Any]]
ResolveWebsiteFn = Callable[..., str]

_STOP = frozenset(
    {
        "the", "and", "for", "with", "from", "that", "this", "they", "their",
        "have", "has", "are", "was", "were", "will", "can", "into", "about",
        "who", "what", "when", "where", "which", "also", "via", "our", "your",
        "client", "clients", "company", "companies", "list", "lists", "spain",
        "updated", "public", "site", "copy", "paid", "scrape", "main", "best",
        "fit", "buyer", "buyers", "sell", "sales", "only", "over", "than",
        "when", "keep", "tight", "notes", "authors", "prefer", "exclude",
        "messaging", "angles", "quality", "pipeline", "local", "through",
    }
)


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm_website(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    return raw.rstrip("/")


def _campaign_client_id(campaign_dir: Path) -> str:
    camp = Path(campaign_dir) / "campaign.json"
    if camp.is_file():
        try:
            data = json.loads(camp.read_text(encoding="utf-8"))
            cid = (data.get("client_id") or "").strip()
            if cid:
                return cid
        except (OSError, json.JSONDecodeError):
            pass
    # data/clients/{client}/campaigns/.../campaign_id
    parts = Path(campaign_dir).resolve().parts
    if "clients" in parts:
        i = parts.index("clients")
        if i + 1 < len(parts):
            return parts[i + 1]
    return ""


def load_client_brief_text(campaign_dir: Path, *, data_root: Path | None = None) -> str:
    """Concatenate CLIENT.md, client.json brief, and ICP brief_source / keywords."""
    root = Path(data_root) if data_root is not None else Path("data")
    parts: list[str] = []
    client_id = _campaign_client_id(campaign_dir)
    if client_id:
        cdir = root / "clients" / client_id
        meta_path = cdir / "client.json"
        brief_name = "CLIENT.md"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
            brief_name = (meta.get("brief_path") or brief_name).strip() or brief_name
            if (meta.get("brief") or "").strip():
                parts.append(str(meta["brief"]).strip())
        md_path = cdir / brief_name
        if md_path.is_file():
            try:
                parts.append(md_path.read_text(encoding="utf-8").strip())
            except OSError:
                pass

    icp_path = Path(campaign_dir) / "icp.json"
    if icp_path.is_file():
        try:
            icp = json.loads(icp_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            icp = {}
        if isinstance(icp, dict):
            if (icp.get("brief_source") or "").strip():
                parts.append(str(icp["brief_source"]).strip())
            kws = icp.get("include_keywords") or icp.get("keywords") or []
            if isinstance(kws, list) and kws:
                parts.append("ICP keywords: " + ", ".join(str(k) for k in kws if str(k).strip()))

    # Dedupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        key = p[:200].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return "\n\n".join(out).strip()


def brief_url_terms(brief: str, icp: Mapping[str, Any] | None = None) -> list[str]:
    """Terms used to prefer sitemap URLs (property pages, etc.)."""
    terms: list[str] = []
    icp = icp or {}
    for key in ("include_keywords", "keywords"):
        raw = icp.get(key) or []
        if isinstance(raw, list):
            terms.extend(str(x).strip() for x in raw if str(x).strip())
    # Phrase-ish tokens from brief (length >= 5, not stopwords)
    for m in re.finditer(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\\-]{4,}", brief or ""):
        tok = m.group(0).lower()
        if tok in _STOP:
            continue
        terms.append(tok)
    # Multi-word include phrases already covered; keep unique
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        low = t.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(t)
    return out[:40]


def analyse_website_for_brief(
    markdown: str,
    *,
    company_name: str,
    brief: str,
    website: str = "",
) -> dict[str, Any]:
    """
    LLM extract guided by client brief (properties, services, etc.).

    Returns dict with website_summary, website_brief_findings (list),
    website_brief_findings_summary, services_offered.
    """
    from agent.integrations.llm import call_llm, website_providers
    from agent.stages.website import _normalize_llm_object, _select_content

    content = _select_content(markdown, per_page_chars=4000, total_chars=26000)
    brief_block = (brief or "").strip()[:3500] or (
        "B2B outreach intelligence: what they sell and who they serve."
    )
    prompt = f"""You extract website intelligence for a B2B list build.
Only use what is visible in the website content. Do not invent listings or URLs.

OUR CLIENT BRIEF (what we care about finding on prospect websites):
{brief_block}

PROSPECT COMPANY: {company_name}
WEBSITE: {website or "unknown"}

WEBSITE CONTENT (Markdown; pages separated by ---):
{content}

Return JSON only:
{{
  "website_summary": "<3-5 sentences: what this company does and who they serve>",
  "services_offered": ["up to 12 concrete services/products visible on the site"],
  "brief_findings": [
    "<concrete items that match the client brief — e.g. property/villa/apartment listings, portfolio units, rental offers, renovation projects, hospitality assets. Prefer name + location + type when visible. Max 15.>"
  ],
  "brief_findings_summary": "<2-4 sentences summarizing how the site relates to the client brief; say if no matching inventory/offers were found>",
  "listing_or_portfolio_urls": ["<URLs of listing/portfolio/property pages if visible, else empty>"]
}}

Rules:
- brief_findings must be grounded in the content. Empty list if nothing matches the brief.
- Prefer inventory/portfolio/listing detail over marketing slogans.
- Do not extract tech stack, SEO, chatbots, or testimonials.
"""
    data = _normalize_llm_object(
        call_llm(
            prompt,
            max_tokens=2000,
            expect_json=True,
            providers=website_providers(),
        )
    )
    if not data:
        return {}
    findings = data.get("brief_findings") or data.get("website_brief_findings") or []
    if not isinstance(findings, list):
        findings = [str(findings)]
    services = data.get("services_offered") or []
    if not isinstance(services, list):
        services = [str(services)]
    urls = data.get("listing_or_portfolio_urls") or []
    if not isinstance(urls, list):
        urls = []
    return {
        "website_summary": _as_str(data.get("website_summary"))[:2000],
        "services_offered": [str(s).strip() for s in services if str(s).strip()][:12],
        "website_brief_findings": [str(f).strip() for f in findings if str(f).strip()][:15],
        "website_brief_findings_summary": _as_str(
            data.get("brief_findings_summary") or data.get("website_brief_findings_summary")
        )[:2000],
        "website_listing_urls": [str(u).strip() for u in urls if str(u).strip()][:10],
    }


def _default_crawl(website: str, brief_terms: list[str]) -> str:
    from agent.integrations.firecrawl import (
        crawl_website_with_fallback,
        map_website_urls,
        scrape_urls,
        website_crawler,
    )
    from agent.utils.concurrency import env_int
    from agent.utils.website_outreach import (
        DEFAULT_OUTREACH_MAX_PAGES,
        augment_mapped_with_content_hubs,
        prefer_brief_relevant_urls,
    )

    max_pages = env_int("FIRECRAWL_MAX_PAGES", default=DEFAULT_OUTREACH_MAX_PAGES, maximum=12)
    sitemap_urls = map_website_urls(website, limit=100)
    sitemap_urls = augment_mapped_with_content_hubs(sitemap_urls, website)
    urls = prefer_brief_relevant_urls(
        sitemap_urls, website, brief_terms, max_pages=max_pages
    )
    markdown = scrape_urls(urls) if urls else None
    if not markdown or len(markdown.strip()) < 200:
        markdown, source = crawl_website_with_fallback(website, max_pages=max_pages)
        log.info("Employer website crawl fallback (%s) for %s", source or website_crawler(), website)
    return markdown or ""


def _resolve_website(row: Mapping[str, Any]) -> str:
    existing = _norm_website(_as_str(row.get("website")))
    if existing:
        return existing
    name = _as_str(row.get("company_name"))
    li = _as_str(row.get("company_linkedin_url"))
    if not name and not li:
        return ""
    # 1) LinkedIn company profile (may include website)
    try:
        from agent.integrations.apify import enrich_company_linkedin

        data = enrich_company_linkedin(
            name or "unknown",
            website=None,
            company_linkedin_url=li or None,
            fetch_open_jobs=False,
        ) or {}
        site = _norm_website(_as_str(data.get("website")))
        if site:
            return site
    except Exception as exc:
        log.warning("Website resolve via LinkedIn failed for %s: %s", name, exc)

    # 2) Apollo org enrich (website_url / primary_domain)
    if name:
        try:
            from agent.integrations.apollo import enrich_company

            org = enrich_company(name) or {}
            site = _norm_website(
                _as_str(org.get("website_url") or org.get("primary_domain"))
            )
            if site:
                return site
        except Exception as exc:
            log.warning("Website resolve via Apollo failed for %s: %s", name, exc)
    return ""


def enrich_contactable_employer_website(
    campaign_dir: Path,
    *,
    crawl_fn: CrawlFn | None = None,
    analyse_fn: AnalyseFn | None = None,
    resolve_website_fn: ResolveWebsiteFn | None = None,
    only_missing: bool = True,
    data_root: Path | None = None,
) -> tuple[int, int]:
    """
    Crawl employer websites for T05 contactables; extract brief-driven findings.

    Returns (enriched_count, skipped_count).
    """
    camp = Path(campaign_dir)
    t05_path = talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE)
    if not t05_path.is_file():
        return 0, 0
    rows = read_people_csv(t05_path)
    if not rows:
        return 0, 0

    brief = load_client_brief_text(camp, data_root=data_root)
    icp: dict[str, Any] = {}
    icp_path = camp / "icp.json"
    if icp_path.is_file():
        try:
            loaded = json.loads(icp_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                icp = loaded
        except (OSError, json.JSONDecodeError):
            pass
    terms = brief_url_terms(brief, icp)

    crawler = crawl_fn
    analyser = analyse_fn or analyse_website_for_brief
    resolver = resolve_website_fn or (lambda r: _resolve_website(r))

    enriched_n = 0
    skipped_n = 0
    updated: list[dict[str, str]] = []
    # Cache by website host to avoid duplicate crawls for same employer
    cache: dict[str, dict[str, Any]] = {}

    for row in rows:
        out = dict(row)
        if only_missing and (out.get("website_brief_findings_summary") or out.get("website_summary") or "").strip():
            updated.append(out)
            continue

        website = _norm_website(_as_str(out.get("website"))) or _norm_website(resolver(out))
        if not website:
            skipped_n += 1
            updated.append(out)
            continue
        out["website"] = website
        company = _as_str(out.get("company_name")) or "company"
        cache_key = website.lower()
        log.info("Employer website enrich: %s → %s", out.get("full_name") or out.get("person_id"), website)

        if cache_key not in cache:
            try:
                if crawler is not None:
                    md = crawler(website)
                else:
                    md = _default_crawl(website, terms)
                if not (md or "").strip():
                    cache[cache_key] = {}
                else:
                    result = analyser(
                        md,
                        company_name=company,
                        brief=brief,
                        website=website,
                    )
                    cache[cache_key] = result or {}
            except Exception as exc:
                log.warning("Employer website enrich failed for %s: %s", website, exc)
                cache[cache_key] = {}

        data = cache[cache_key]
        if data.get("website_summary"):
            out["website_summary"] = str(data["website_summary"])
        findings = data.get("website_brief_findings") or []
        if findings:
            out["website_brief_findings"] = "; ".join(str(f) for f in findings)
        if data.get("website_brief_findings_summary"):
            out["website_brief_findings_summary"] = str(data["website_brief_findings_summary"])
        services = data.get("services_offered") or []
        if services:
            out["website_services"] = "; ".join(str(s) for s in services)
        listing_urls = data.get("website_listing_urls") or []
        if listing_urls:
            out["website_listing_urls"] = "; ".join(str(u) for u in listing_urls)

        if (out.get("website_summary") or out.get("website_brief_findings")
                or out.get("website_brief_findings_summary")):
            enriched_n += 1
        else:
            # Website known but empty analysis — still count as attempted soft-success
            enriched_n += 1
        updated.append(out)

    _write_profile_csv(t05_path, updated)
    log.info(
        "Employer website enrich: enriched=%d skipped=%d of %d contactable",
        enriched_n,
        skipped_n,
        len(rows),
    )
    return enriched_n, skipped_n
