"""
Cheap firmographic enrichment (foxlabs/company-enrichment via Owler).

Fills website / industry / employee band on seed rows. Useful after Maps or
Jobs discovery when LinkedIn jobs ``companyWebsite`` is wrong vs the company
profile domain.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse


def _root_domain(url: str) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = f"https://{url}"
    return urlparse(url).netloc.lower().removeprefix("www.")


def _normalize_website(url: str) -> str:
    if not url:
        return ""
    url = str(url).strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    parsed = urlparse(url)
    if not parsed.netloc:
        return ""
    return f"https://{parsed.netloc.lower()}"


def merge_firmographics_into_rows(
    rows: list[dict[str, Any]],
    enrichments: list[dict[str, Any]],
    *,
    prefer_enriched_website: bool = True,
) -> list[dict[str, Any]]:
    """
    Match foxlabs rows back onto seeds by domain or fuzzy name.

    When ``prefer_enriched_website`` is True, overwrite seed website with the
    firmographic website (often the local TLD LinkedIn company About shows).
    """
    import re

    def re_norm_name(name: str) -> str:
        return re.sub(r"\s+", " ", (name or "").strip().lower())

    by_domain: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for item in enrichments:
        if not isinstance(item, dict) or item.get("error"):
            continue
        domain = (item.get("domain") or _root_domain(item.get("website") or "")).lower()
        name_key = re_norm_name(item.get("name") or item.get("legalName") or "")
        if domain:
            by_domain[domain] = item
        if name_key:
            by_name[name_key] = item

    out: list[dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        domain = _root_domain(r.get("website") or "")
        name_key = re_norm_name(r.get("company_name") or "")
        hit = by_domain.get(domain) if domain else None
        if hit is None and name_key:
            hit = by_name.get(name_key)
        if not hit:
            out.append(r)
            continue

        enriched_site = _normalize_website(hit.get("website") or "")
        if prefer_enriched_website and enriched_site:
            r["website"] = enriched_site
        if not (r.get("industry") or "").strip() and hit.get("industry"):
            r["industry"] = str(hit.get("industry") or "")
        emp = hit.get("employees") or hit.get("employeesFormatted") or hit.get("employeesRange")
        if not (r.get("company_size") or "").strip() and emp is not None:
            r["company_size"] = str(emp)
        notes = (r.get("notes") or "").strip()
        tag = "firmographics:foxlabs"
        if tag not in notes:
            r["notes"] = f"{notes} | {tag}".strip(" |") if notes else tag
        out.append(r)
    return out


def enrich_seed_rows_firmographics(
    rows: list[dict[str, Any]],
    *,
    max_companies: Optional[int] = None,
    prefer_enriched_website: bool = True,
) -> list[dict[str, Any]]:
    """Call foxlabs and merge results into seed row dicts. No-op if empty / no token."""
    if not rows:
        return rows
    from agent.integrations.apify import company_enrichment_foxlabs

    slice_rows = rows[: max_companies] if max_companies else list(rows)
    names = [str(r.get("company_name") or "").strip() for r in slice_rows]
    names = [n for n in names if n]
    domains = []
    for r in slice_rows:
        d = _root_domain(r.get("website") or "")
        if d:
            domains.append(d)
    enrichments = company_enrichment_foxlabs(
        company_names=names,
        company_domains=domains or None,
        max_results=len(slice_rows),
    )
    if not enrichments:
        return rows
    merged = merge_firmographics_into_rows(
        slice_rows,
        enrichments,
        prefer_enriched_website=prefer_enriched_website,
    )
    if max_companies and len(rows) > max_companies:
        return merged + list(rows[max_companies:])
    return merged
