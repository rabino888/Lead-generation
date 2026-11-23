"""Turn research text into a reviewable plan. No paid APIs."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from agent.autopilot.adjust import SEED_LADDER
from agent.autopilot.learnings import apply_learnings
from agent.autopilot.research import host_of
from agent.utils.brief_icp import parse_brief_to_icp
from agent.utils.enrichment_plan import DEFAULT_MODULES_COMPANY, estimate_plan

_LOCAL_RE = re.compile(
    r"\b(opening hours|store hours|visit us|our menu|book a table|dine in|locations near you)\b",
    re.I,
)
_HIRING_RE = re.compile(
    r"\b(we(?:'re| are) hiring|open roles|join our team|see open positions|careers)\b",
    re.I,
)
_B2B_RE = re.compile(
    r"\b(b2b|saas|platform|enterprise|api|software|workflow|customers)\b",
    re.I,
)
_FREE_RE = re.compile(
    r"\b(free trial|free demo|free guide|free audit|download the|book a demo)\b",
    re.I,
)
_SECTORS = (
    "software",
    "saas",
    "insurance",
    "healthcare",
    "logistics",
    "manufacturing",
    "restaurants",
    "hospitality",
    "fintech",
    "cybersecurity",
    "construction",
    "real estate",
    "education",
    "retail",
)
_COUNTRY_CODES = {
    "United States": "US",
    "United Kingdom": "GB",
    "Spain": "ES",
    "Germany": "DE",
    "France": "FR",
    "Canada": "CA",
    "Australia": "AU",
    "Netherlands": "NL",
    "Ireland": "IE",
    "Italy": "IT",
    "Portugal": "PT",
    "Sweden": "SE",
    "Brazil": "BR",
    "Mexico": "MX",
    "India": "IN",
    "Singapore": "SG",
}
_SEED_USD = {
    "csv_ingest": 0.0,
    "google_search": 0.15,
    "google_maps": 0.20,
    "linkedin_companies": 0.25,
}
_DEFAULT_TITLES = ["Founder", "CEO", "Owner", "Managing Director"]


def page_kind(text: str) -> str:
    local = bool(_LOCAL_RE.search(text or ""))
    b2b = bool(_B2B_RE.search(text or ""))
    if local and not b2b:
        return "local"
    if b2b:
        return "b2b"
    return "general"


def _sectors(text: str) -> list[str]:
    found = []
    lower = (text or "").lower()
    for word in _SECTORS:
        if word in lower and word not in found:
            found.append(word)
    return found


def _queries(industries: list[str], geo: str) -> list[str]:
    labels = industries[:3] or ["B2B"]
    queries = []
    for label in labels:
        query = f"{label} companies"
        if geo:
            query = f"{query} {geo}"
        queries.append(query)
    return queries


def seed_config_for(source_id: str, icp: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Config block stored on seed_sources.json for one source id."""
    prev = dict(previous or {})
    geo = ((icp.get("geo") or {}).get("primary_location") or "") or (
        (icp.get("contact_locations") or [""])[0]
    )
    industries = list(icp.get("industries") or [])
    excludes = list(icp.get("excluded_domains") or [])
    max_results = int(prev.get("max_results") or 40)
    if source_id == "csv_ingest":
        return {"csv_path": prev.get("csv_path") or "", "max_results": max_results}
    if source_id == "google_maps":
        keyword = industries[0] if industries else "business"
        return {
            "queries": [{"keyword": keyword, "location": geo}],
            "keyword": keyword,
            "location": geo,
            "max_results": max_results,
            "exclude_domains": excludes,
        }
    if source_id == "linkedin_companies":
        return {
            "queries": [{"keyword": query} for query in _queries(industries, geo)],
            "max_results": max_results,
            "exclude_domains": excludes,
        }
    country = _COUNTRY_CODES.get(geo, "")
    return {
        "queries": _queries(industries, geo),
        "max_results": max_results,
        "country_code": country,
        "location": geo,
        "exclude_domains": excludes,
    }


def choose_seed_source(kind: str, *, hiring: bool, csv_path: str = "") -> tuple[str, str]:
    if csv_path:
        return "csv_ingest", "Operator supplied a seed CSV, so discovery stays off paid search."
    if kind == "local":
        return "google_maps", "The site reads as a local business, so Google Maps is the seed source."
    if hiring and kind == "b2b":
        return (
            "linkedin_companies",
            "The site is B2B and hiring, so LinkedIn company search is the seed source.",
        )
    return (
        "google_search",
        "Web search is the default company seed. Apollo company search is not used.",
    )


def choose_contact_provider() -> tuple[str, str]:
    return (
        "apollo",
        "Apollo is the first source for a decision-maker email and LinkedIn URL. "
        "Treg is the fallback if that gate comes back empty.",
    )


def _employee_ranges(size_min: int | None, size_max: int | None) -> list[str]:
    if size_min is None and size_max is None:
        return []
    low = 1 if size_min is None else int(size_min)
    high = 1000 if size_max is None else int(size_max)
    buckets = [(1, 10), (11, 50), (51, 200), (201, 500), (501, 1000)]
    out = []
    for start, end in buckets:
        if end >= low and start <= high:
            out.append(f"{start},{end}")
    return out


def _offer(research: dict[str, Any]) -> dict[str, Any]:
    text = research.get("text") or ""
    description = (research.get("description") or "").strip()
    if not description:
        description = " ".join(text.split())[:400]
    free = _FREE_RE.search(text)
    magnet_status = "existing" if free else "proposed"
    magnet_title = free.group(0) if free else "Short working sample"
    return {
        "id": 1,
        "title": (research.get("company_name") or "Working offer")[:80],
        "description": (
            description
            or "Offer inferred from the public homepage. Confirm what the client can deliver."
        )[:800],
        "deliverability_note": "Inferred from the public site. Confirm the client can deliver this before any send.",
        "lead_magnet": {
            "id": 1,
            "offer_id": 1,
            "status": magnet_status,
            "title": magnet_title[:80],
            "description": "Free sample tied to the offer above. The email must not promise more than this.",
            "deliverability_note": "Operator confirms delivery capacity at plan approval.",
            "pitch": "Use this and tell me what you think.",
            "upsell": "The paid work in the linked offer.",
            "effort": "Unknown until the operator confirms.",
            "compliance": "none noted",
        },
    }


def _cost(plan_modules: dict[str, bool], target_leads: int, seed_id: str, contact_provider: str) -> dict[str, Any]:
    estimate = estimate_plan(
        {
            "campaign_id": "autopilot",
            "campaign_type": "company_outreach",
            "target_leads": target_leads,
            "modules": plan_modules,
            "website_crawler": "apify",
        }
    )
    seed_usd = float(_SEED_USD.get(seed_id, 0.15))
    contact_usd = 0.0
    enrich_usd = 0.0
    for line in estimate.get("lines") or []:
        module = line.get("module_id")
        amount = float(line.get("line_usd") or 0)
        if module == "apollo_email":
            contact_usd += amount
        elif module not in {"icp_match", "dedupe"}:
            enrich_usd += amount
    if contact_provider == "treg":
        # Treg email is not in the builder rate card. Use a visible per-lead estimate.
        contact_usd = round(target_leads * 0.03, 4)
    outreach_usd = 0.5
    by_stage = {
        "seed": round(seed_usd, 4),
        "icp_match": 0.0,
        "dedupe": 0.0,
        "contacts": round(contact_usd, 4),
        "enrich": round(enrich_usd, 4),
        "outreach": outreach_usd,
    }
    total = round(sum(by_stage.values()), 4)
    return {
        "by_stage": by_stage,
        "total_usd": total,
        "confidence": "est",
        "builder_estimate": {
            "total_usd": estimate.get("total_usd"),
            "warnings": estimate.get("warnings") or [],
        },
    }


def build_plan(
    research: dict[str, Any],
    *,
    campaign_id: str,
    client_id: str,
    learnings: list[dict[str, Any]] | None = None,
    target_leads: int = 25,
    max_usd: float = 25.0,
    notes: str = "",
    seed_source: str = "",
    contact_provider: str = "",
    csv_path: str = "",
    max_attempts: int = 3,
) -> dict[str, Any]:
    text = "\n".join(
        part
        for part in (research.get("text") or "", research.get("description") or "", notes)
        if part
    )
    kind = page_kind(text)
    hiring = bool(_HIRING_RE.search(text))
    parsed = parse_brief_to_icp(text, campaign_id=campaign_id, campaign_type="company_outreach")
    industries = list(parsed.get("industries") or [])
    for sector in _sectors(text):
        if sector not in [item.lower() for item in industries]:
            industries.append(sector)
    titles = list(parsed.get("job_titles") or [])
    titles_inferred = not titles
    if not titles:
        titles = list(_DEFAULT_TITLES)
    locations = list(parsed.get("contact_locations") or [])
    primary = locations[0] if locations else ""
    host = research.get("host") or host_of(research.get("url") or "")
    excluded_domains = [host] if host else []
    icp: dict[str, Any] = {
        "schema_version": "1",
        "campaign_id": campaign_id,
        "industries": industries,
        "excluded_industries": ["staffing and recruiting"],
        "geo": {
            "primary_location": primary,
            "country_codes": {primary: _COUNTRY_CODES[primary]} if primary in _COUNTRY_CODES else {},
            "location_hints": [item.lower() for item in locations],
            "segments": locations or ["default"],
        },
        "company_size_min": parsed.get("company_size_min"),
        "company_size_max": parsed.get("company_size_max"),
        "employee_ranges": _employee_ranges(parsed.get("company_size_min"), parsed.get("company_size_max")),
        "include_keywords": list(parsed.get("include_keywords") or []),
        "exclude_keywords": ["staffing agency", "recruitment agency"],
        "excluded_name_patterns": "recruitment|staffing",
        "excluded_domains": excluded_domains,
        "job_titles": titles,
        "contact_locations": locations,
        "website_analysis_mode": "hiring_first" if hiring else "full",
        "apply_weak_seed_checks": True,
        "weak_seed_rule_pack": "generic",
        "weak_seed_extra_patterns": [],
        "exclude_mega_outsourcers": True,
    }
    chosen_seed, seed_why = choose_seed_source(kind, hiring=hiring, csv_path=csv_path)
    if seed_source:
        chosen_seed = seed_source
        seed_why = "Operator locked the seed source."
    provider, provider_why = choose_contact_provider()
    if contact_provider:
        provider = contact_provider
        provider_why = "Operator locked the contact provider."
    modules = dict(DEFAULT_MODULES_COMPANY)
    if hiring:
        modules["linkedin_company_jobs"] = True
        modules["website_scrape"] = True
    if kind == "local" and not hiring:
        modules["linkedin_company_jobs"] = False
    modules["apollo_phone"] = False
    offer = _offer(research)
    warnings = []
    if not industries:
        warnings.append("No industry was read off the site. Confirm industries before approval.")
    if not locations:
        warnings.append("No buyer country was read off the site. Apollo will not filter person location.")
    if titles_inferred:
        warnings.append("Buyer titles were not on the page. The plan uses Founder, CEO, Owner, and Managing Director.")
    if offer["lead_magnet"]["status"] != "existing":
        warnings.append("Lead magnet is proposed, not a published free offer. Approval marks it approved.")
    plan: dict[str, Any] = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "client_id": client_id,
        "company_name": research.get("company_name") or client_id,
        "company_url": research.get("url") or "",
        "page_kind": kind,
        "hiring_signal": hiring,
        "seed_source": {
            "id": chosen_seed,
            "type": chosen_seed,
            "why": seed_why,
            "ladder": list(SEED_LADDER),
            "config": seed_config_for(
                chosen_seed,
                icp,
                {"csv_path": csv_path, "max_results": max(20, min(150, target_leads * 4))},
            ),
        },
        "contact_provider": provider,
        "contact_provider_why": provider_why,
        "linkedin_enrich_provider": "apify",
        "icp": icp,
        "titles_inferred": titles_inferred,
        "enrichment": {
            "modules": modules,
            "website_crawler": "apify",
            "website_analysis_mode": icp["website_analysis_mode"],
            "why": (
                "Hiring-first website mode and company jobs are on because the site is hiring."
                if hiring
                else "Company-outreach defaults: Apollo email, decision-maker LinkedIn, website scrape. Phone reveal stays off."
            ),
        },
        "offer": offer,
        "outreach_strategy": {
            "shape": "four-step cold email",
            "steps": ["opener", "bump", "thoughts", "breakup"],
            "path": "optional, only after the enrichment gate",
            "optional": True,
            "angle": (
                "Lead with the offer on the client's site. Cite only fields the lead row actually contains. "
                "Do not send email from this run. Skip the sequence if the enriched list is not worth a draft."
            ),
            "qa": "outreach verify.py / qa.py. A held lead is retried with the gate reasons added as constraints.",
        },
        "target_leads": int(target_leads),
        "max_usd": float(max_usd),
        "max_attempts": int(max_attempts),
        "gates": {
            "min_seeds": 1,
            "max_weak_seed_ratio": 0.5,
            "min_matched": 1,
            "min_match_ratio": 0.15,
            "min_contactable": 1,
            "min_outreach_passed": 1,
        },
        "warnings": warnings,
        "locked_fields": [
            name
            for name, flag in (
                ("seed_source", bool(seed_source or csv_path)),
                ("contact_provider", bool(contact_provider)),
            )
            if flag
        ],
        "outreach_constraints": [],
        "notes": (notes or "").strip(),
    }
    plan["cost_estimate"] = _cost(modules, int(target_leads), chosen_seed, provider)
    plan["_modules_on_at_plan"] = [key for key, value in modules.items() if value]
    plan = apply_learnings(plan, learnings or [])
    # Learnings may have changed the source or provider. Refresh cost and queries.
    seed_id = plan["seed_source"]["id"]
    plan["seed_source"]["config"] = seed_config_for(seed_id, plan["icp"], plan["seed_source"].get("config"))
    plan["cost_estimate"] = _cost(plan["enrichment"]["modules"], int(target_leads), seed_id, plan["contact_provider"])
    plan["markdown"] = render_plan_md(plan)
    return plan


def render_plan_md(plan: dict[str, Any]) -> str:
    icp = plan.get("icp") or {}
    geo = (icp.get("geo") or {}).get("primary_location") or "(not read)"
    seed = plan.get("seed_source") or {}
    cost = plan.get("cost_estimate") or {}
    modules = (plan.get("enrichment") or {}).get("modules") or {}
    on = [key for key, value in modules.items() if value]
    lines = [
        f"# Autopilot plan — {plan.get('company_name')}",
        "",
        f"- Company URL: {plan.get('company_url')}",
        f"- Client id: `{plan.get('client_id')}`",
        f"- Campaign id: `{plan.get('campaign_id')}`",
        f"- Page read as: {plan.get('page_kind')}",
        "",
        "## Seed source",
        "",
        f"- Choice: `{seed.get('id')}`",
        f"- Why: {seed.get('why')}",
        f"- Queries / config: `{seed.get('config')}`",
        "",
        "## ICP",
        "",
        f"- Industries: {', '.join(icp.get('industries') or []) or '(none read)'}",
        f"- Buyer location: {geo}",
        f"- Titles: {', '.join(icp.get('job_titles') or [])}",
        f"- Headcount: {icp.get('company_size_min')}–{icp.get('company_size_max')}",
        f"- Website mode: `{icp.get('website_analysis_mode')}`",
        "- Weak-seed checks: on (`generic` pack) for this campaign.",
        "",
        "## Contacts and enrichment",
        "",
        f"- Email and LinkedIn: `{plan.get('contact_provider')}`",
        f"- Why: {plan.get('contact_provider_why')}",
        f"- LinkedIn enrichment provider: `{plan.get('linkedin_enrich_provider')}`",
        f"- Modules on: {', '.join(on)}",
        f"- Why: {(plan.get('enrichment') or {}).get('why')}",
        "",
        "## Outreach strategy",
        "",
        f"- Shape: {(plan.get('outreach_strategy') or {}).get('shape')}",
        f"- Angle: {(plan.get('outreach_strategy') or {}).get('angle')}",
        f"- QA: {(plan.get('outreach_strategy') or {}).get('qa')}",
        f"- Offer: {(plan.get('offer') or {}).get('title')}",
        f"- Lead magnet: {((plan.get('offer') or {}).get('lead_magnet') or {}).get('title')} "
        f"({((plan.get('offer') or {}).get('lead_magnet') or {}).get('status')})",
        "",
        "## Cost",
        "",
        f"- Cap: ${plan.get('max_usd')}",
        f"- Estimate: ${cost.get('total_usd')} ({cost.get('confidence')})",
        f"- By stage: `{cost.get('by_stage')}`",
        f"- Target leads: {plan.get('target_leads')}",
        f"- Attempts per stage: {plan.get('max_attempts')}",
        "",
        "## Warnings",
        "",
    ]
    warnings = plan.get("warnings") or []
    if warnings:
        lines.extend(f"- {item}" for item in warnings)
    else:
        lines.append("- None.")
    applied = plan.get("learnings_applied") or []
    lines.extend(["", "## Learnings applied", ""])
    if applied:
        for item in applied:
            lines.append(f"- {item.get('why')} `{item.get('set')}`")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Approval gates",
            "",
            "1. This plan. Nothing is spent until it is approved.",
            "2. Companies after ICP match and dedupe. Check that the list is the right kind of company.",
            "3. Enriched leads. Outreach is optional here: generate a sequence, or stop with the list.",
            "",
            "Each gate includes an assistant note (what it sees, and what it would change). "
            "Those notes do not relax the checkers. No email is sent.",
            "",
        ]
    )
    return "\n".join(lines)


def client_id_from_url(url: str) -> str:
    from agent.autopilot.research import domain_label
    from agent.utils.client_intake import slugify_client_id

    return slugify_client_id(domain_label(url))


def campaign_id_for(client_id: str, data_root: Any) -> str:
    from pathlib import Path

    base = f"{client_id}_autopilot"[:60]
    root = Path(data_root) / "campaigns"
    if not (root / base).exists():
        return base
    for number in range(2, 100):
        candidate = f"{base}_{number}"[:64]
        if not (root / candidate).exists():
            return candidate
    raise ValueError(f"Could not allocate a campaign id for {client_id}")
