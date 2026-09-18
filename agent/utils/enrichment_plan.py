"""
Enrichment plan persistence and pre-run cost estimates.

Contracts: SPEC-dashboard.md, SPEC-talent-search.md
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ESSENTIAL_MODULES = ("icp_match", "dedupe")

DEFAULT_MODULES_COMPANY: dict[str, bool] = {
    "icp_match": True,
    "dedupe": True,
    "apollo_email": True,
    "apify_dm_profile": True,
    "apify_dm_posts": True,
    "website_scrape": True,
    "apify_company_profile": False,
    "apify_company_jobs": False,
    "apollo_phone": False,
}

DEFAULT_MODULES_TALENT: dict[str, bool] = {
    "icp_match": True,
    "dedupe": True,
    "apollo_email": True,
    "apify_dm_profile": True,
    "apify_dm_posts": False,
    "website_scrape": False,
    "apify_company_profile": False,
    "apify_company_jobs": False,
    "apollo_phone": True,
}

DEFAULT_TALENT_PASS_RATES = {
    "after_dedupe": 0.90,
    "after_icp": 0.40,
    "email_success": 0.70,
}


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def unit_rates() -> dict[str, float]:
    """
    Typical (mid) unit rates for pre-run estimates. Confidence is always est.

    Defaults calibrated from Automata batch2 enrich (50 leads, Apify API truth):
      apimaestro person ≈ $0.005/run, posts ≈ $0.009, company ≈ $0.004
      blended enrich ~$0.13/lead (see data/campaigns/automata/cost_log_run_20260719_*).
    Override via BUILDER_* env vars when plans change.
    """
    return {
        "apollo_per_credit": _env_float("APOLLO_CREDIT_USD", 0.02),
        "apollo_credits_per_lead": _env_float("BUILDER_APOLLO_CREDITS_PER_LEAD", 1.5),
        "apify_dm_profile_usd": _env_float("BUILDER_APIFY_DM_PROFILE_USD", 0.005),
        "apify_dm_posts_usd": _env_float("BUILDER_APIFY_DM_POSTS_USD", 0.009),
        "apify_company_profile_usd": _env_float("BUILDER_APIFY_COMPANY_PROFILE_USD", 0.004),
        "apify_company_jobs_usd": _env_float("BUILDER_APIFY_COMPANY_JOBS_USD", 0.02),
        "apify_website_per_page_usd": _env_float("BUILDER_APIFY_WEBSITE_PAGE_USD", 0.004),
        "firecrawl_per_page": _env_float("FIRECRAWL_PAGE_USD", 0.01),
        "llm_website_per_lead_usd": _env_float("BUILDER_LLM_WEBSITE_PER_LEAD_USD", 0.023),
        "website_pages_per_company": _env_float(
            "WEBSITE_MAX_PAGES",
            _env_float("FIRECRAWL_MAX_PAGES", 8.0),
        ),
        "talent_seed_scrape_usd": _env_float("BUILDER_TALENT_SEED_SCRAPE_USD", 0.01),
        "talent_profile_usd": _env_float("BUILDER_TALENT_PROFILE_USD", 0.005),
    }


def plan_path(campaign_dir: Path) -> Path:
    return campaign_dir / "enrichment_plan.json"


def default_plan(
    campaign_id: str,
    *,
    campaign_type: str = "company_outreach",
) -> dict[str, Any]:
    ctype = (
        "talent_search"
        if campaign_type == "talent_search"
        else "company_outreach"
    )
    modules = (
        dict(DEFAULT_MODULES_TALENT)
        if ctype == "talent_search"
        else dict(DEFAULT_MODULES_COMPANY)
    )
    return {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "campaign_type": ctype,
        "updated_at_utc": None,
        "target_leads": 50 if ctype == "company_outreach" else None,
        "seed_count": 500 if ctype == "talent_search" else None,
        "website_crawler": "apify" if ctype == "company_outreach" else None,
        "modules": modules,
        "talent_pass_rates": (
            dict(DEFAULT_TALENT_PASS_RATES) if ctype == "talent_search" else None
        ),
        "talent_seed": None,
        "notes": "",
    }


def load_plan(campaign_dir: Path, campaign_id: str) -> dict[str, Any]:
    path = plan_path(campaign_dir)
    if not path.is_file():
        ctype = _infer_campaign_type(campaign_dir)
        return default_plan(campaign_id, campaign_type=ctype)
    data = json.loads(path.read_text(encoding="utf-8"))
    return normalize_plan(data, campaign_id=campaign_id)


def _infer_campaign_type(campaign_dir: Path) -> str:
    for name in ("campaign.json", "icp.json"):
        path = campaign_dir / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        raw = (data.get("campaign_type") or "").strip()
        if raw == "talent_search":
            return "talent_search"
    return "company_outreach"


def normalize_plan(data: dict[str, Any], *, campaign_id: str) -> dict[str, Any]:
    ctype = data.get("campaign_type") or "company_outreach"
    if ctype not in ("company_outreach", "talent_search"):
        ctype = "company_outreach"
    base = default_plan(campaign_id, campaign_type=ctype)
    modules = dict(base["modules"])
    incoming = data.get("modules") or {}
    for key, val in incoming.items():
        if key in modules:
            modules[key] = bool(val)
    for key in ESSENTIAL_MODULES:
        modules[key] = True

    rates = data.get("talent_pass_rates")
    if ctype == "talent_search":
        merged_rates = dict(DEFAULT_TALENT_PASS_RATES)
        if isinstance(rates, dict):
            for k, v in rates.items():
                if k in merged_rates:
                    try:
                        merged_rates[k] = max(0.0, min(1.0, float(v)))
                    except (TypeError, ValueError):
                        pass
    else:
        merged_rates = None

    crawler = data.get("website_crawler") or base["website_crawler"]
    if crawler not in ("apify", "firecrawl", None):
        crawler = "apify"

    target = data.get("target_leads", base["target_leads"])
    seed = data.get("seed_count", base["seed_count"])
    try:
        target = int(target) if target is not None else None
    except (TypeError, ValueError):
        target = base["target_leads"]
    try:
        seed = int(seed) if seed is not None else None
    except (TypeError, ValueError):
        seed = base["seed_count"]

    return {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "campaign_type": ctype,
        "updated_at_utc": data.get("updated_at_utc"),
        "target_leads": target,
        "seed_count": seed,
        "website_crawler": crawler if ctype == "company_outreach" else None,
        "modules": modules,
        "talent_pass_rates": merged_rates,
        "talent_seed": data.get("talent_seed"),
        "notes": data.get("notes") or "",
    }


def validate_plan(plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    modules = plan.get("modules") or {}
    for key in ESSENTIAL_MODULES:
        if not modules.get(key):
            errors.append(f"{key} must remain on")
    ctype = plan.get("campaign_type")
    if ctype not in ("company_outreach", "talent_search"):
        errors.append("campaign_type must be company_outreach or talent_search")
    if ctype == "company_outreach":
        n = plan.get("target_leads")
        if n is None or int(n) < 0:
            errors.append("target_leads must be >= 0")
    if ctype == "talent_search":
        n = plan.get("seed_count")
        if n is None or int(n) < 0:
            errors.append("seed_count must be >= 0")
    return errors


def save_plan(campaign_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    campaign_id = plan.get("campaign_id") or campaign_dir.name
    normalized = normalize_plan(plan, campaign_id=campaign_id)
    errors = validate_plan(normalized)
    if errors:
        raise ValueError("; ".join(errors))
    normalized["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    campaign_dir.mkdir(parents=True, exist_ok=True)
    path = plan_path(campaign_dir)
    path.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
    return normalized


def estimate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return CostEstimateResponse (SPEC-dashboard §7.2)."""
    normalized = normalize_plan(plan, campaign_id=plan.get("campaign_id") or "unknown")
    rates = unit_rates()
    if normalized["campaign_type"] == "talent_search":
        return _estimate_talent(normalized, rates)
    return _estimate_company(normalized, rates)


def _line(
    module_id: str,
    label: str,
    *,
    unit_usd: float,
    units: float,
    line_usd: float,
    basis: str,
) -> dict[str, Any]:
    return {
        "module_id": module_id,
        "label": label,
        "unit_usd": round(unit_usd, 6),
        "units": round(units, 4),
        "line_usd": round(line_usd, 4),
        "basis": basis,
    }


def _estimate_company(plan: dict[str, Any], rates: dict[str, float]) -> dict[str, Any]:
    m = plan["modules"]
    n = max(0, int(plan.get("target_leads") or 0))
    crawler = plan.get("website_crawler") or "apify"
    lines: list[dict[str, Any]] = [
        _line("icp_match", "ICP match", unit_usd=0, units=0, line_usd=0, basis="local"),
        _line("dedupe", "Deduplication", unit_usd=0, units=0, line_usd=0, basis="local"),
    ]
    warnings: list[str] = []

    if m.get("apollo_email"):
        credits = n * rates["apollo_credits_per_lead"]
        lines.append(
            _line(
                "apollo_email",
                "Email reveal",
                unit_usd=rates["apollo_per_credit"],
                units=credits,
                line_usd=credits * rates["apollo_per_credit"],
                basis="apollo_credits_est",
            )
        )
    else:
        warnings.append(
            "Email reveal is OFF — LinkedIn-only mode; not contactable under DECISIONS D3."
        )

    if m.get("apify_dm_profile"):
        lines.append(
            _line(
                "apify_dm_profile",
                "LinkedIn DM profile",
                unit_usd=rates["apify_dm_profile_usd"],
                units=n,
                line_usd=n * rates["apify_dm_profile_usd"],
                basis="1 person-profile run / lead",
            )
        )
    if m.get("apify_dm_posts"):
        lines.append(
            _line(
                "apify_dm_posts",
                "LinkedIn DM posts",
                unit_usd=rates["apify_dm_posts_usd"],
                units=n,
                line_usd=n * rates["apify_dm_posts_usd"],
                basis="1 posts run / lead",
            )
        )
    if m.get("website_scrape"):
        pages = rates["website_pages_per_company"]
        per_page = (
            rates["firecrawl_per_page"]
            if crawler == "firecrawl"
            else rates["apify_website_per_page_usd"]
        )
        page_units = n * pages
        lines.append(
            _line(
                "website_scrape",
                f"Website scrape ({crawler})",
                unit_usd=per_page,
                units=page_units,
                line_usd=page_units * per_page,
                basis=f"{int(pages)} pages/company",
            )
        )
        lines.append(
            _line(
                "website_llm",
                "Website LLM read",
                unit_usd=rates["llm_website_per_lead_usd"],
                units=n,
                line_usd=n * rates["llm_website_per_lead_usd"],
                basis="llm_flat_rate_est",
            )
        )
        if crawler == "firecrawl" and not (os.environ.get("FIRECRAWL_API_KEY") or "").strip():
            warnings.append("Firecrawl selected but FIRECRAWL_API_KEY is not set.")
    if m.get("apify_company_profile"):
        lines.append(
            _line(
                "apify_company_profile",
                "LinkedIn company profile",
                unit_usd=rates["apify_company_profile_usd"],
                units=n,
                line_usd=n * rates["apify_company_profile_usd"],
                basis="1 company run / lead",
            )
        )
    if m.get("apify_company_jobs"):
        lines.append(
            _line(
                "apify_company_jobs",
                "LinkedIn company jobs",
                unit_usd=rates["apify_company_jobs_usd"],
                units=n,
                line_usd=n * rates["apify_company_jobs_usd"],
                basis="1 jobs run / lead",
            )
        )
    if m.get("apollo_phone"):
        lines.append(
            _line(
                "apollo_phone",
                "Phone reveal",
                unit_usd=rates["apollo_per_credit"],
                units=n,
                line_usd=n * rates["apollo_per_credit"],
                basis="phone_credit_est",
            )
        )
        if not (os.environ.get("APOLLO_PHONE_WEBHOOK_URL") or "").strip():
            warnings.append("Phone reveal ON but APOLLO_PHONE_WEBHOOK_URL is not set.")

    total = sum(float(x["line_usd"]) for x in lines)
    per = (total / n) if n else 0.0
    return {
        "campaign_id": plan["campaign_id"],
        "campaign_type": "company_outreach",
        "confidence": "est",
        "currency": "USD",
        "billable_leads": n,
        "usd_per_lead": round(per, 4),
        "usd_per_lead_low": None,
        "usd_per_lead_high": None,
        "usd_run_total": round(total, 4),
        "line_items": lines,
        "warnings": warnings,
        "funnel": None,
    }


def _estimate_talent(plan: dict[str, Any], rates: dict[str, float]) -> dict[str, Any]:
    seeds = max(0, int(plan.get("seed_count") or 0))
    pr = plan.get("talent_pass_rates") or dict(DEFAULT_TALENT_PASS_RATES)
    after_dedupe = seeds * float(pr.get("after_dedupe", 0.9))
    survivors = after_dedupe * float(pr.get("after_icp", 0.4))
    emails = survivors * float(pr.get("email_success", 0.7))
    phone_on = bool((plan.get("modules") or {}).get("apollo_phone"))
    phones = emails if phone_on else 0.0

    seed_scrape = seeds * rates["talent_seed_scrape_usd"]
    profile = survivors * rates["talent_profile_usd"]
    email_reveal = survivors * rates["apollo_credits_per_lead"] * rates["apollo_per_credit"]
    phone_reveal = phones * rates["apollo_per_credit"]
    total = seed_scrape + profile + email_reveal + phone_reveal
    per = (total / emails) if emails > 0 else 0.0

    lines = [
        _line(
            "talent_seed",
            "Initial seed scrape",
            unit_usd=rates["talent_seed_scrape_usd"],
            units=seeds,
            line_usd=seed_scrape,
            basis="seed_scrape_est",
        ),
        _line("dedupe", "Deduplication", unit_usd=0, units=0, line_usd=0, basis="local"),
        _line("icp_match", "ICP match", unit_usd=0, units=0, line_usd=0, basis="local"),
        _line(
            "talent_profile",
            "Personal profile scrape",
            unit_usd=rates["talent_profile_usd"],
            units=survivors,
            line_usd=profile,
            basis="profile_icp_survivors",
        ),
        _line(
            "apollo_email",
            "Apollo email reveal",
            unit_usd=rates["apollo_per_credit"],
            units=survivors * rates["apollo_credits_per_lead"],
            line_usd=email_reveal,
            basis="apollo_credits_est",
        ),
    ]
    if phone_on:
        lines.append(
            _line(
                "apollo_phone",
                "Apollo phone reveal",
                unit_usd=rates["apollo_per_credit"],
                units=phones,
                line_usd=phone_reveal,
                basis="phone_credit_est",
            )
        )

    warnings = [
        "Profile spend applies to ICP survivors, not every seed.",
        "Pre-run estimate — seed count × assumed pass rates. Confidence est.",
    ]
    if phone_on and not (os.environ.get("APOLLO_PHONE_WEBHOOK_URL") or "").strip():
        warnings.append("Phone reveal ON but APOLLO_PHONE_WEBHOOK_URL is not set.")

    return {
        "campaign_id": plan["campaign_id"],
        "campaign_type": "talent_search",
        "confidence": "est",
        "currency": "USD",
        "billable_leads": int(round(emails)),
        "usd_per_lead": round(per, 4),
        "usd_per_lead_low": None,
        "usd_per_lead_high": None,
        "usd_run_total": round(total, 4),
        "line_items": lines,
        "warnings": warnings,
        "funnel": {
            "seeds": seeds,
            "after_dedupe": round(after_dedupe, 2),
            "after_icp": round(survivors, 2),
            "email_successes": round(emails, 2),
            "phone_reveals": round(phones, 2),
        },
    }


def list_campaign_dirs(campaigns_root: Path) -> list[Path]:
    """
    List campaign folders.

    When ``campaigns_root`` is ``…/data/campaigns``, also include
    ``…/data/clients/*/campaigns/*`` (client-first layout wins on id clash).
    """
    from agent.utils.client_store import list_all_campaign_dirs

    root = Path(campaigns_root)
    if root.name == "campaigns":
        return list_all_campaign_dirs(root.parent)
    if not root.is_dir():
        return []
    return sorted(
        [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")],
        key=lambda p: p.name.lower(),
    )


def _list_run_summary(campaign_dir: Path, campaign_id: str) -> dict[str, Any]:
    try:
        from agent.utils.list_run import load_list_run

        return load_list_run(campaign_dir, campaign_id)
    except Exception:
        return {
            "schema_version": 1,
            "campaign_id": campaign_id,
            "phase": "idle",
            "smoke_leads": 2,
            "target_leads": 50,
            "message": "",
            "error": "",
        }


def _seed_plan_summary(campaign_dir: Path, icp: dict[str, Any]) -> dict[str, Any]:
    """How the gated run will obtain company seeds (dashboard Confirm)."""
    from agent.utils.linkedin_company_seeds import preview_auto_linkedin_companies_seed
    from agent.utils.seeds import load_company_seeds_csv

    stages = campaign_dir / "stages" / "01_raw_seeds.csv"
    if stages.is_file():
        try:
            import csv as _csv

            with stages.open(encoding="utf-8-sig", newline="") as f:
                n = sum(1 for r in _csv.DictReader(f) if (r.get("company_name") or "").strip())
            if n:
                return {
                    "ok": True,
                    "mode": "stage_csv",
                    "count": n,
                    "message": f"Using existing stages/01_raw_seeds.csv ({n} companies).",
                }
        except Exception:
            pass

    seeds_csv = campaign_dir / "seeds.csv"
    if seeds_csv.is_file():
        try:
            rows = load_company_seeds_csv(seeds_csv)
            if rows:
                return {
                    "ok": True,
                    "mode": "csv_ingest",
                    "count": len(rows),
                    "message": f"Using seeds.csv ({len(rows)} companies) — free ingest.",
                }
        except Exception:
            pass

    ss_path = campaign_dir / "seed_sources.json"
    if ss_path.is_file():
        try:
            cfg = json.loads(ss_path.read_text(encoding="utf-8"))
            for entry in cfg.get("sources") or []:
                if not isinstance(entry, dict) or entry.get("enabled") is False:
                    continue
                sid = (entry.get("id") or entry.get("type") or "").strip()
                conf = entry.get("config") if isinstance(entry.get("config"), dict) else {}
                if sid == "linkedin_jobs":
                    continue
                if sid == "linkedin_companies":
                    queries = list(conf.get("queries") or [])
                    urls = list(conf.get("urls") or [])
                    if queries or urls:
                        return {
                            "ok": True,
                            "mode": "linkedin_companies",
                            "query_count": len(queries) or len(urls),
                            "urls": (urls or [q.get("url") for q in queries])[:8],
                            "count": int(conf.get("max_results") or 80),
                            "message": (
                                f"Configured linkedin_companies — "
                                f"{len(queries) or len(urls)} company-search quer(y/ies), "
                                "Apify scrape on Start (paid; not jobs)."
                            ),
                        }
                if sid == "google_maps":
                    queries = list(conf.get("queries") or [])
                    if queries or conf.get("keyword"):
                        return {
                            "ok": True,
                            "mode": "google_maps",
                            "query_count": len(queries) or 1,
                            "queries": queries[:8],
                            "count": int(conf.get("max_results") or 80),
                            "message": (
                                f"Configured google_maps — {len(queries) or 1} quer(y/ies), "
                                "Apify company discovery on Start (paid)."
                            ),
                        }
        except (OSError, json.JSONDecodeError):
            pass

    return preview_auto_linkedin_companies_seed(icp)


def campaign_summary(campaign_dir: Path) -> dict[str, Any]:
    campaign_id = campaign_dir.name
    display_name = campaign_id
    campaign_meta: dict[str, Any] = {}
    camp_path = campaign_dir / "campaign.json"
    if camp_path.is_file():
        try:
            campaign_meta = json.loads(camp_path.read_text(encoding="utf-8"))
            display_name = (
                campaign_meta.get("name")
                or campaign_meta.get("display_name")
                or campaign_id
            )
        except (OSError, json.JSONDecodeError):
            pass

    icp: dict[str, Any] = {}
    icp_path = campaign_dir / "icp.json"
    if icp_path.is_file():
        try:
            icp = json.loads(icp_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    plan = load_plan(campaign_dir, campaign_id)
    has_plan_file = plan_path(campaign_dir).is_file()

    titles = icp.get("job_titles") or []
    if isinstance(titles, str):
        titles = [titles]
    locations = icp.get("contact_locations") or []
    if not locations and icp.get("location"):
        locations = [icp.get("location")]

    client_id = (
        campaign_meta.get("client_id")
        or icp.get("client_id")
        or ""
    )
    client_name = (
        campaign_meta.get("client_name")
        or campaign_meta.get("client")
        or client_id
    )
    if not client_id:
        payload_path = campaign_dir / "run_payload.json"
        if payload_path.is_file():
            try:
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
                sender = payload.get("sender") or {}
                client_id = sender.get("client_id") or client_id
                client_name = sender.get("company") or sender.get("name") or client_name or client_id
            except (OSError, json.JSONDecodeError):
                pass

    from agent.utils.icp_readiness import assess_icp_depth

    ctype = plan.get("campaign_type") or campaign_meta.get("campaign_type") or "company_outreach"
    depth = assess_icp_depth(icp if isinstance(icp, dict) else {}, campaign_type=ctype)
    seed_plan = _seed_plan_summary(campaign_dir, icp if isinstance(icp, dict) else {})

    return {
        "campaign_id": campaign_id,
        "display_name": display_name,
        "client_id": client_id or None,
        "client_name": client_name or None,
        "icp_id": campaign_meta.get("icp_id") or None,
        "campaign_type": ctype,
        "has_saved_plan": has_plan_file,
        "plan_updated_at_utc": plan.get("updated_at_utc"),
        "brief": campaign_meta.get("brief") or "",
        "list_build_status": campaign_meta.get("list_build_status") or "",
        "list_run": _list_run_summary(campaign_dir, campaign_id),
        "icp_ready": depth["ready"],
        "icp_missing": depth["missing"],
        "icp_remedy": depth["remedy"],
        "seed_plan": seed_plan,
        "disk_path": str(campaign_dir.resolve()),
        "icp": {
            "job_titles": titles[:12],
            "location": icp.get("location"),
            "contact_locations": locations,
            "industries": list(icp.get("industries") or [])[:12],
            "company_size_min": icp.get("company_size_min") or icp.get("size_min"),
            "company_size_max": icp.get("company_size_max") or icp.get("size_max"),
            "website_analysis_mode": icp.get("website_analysis_mode") or "full",
        },
        "plan": plan,
        "links": {
            "cost_dashboard": f"/dashboard#/campaign/{campaign_id}",
            "builder": (
                f"/builder?client={client_id}"
                if client_id
                else f"/builder?campaign={campaign_id}"
            ),
        },
    }
