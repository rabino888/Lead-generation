"""
Hard gate before Apollo — drop megacorps, recruiters, oversize, and re-billed domains.

Runs after stage 03 dedupe; only filtered rows should reach stage_apollo.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

from agent.utils.icp_match import RECRUITER_NAME_RE, _parse_employee_count, _root_domain
from agent.utils.icp_rules import IcpRules
from agent.utils.icp_seed_quality import weak_seed_reason


def _employee_from_notes(notes: str) -> Optional[int]:
    match = re.search(r"employees:\s*(\d+)", notes or "", re.I)
    if match:
        return int(match.group(1))
    return None


def _load_linkedin_jobs_employee_index(campaign_dir: Path) -> tuple[dict[str, int], dict[str, int]]:
    """Domain/name → employee count from linkedin_jobs_raw.json if present."""
    raw_path = campaign_dir / "linkedin_jobs_raw.json"
    if not raw_path.exists():
        return {}, {}
    try:
        jobs = json.loads(raw_path.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}
    if not isinstance(jobs, list):
        return {}, {}
    by_domain: dict[str, int] = {}
    by_name: dict[str, int] = {}
    for job in jobs:
        if not isinstance(job, dict):
            continue
        raw = job.get("companyEmployeesCount")
        if raw is None:
            continue
        try:
            count = int(raw)
        except (TypeError, ValueError):
            continue
        website = job.get("companyWebsite") or ""
        parsed = urlparse(website if "://" in website else f"https://{website}")
        domain = parsed.netloc.lower().removeprefix("www.")
        name = (job.get("companyName") or "").strip().lower()
        if domain:
            prev = by_domain.get(domain)
            if prev is None or count < prev:
                by_domain[domain] = count
        if name:
            prev = by_name.get(name)
            if prev is None or count < prev:
                by_name[name] = count
    return by_domain, by_name


def _load_attempted_domains(campaign_dir: Path) -> set[str]:
    """Domains already sent to Apollo in prior runs (from cost_runs apollo batches)."""
    path = campaign_dir / "apollo_attempted_domains.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {d.lower().removeprefix("www.") for d in data.get("domains") or []}
        except Exception:
            pass
    attempted: set[str] = set()
    cost_runs = campaign_dir / "cost_runs.json"
    if cost_runs.exists():
        try:
            manifest = json.loads(cost_runs.read_text(encoding="utf-8"))
            for entry in manifest.get("runs") or []:
                if entry.get("stage") != "apollo_contact":
                    continue
                ap = entry.get("apollo") or {}
                for d in ap.get("attempted_domains") or []:
                    attempted.add(str(d).lower().removeprefix("www."))
        except Exception:
            pass
    return attempted


def resolve_employee_count(
    row: Mapping[str, Any],
    by_domain: dict[str, int],
    by_name: dict[str, int],
) -> Optional[int]:
    """Best employee count for a seed row."""
    notes = row.get("notes") or ""
    from_notes = _employee_from_notes(notes)
    if from_notes is not None:
        return from_notes
    size_raw = row.get("company_size") or row.get("employee_count")
    parsed = _parse_employee_count(size_raw)
    if parsed is not None:
        return parsed
    domain = _root_domain((row.get("website") or "").strip())
    if domain and domain in by_domain:
        return by_domain[domain]
    name = (row.get("company_name") or "").strip().lower()
    if name in by_name:
        return by_name[name]
    return None


def pre_apollo_reject_reason(
    row: Mapping[str, Any],
    rules: IcpRules,
    *,
    by_domain: dict[str, int],
    by_name: dict[str, int],
    attempted_domains: set[str],
    drop_unknown_size: bool = True,
) -> Optional[str]:
    """
    Return a rejection reason string, or None if the row should proceed to Apollo.
    Re-applies size/mega/recruiter gates that LinkedIn job scrapes often bypass.
    """
    name = (row.get("company_name") or "").strip()
    website = (row.get("website") or "").strip()
    if not name or not website:
        return "missing_name_or_website"

    if RECRUITER_NAME_RE.search(name):
        return "recruiter_or_staffing_name"

    domain = _root_domain(website)
    if domain in rules.merged_excluded_domains():
        return f"excluded_domain:{domain}"

    pattern_str = rules.merged_excluded_name_pattern()
    if pattern_str:
        pattern = re.compile(pattern_str, re.I)
        industry = (row.get("industry") or "").strip()
        notes = (row.get("notes") or "").strip()
        if pattern.search(name) or (industry and pattern.search(industry)) or (notes and pattern.search(notes)):
            return "excluded_name_pattern"

    blob = " ".join(
        str(row.get(k) or "")
        for k in ("company_name", "industry", "location", "notes", "source_url")
    ).lower()
    for kw in rules.excluded_keywords:
        if kw and kw.lower() in blob:
            return f"excluded_keyword:{kw}"

    industry_lower = (row.get("industry") or "").strip().lower()
    for blocked in rules.excluded_industries:
        if blocked and blocked.lower() in industry_lower:
            return f"excluded_industry:{blocked}"

    if rules.apply_weak_seed_checks:
        weak = weak_seed_reason(
            name,
            website,
            (row.get("industry") or "").strip(),
            enabled=True,
            rule_pack=rules.weak_seed_rule_pack,
            extra_patterns=rules.weak_seed_extra_patterns,
        )
        if weak:
            return f"weak_seed:{weak}"

    employees = resolve_employee_count(row, by_domain, by_name)
    if employees is not None:
        if rules.company_size_max is not None and employees > rules.company_size_max:
            return f"employees_above_max:{employees}>{rules.company_size_max}"
        if rules.company_size_min is not None and employees < rules.company_size_min:
            return f"employees_below_min:{employees}<{rules.company_size_min}"
    elif drop_unknown_size and rules.company_size_max is not None:
        return "unknown_employee_count"

    if domain and domain in attempted_domains:
        return f"apollo_already_attempted:{domain}"

    return None


def filter_pre_apollo(
    rows: list[dict[str, Any]],
    rules: IcpRules,
    campaign_dir: Path,
    *,
    drop_unknown_size: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Split seed rows into kept (Apollo-eligible) and rejected with reasons.
    """
    by_domain, by_name = _load_linkedin_jobs_employee_index(campaign_dir)
    attempted = _load_attempted_domains(campaign_dir)
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        reason = pre_apollo_reject_reason(
            row,
            rules,
            by_domain=by_domain,
            by_name=by_name,
            attempted_domains=attempted,
            drop_unknown_size=drop_unknown_size,
        )
        if reason:
            out = dict(row)
            out["pre_apollo_reason"] = reason
            rejected.append(out)
        else:
            kept.append(dict(row))
    return kept, rejected


def record_apollo_attempted_domains(campaign_dir: Path, rows: list[Mapping[str, Any]]) -> None:
    """Persist domains sent to Apollo so future runs skip re-billing."""
    path = campaign_dir / "apollo_attempted_domains.json"
    existing: set[str] = set()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            existing = {d.lower().removeprefix("www.") for d in data.get("domains") or []}
        except Exception:
            pass
    for row in rows:
        domain = _root_domain((row.get("website") or "").strip())
        if domain:
            existing.add(domain)
    path.write_text(
        json.dumps({"domains": sorted(existing)}, indent=2),
        encoding="utf-8",
    )
