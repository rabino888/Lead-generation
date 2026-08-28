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


def _root_domain(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if url.startswith("http") else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def _parse_employee_count(size: str | int | None) -> int | None:
    if size is None or size == "":
        return None
    if isinstance(size, int):
        return size
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
        return int(m.group(1))
    return None


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

    # Excluded name / industry patterns
    pattern_str = rules.merged_excluded_name_pattern()
    if pattern_str:
        pattern = re.compile(pattern_str, re.I)
        if pattern.search(name) or pattern.search(industry):
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
    for blocked in rules.excluded_industries:
        if blocked and blocked.lower() in industry_lower:
            return False, [f"excluded_industry:{blocked}"]

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

    # Location hints — opt-in hard gate (curated seeds often omit reliable geo)
    if rules.require_location_match:
        hints = [h.lower() for h in rules.location_hints if h]
        if rules.location:
            hints.append(rules.location.lower())
        if hints:
            loc_l = location.lower()
            geo = (seed_row.get("geo_segment") or "").lower()
            hay = f"{loc_l} {geo}"
            if not any(h in hay for h in hints):
                return False, ["location_mismatch"]

    if rules.require_include_keywords and rules.keywords:
        if not any(kw.lower() in blob for kw in rules.keywords if kw):
            return False, ["missing_include_keyword"]

    if rules.require_industry_match and rules.industries_list() and industry_lower:
        allowed = [i.lower() for i in rules.industries_list() if i]
        if allowed and not any(a in industry_lower or industry_lower in a for a in allowed):
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
