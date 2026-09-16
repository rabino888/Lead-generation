"""
ICP depth / readiness for portal gating.

Portal must not run (or offer as “existing”) a thin stub — Automata-depth
fields from `.cursor/skills/icp-draft` / SOP-ICP-PLANNING.md.
"""
from __future__ import annotations

from typing import Any, Optional


def _as_list(val: Any) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return [x for x in val if x is not None and str(x).strip() != ""]
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return []


def assess_icp_depth(
    icp: Optional[dict[str, Any]],
    *,
    campaign_type: str = "company_outreach",
) -> dict[str, Any]:
    """
    Return readiness for list-building.

    ``ready`` is True only when core Automata-depth fields are present.
    ``missing`` lists human-readable gaps; ``remedy`` is operator copy.
    """
    data = icp if isinstance(icp, dict) else {}
    ctype = (campaign_type or "company_outreach").strip() or "company_outreach"
    missing: list[str] = []

    if ctype == "talent_search":
        pm = data.get("person_match") if isinstance(data.get("person_match"), dict) else {}
        if not _as_list(data.get("job_titles")) and not _as_list(pm.get("skills")):
            missing.append("job titles or person_match.skills")
        if not _as_list(data.get("contact_locations")) and not (data.get("location") or "").strip():
            missing.append("contact_locations (person geo)")
        ready = not missing
        remedy = (
            "Complete the talent ICP in Cursor (icp-draft skill): titles/skills and "
            "contact locations, then Reload ICP from disk."
            if not ready
            else ""
        )
        return {
            "ready": ready,
            "missing": missing,
            "remedy": remedy,
            "campaign_type": ctype,
        }

    industries = _as_list(data.get("industries"))
    titles = _as_list(data.get("job_titles"))
    contact_locs = _as_list(data.get("contact_locations"))
    include_kw = _as_list(data.get("include_keywords"))
    excluded_ind = _as_list(data.get("excluded_industries"))
    exclude_kw = _as_list(data.get("exclude_keywords"))
    excluded_domains = _as_list(data.get("excluded_domains"))
    name_pat = (data.get("excluded_name_patterns") or "").strip()
    size_min = data.get("company_size_min")
    if size_min is None:
        size_min = data.get("size_min")
    size_max = data.get("company_size_max")
    if size_max is None:
        size_max = data.get("size_max")
    employee_ranges = _as_list(data.get("employee_ranges"))

    geo = data.get("geo") if isinstance(data.get("geo"), dict) else {}
    primary = (geo.get("primary_location") or data.get("location") or "").strip()
    country_codes = geo.get("country_codes") if isinstance(geo.get("country_codes"), dict) else {}
    location_hints = _as_list(geo.get("location_hints"))

    if len(industries) < 1:
        missing.append("industries (target sectors)")
    if len(titles) < 2:
        missing.append("job_titles (at least 2 decision-maker titles)")
    if len(contact_locs) < 1:
        missing.append("contact_locations (Apollo person geo)")
    if size_min is None or size_max is None:
        if not employee_ranges:
            missing.append("company_size_min and company_size_max (or employee_ranges)")
    if not primary and not country_codes and not location_hints:
        missing.append("geo (primary_location, country_codes, or location_hints)")
    if len(include_kw) < 1:
        missing.append("include_keywords (company text signals)")
    if not (excluded_ind or exclude_kw or name_pat or excluded_domains):
        missing.append(
            "excludes (excluded_industries, exclude_keywords, "
            "excluded_name_patterns, or excluded_domains)"
        )

    ready = not missing
    remedy = ""
    if not ready:
        remedy = (
            "This ICP is too thin to build a list. Draft Automata-depth icp.json in Cursor "
            "(icp-draft skill / SOP-ICP-PLANNING.md), then click Reload ICP from disk. "
            "Missing: " + "; ".join(missing) + "."
        )

    return {
        "ready": ready,
        "missing": missing,
        "remedy": remedy,
        "campaign_type": ctype,
    }


def assert_icp_ready_for_run(
    icp: Optional[dict[str, Any]],
    *,
    campaign_type: str = "company_outreach",
) -> None:
    """Raise ValueError with operator remedy when ICP is thin."""
    assessment = assess_icp_depth(icp, campaign_type=campaign_type)
    if not assessment["ready"]:
        raise ValueError(assessment["remedy"] or "ICP is incomplete.")
