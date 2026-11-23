"""
Deterministic company ICP matching — no LLM.

Pure functions: match_company(row, rules) -> (passed, reasons).
"""
from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlparse

from agent.utils.icp_rules import IcpRules
from agent.utils.icp_seed_quality import weak_seed_reason

# LinkedIn labels a marketing agency "Advertising Services", not "marketing and advertising".
_INDUSTRY_STOP = frozenset(
    {
        "and",
        "or",
        "the",
        "of",
        "for",
        "services",
        "service",
        "other",
        "general",
        "goods",
    }
)
SERVICE_PROVIDER_RE = re.compile(
    r"\bagenc(y|ies)\b|\bconsultancy\b|\bshopify\s+(plus\s+|platinum\s+)?partner\b",
    re.I,
)


def industry_tokens(text: str) -> set[str]:
    parts = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {part for part in parts if len(part) >= 4 and part not in _INDUSTRY_STOP}


def industry_matches_allowed(industry: str, allowed: list[str]) -> bool:
    observed = (industry or "").lower()
    tokens = industry_tokens(observed)
    for name in allowed:
        label = (name or "").lower().strip()
        if not label:
            continue
        # The allow phrase is contained in the company industry.
        # The reverse (company industry contained in a longer allow phrase)
        # would treat a bare "Manufacturing" as personal-care manufacturing.
        if label in observed:
            return True
        label_tokens = industry_tokens(label)
        overlap = label_tokens & tokens
        if not overlap:
            continue
        # A long allow label needs two shared tokens. One shared word such as
        # "manufacturing" must not pull in machinery or generic manufacturers.
        if len(label_tokens) >= 3 and len(overlap) < 2:
            continue
        return True
    return False


def excluded_industry_hit(industry: str, excluded: list[str], allowed: list[str]) -> str:
    """Return the excluded industry label that this company industry belongs to."""
    if industry_matches_allowed(industry, allowed):
        return ""
    observed = (industry or "").lower()
    tokens = industry_tokens(observed)
    for blocked in excluded:
        label = (blocked or "").strip()
        if not label:
            continue
        low = label.lower()
        if low in observed or industry_tokens(low) & tokens:
            return label
    return ""


def targets_operators_not_agencies(rules: IcpRules) -> bool:
    """True when the ICP excludes marketing/advertising and does not target it."""
    excluded = " ".join(rules.excluded_industries).lower()
    allowed = " ".join(rules.industries_list()).lower()
    blocks = "marketing" in excluded or "advertising" in excluded
    wants = "marketing" in allowed or "advertising" in allowed
    return blocks and not wants


# Always-on cheap gate: LinkedIn jobs often label recruiters as the client's industry.
RECRUITER_NAME_RE = re.compile(
    r"\b("
    r"talent|staffing|recruit|reclut|headhunt|rr\.?\s*hh|recursos\s+humanos|"
    r"selecci[oó]n(\s+de\s+personal)?|new\s+talent|people\s+search|"
    r"executive\s+search|personnel|manpower|adecco|randstad"
    r")\b",
    re.I,
)


def _root_domain(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if url.startswith("http") else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def _parse_employee_count(size: str | int | None) -> int | None:
    if size is None or size == "":
        return None
    if isinstance(size, int):
        return size or None
    text = str(size).strip().lower().replace(",", "")
    # Ranges like "51-200", "200,50000", "1,100"
    m = re.match(r"^(\d+)\s*[-–to]+\s*(\d+)$", text)
    if m:
        return (int(m.group(1)) + int(m.group(2))) // 2
    m = re.match(r"^(\d+)\+$", text)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)", text)
    if m:
        # 0 employees is a vendor's "unknown", never a real headcount.
        return int(m.group(1)) or None
    return None


_STAFFING_INDUSTRY_RE = re.compile(r"\b(staffing|recruiting|recruitment)\b", re.I)

_SPAM_HOST_SUFFIXES = (
    "blogspot.com",
    "wordpress.com",
    "wixsite.com",
    "godaddysites.com",
    "web.app",
    "firebaseapp.com",
    "sites.google.com",
    "linktr.ee",
    "carrd.co",
    "notion.site",
    "medium.com",
    "beacons.ai",
    "bio.link",
    "myshopify.com",
)
_SPAM_TLDS = (
    ".biz",
    ".info",
    ".xyz",
    ".top",
    ".click",
    ".loan",
    ".gq",
    ".tk",
    ".ml",
    ".cf",
    ".ga",
)
_SPAM_TEXT_RE = re.compile(
    r"email\s*lists?|lead\s*lists?|buy\s+leads|leads?\s+for\s+sale|"
    r"\bdirectory\b|crypto|blockchain|\bweb3\b|casino|gambling",
    re.I,
)


def allows_recruiter_companies(rules: IcpRules) -> bool:
    """
    Recruiter and staffing names are rejected unless this ICP is targeting them.

    Explicit ``allow_recruiter_companies`` true/false wins. When unset, allow
    only if a target industry is staffing or recruiting.
    """
    explicit = rules.allow_recruiter_companies
    if explicit is True:
        return True
    if explicit is False:
        return False
    return any(_STAFFING_INDUSTRY_RE.search(ind or "") for ind in rules.industries_list())


def small_company_spam_reason(name: str, website: str, industry: str = "") -> str | None:
    """
    Spam check for companies under 10 employees.

    Real boutiques pass. Free hosts, spam TLDs, lead-list language, and
    education institutes do not.
    """
    domain = _root_domain(website)
    if not domain or "." not in domain:
        return "missing_domain"
    for host in _SPAM_HOST_SUFFIXES:
        if domain == host or domain.endswith("." + host):
            return f"free_host:{host}"
    for tld in _SPAM_TLDS:
        if domain.endswith(tld):
            return f"spam_tld:{tld}"
    blob = f"{name} {industry}"
    if _SPAM_TEXT_RE.search(blob):
        return "spam_text"
    if re.search(r"\binstitute\b", name or "", re.I) and (
        domain.endswith(".org") or "education" in (industry or "").lower()
    ):
        return "edu_institute"
    return None


def _notes_for_name_pattern(notes: str) -> str:
    """Notes for name-pattern checks, without seed-source boilerplate or URLs."""
    text = re.sub(r"https?://\S+", " ", notes or "")
    text = re.sub(r"linkedin company search", " ", text, flags=re.I)
    return text


def _blob(row: Mapping[str, Any]) -> str:
    parts = [
        row.get("company_name") or "",
        row.get("industry") or "",
        row.get("location") or "",
        row.get("notes") or "",
        row.get("source_url") or "",
    ]
    return " ".join(str(p) for p in parts).lower()


def match_company(
    seed_row: Mapping[str, Any],
    rules: IcpRules,
) -> tuple[bool, list[str]]:
    """
    Return (passed, reasons).

    On pass, reasons may include soft notes; on fail, reasons explain every
    hard rejection that fired (first-fail short-circuit for clarity).
    """
    name = (seed_row.get("company_name") or "").strip()
    website = (seed_row.get("website") or "").strip()
    industry = (seed_row.get("industry") or "").strip()
    location = (seed_row.get("location") or "").strip()
    size_raw = seed_row.get("company_size") or seed_row.get("employee_count")
    blob = _blob(seed_row)
    reasons: list[str] = []

    if not name:
        return False, ["missing company_name"]
    if not website:
        return False, ["missing website"]

    # Cheap recruiter / staffing name gate (no website crawl required).
    # Staffing ICPs set allow_recruiter_companies so the target industry is not rejected.
    if not allows_recruiter_companies(rules) and RECRUITER_NAME_RE.search(name):
        return False, ["recruiter_or_staffing_name"]

    # Excluded name / industry patterns. Notes can bury industry on job scrapes.
    # Do not scan source_url: a linkedin\b rule would reject every LinkedIn-sourced seed.
    pattern_str = rules.merged_excluded_name_pattern()
    if pattern_str:
        pattern = re.compile(pattern_str, re.I)
        notes = _notes_for_name_pattern(str(seed_row.get("notes") or ""))
        if pattern.search(name) or pattern.search(industry) or pattern.search(notes):
            return False, ["excluded_name_pattern"]

    # Excluded keywords (substring in company blob)
    for kw in rules.excluded_keywords:
        if kw and kw.lower() in blob:
            return False, [f"excluded_keyword:{kw}"]

    domain = _root_domain(website)
    if domain in rules.merged_excluded_domains():
        return False, [f"excluded_domain:{domain}"]

    for suffix in rules.excluded_website_suffixes:
        s = suffix.lower().removeprefix(".")
        if s and domain.endswith(s):
            return False, [f"excluded_website_suffix:{s}"]

    industry_lower = industry.lower()
    if not industry_lower:
        # LinkedIn jobs often only put industry into notes
        m = re.search(r"industry:\s*([^|]+)", blob, re.I)
        if m:
            industry_lower = m.group(1).strip().lower()
    blocked = excluded_industry_hit(
        industry_lower, list(rules.excluded_industries), rules.industries_list()
    )
    if blocked:
        return False, [f"excluded_industry:{blocked}"]

    if targets_operators_not_agencies(rules):
        provider_text = " ".join(
            str(seed_row.get(key) or "")
            for key in (
                "company_name",
                "industry",
                "website_summary",
                "about_summary",
                "services_offered",
                "company_linkedin_description",
            )
        )
        if SERVICE_PROVIDER_RE.search(provider_text):
            return False, ["service_provider"]

    # Required name signal
    signal_re = rules.name_signal_re()
    if signal_re is not None:
        if not (signal_re.search(name) or signal_re.search(industry)):
            return False, ["missing_name_signal"]

    # Company size bounds (only when size is known)
    emp = _parse_employee_count(size_raw)
    if emp is not None:
        if rules.company_size_min is not None and emp < rules.company_size_min:
            return False, [f"company_size_below_min:{emp}<{rules.company_size_min}"]
        if rules.company_size_max is not None and emp > rules.company_size_max:
            return False, [f"company_size_above_max:{emp}>{rules.company_size_max}"]
        if emp < 10:
            spam = small_company_spam_reason(name, website, industry)
            if spam:
                return False, [f"small_company_spam:{spam}"]

    # Location — hard gate when require_location_match (company location only;
    # do not trust search geo_segment — jobs/maps queries stamp every row).
    if rules.require_location_match:
        from agent.utils.location_match import location_matches_icp

        if not location_matches_icp(
            location,
            website=website,
            primary_location=rules.location,
            location_hints=rules.location_hints,
            contact_locations=rules.contact_locations,
        ):
            return False, ["location_mismatch"]

    if rules.require_include_keywords and rules.keywords:
        if not any(kw.lower() in blob for kw in rules.keywords if kw):
            return False, ["missing_include_keyword"]

    if rules.require_industry_match and rules.industries_list() and industry_lower:
        if not industry_matches_allowed(industry_lower, rules.industries_list()):
            return False, ["industry_mismatch"]

    if rules.apply_weak_seed_checks:
        weak = weak_seed_reason(
            name,
            website,
            industry,
            enabled=True,
            rule_pack=rules.weak_seed_rule_pack,
            extra_patterns=rules.weak_seed_extra_patterns,
        )
        if weak:
            return False, [f"weak_seed:{weak}"]

    reasons.append("icp_match")
    return True, reasons


def match_reason_summary(passed: bool, reasons: list[str]) -> str:
    if passed:
        return "pass"
    return ";".join(reasons) if reasons else "reject"
