"""
Check a finished lead list against the enrichment modules that were turned on.

A list can complete with a Google Sheet while posts, jobs, or social modules
stored nothing. This reads the delivered rows and says which modules landed.
"""
from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional


def _cell(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = row.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return ""


def _has_linkedin_posts(row: dict[str, Any]) -> bool:
    return bool(_cell(row, "decision_maker_linkedin_post_summary", "linkedin_post_summary"))


def _posts_applicable(row: dict[str, Any]) -> bool:
    return bool(_cell(row, "decision_maker_linkedin", "linkedin_url"))


def _has_dm_profile(row: dict[str, Any]) -> bool:
    return bool(
        _cell(row, "decision_maker_linkedin_headline", "decision_maker_linkedin_about", "linkedin_headline")
    )


def _dm_profile_applicable(row: dict[str, Any]) -> bool:
    return _posts_applicable(row)


def _has_company_profile(row: dict[str, Any]) -> bool:
    return bool(_cell(row, "company_linkedin_description"))


def _company_applicable(row: dict[str, Any]) -> bool:
    return bool(_cell(row, "company_name"))


def _has_company_jobs(row: dict[str, Any]) -> bool:
    hiring = _cell(row, "company_is_hiring").lower()
    if hiring in {"yes", "no"}:
        return True
    return bool(_cell(row, "company_open_jobs_summary"))


def _has_website(row: dict[str, Any]) -> bool:
    return bool(_cell(row, "website_summary", "about_summary", "services_offered"))


def _has_phone(row: dict[str, Any]) -> bool:
    return bool(_cell(row, "decision_maker_direct_phone", "decision_maker_mobile_phone"))


def _social_applicable(row: dict[str, Any], column: str) -> bool:
    return bool(_cell(row, column))


def _has_social_summary(row: dict[str, Any], column: str) -> bool:
    return bool(_cell(row, column))


def _check_spec() -> list[dict[str, Any]]:
    return [
        {
            "module_id": "linkedin_dm_posts",
            "label": "Decision-maker LinkedIn posts",
            "applicable": _posts_applicable,
            "evidence": _has_linkedin_posts,
            "empty_detail": "no post summary was stored for decision-makers who have a LinkedIn URL",
        },
        {
            "module_id": "linkedin_company_jobs",
            "label": "Company LinkedIn jobs",
            "applicable": _company_applicable,
            "evidence": _has_company_jobs,
            "empty_detail": (
                "no company has is_hiring set to yes or no, so the jobs lookup was not written"
            ),
        },
        {
            "module_id": "linkedin_dm_profile",
            "label": "Decision-maker LinkedIn profile",
            "applicable": _dm_profile_applicable,
            "evidence": _has_dm_profile,
            "empty_detail": "no headline or about text was stored",
        },
        {
            "module_id": "linkedin_company_profile",
            "label": "Company LinkedIn profile",
            "applicable": _company_applicable,
            "evidence": _has_company_profile,
            "empty_detail": "no company LinkedIn description was stored",
        },
        {
            "module_id": "website_scrape",
            "label": "Website scrape",
            "applicable": _company_applicable,
            "evidence": _has_website,
            "empty_detail": "no website summary was stored",
        },
        {
            "module_id": "instagram_company_posts",
            "label": "Instagram company posts",
            "applicable": lambda row: _social_applicable(row, "social_instagram"),
            "evidence": lambda row: _has_social_summary(row, "instagram_post_summary"),
            "empty_detail": "Instagram URLs were found but no post summary was stored",
            "no_source_ok": True,
        },
        {
            "module_id": "facebook_company_posts",
            "label": "Facebook company posts",
            "applicable": lambda row: _social_applicable(row, "social_facebook"),
            "evidence": lambda row: _has_social_summary(row, "facebook_post_summary"),
            "empty_detail": "Facebook URLs were found but no post summary was stored",
            "no_source_ok": True,
        },
        {
            "module_id": "meta_ads",
            "label": "Meta ads",
            "applicable": _company_applicable,
            "evidence": lambda row: (row.get("meta_ads_active") or "").strip().lower() in {"yes", "no"},
            "empty_detail": "the ads lookup was not written",
        },
        {
            "module_id": "apollo_phone",
            "label": "Apollo phone",
            "applicable": _company_applicable,
            "evidence": _has_phone,
            "empty_detail": "no direct or mobile phone was stored",
        },
    ]


def _module_enabled(modules: dict[str, Any], module_id: str) -> bool:
    if module_id in modules:
        return bool(modules.get(module_id))
    aliases = {
        "linkedin_dm_posts": "apify_dm_posts",
        "linkedin_company_jobs": "apify_company_jobs",
        "linkedin_dm_profile": "apify_dm_profile",
        "linkedin_company_profile": "apify_company_profile",
    }
    alias = aliases.get(module_id)
    if alias and alias in modules:
        return bool(modules.get(alias))
    return False


def audit_enriched_rows(plan: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare enabled enrichment modules to columns on the delivered rows."""
    modules = (plan or {}).get("modules") or {}
    checks: list[dict[str, Any]] = []
    for spec in _check_spec():
        module_id = spec["module_id"]
        if not _module_enabled(modules, module_id):
            checks.append(
                {
                    "module_id": module_id,
                    "label": spec["label"],
                    "enabled": False,
                    "status": "off",
                    "applicable": 0,
                    "with_evidence": 0,
                    "detail": "Module was off for this list.",
                }
            )
            continue
        applicable_rows = [row for row in rows if spec["applicable"](row)]
        with_evidence = sum(1 for row in applicable_rows if spec["evidence"](row))
        applicable = len(applicable_rows)
        if applicable == 0 and spec.get("no_source_ok"):
            status = "no_source"
            detail = "Module was on, and no source URL was found on the delivered rows."
        elif applicable == 0:
            status = "fail"
            detail = "Module was on, and there were no delivered rows to check."
        elif with_evidence == 0:
            status = "fail"
            detail = f"Module was on, and {spec['empty_detail']} ({with_evidence}/{applicable})."
        elif with_evidence < applicable:
            status = "partial"
            detail = f"{with_evidence}/{applicable} delivered rows have this field."
        else:
            status = "ok"
            detail = f"{with_evidence}/{applicable} delivered rows have this field."
        checks.append(
            {
                "module_id": module_id,
                "label": spec["label"],
                "enabled": True,
                "status": status,
                "applicable": applicable,
                "with_evidence": with_evidence,
                "detail": detail,
            }
        )

    failed = [c for c in checks if c["status"] == "fail"]
    partial = [c for c in checks if c["status"] in {"partial", "no_source"}]
    if failed:
        summary = "Enrichment check failed: " + "; ".join(
            f"{c['label']} ({c['with_evidence']}/{c['applicable']})" for c in failed
        )
    elif partial:
        summary = "Enrichment check passed with gaps: " + "; ".join(
            f"{c['label']} ({c['status']})" for c in partial
        )
    else:
        enabled = [c for c in checks if c["enabled"]]
        summary = (
            "Enrichment check passed."
            if enabled
            else "Enrichment check: no optional modules were on."
        )
    return {
        "schema_version": 1,
        "ok": not failed,
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "row_count": len(rows),
        "summary": summary,
        "checks": checks,
    }


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def audit_delivery_csv(plan: dict[str, Any], csv_path: Optional[Path]) -> dict[str, Any]:
    rows = _read_rows(csv_path) if csv_path else []
    audit = audit_enriched_rows(plan, rows)
    audit["csv_path"] = str(csv_path) if csv_path else ""
    return audit


def write_enrichment_audit(campaign_dir: Path, audit: dict[str, Any]) -> Path:
    path = campaign_dir / "enrichment_audit.json"
    path.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def delivery_csv_for_campaign(campaign_dir: Path) -> Optional[Path]:
    """Prefer the contactable stage after smoke rows are merged back in."""
    stages = campaign_dir / "stages"
    for name in (
        "04_apollo_contactable.csv",
        "05_enriched.csv",
        "06_scored.csv",
        "04_smoke_contactable.csv",
    ):
        path = stages / name
        if path.is_file() and path.stat().st_size > 32:
            return path
    return None
