"""Default stage runners. They call the existing funnel with the campaign root patched.

Tests pass their own runners and never reach these.
"""
from __future__ import annotations

import csv
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from agent.autopilot.scaffold import campaign_dir


@contextmanager
def use_campaigns_root(root: Path) -> Iterator[None]:
    import agent.utils.campaign_config as campaign_config

    previous = campaign_config.CAMPAIGNS_ROOT
    campaign_config.CAMPAIGNS_ROOT = Path(root)
    try:
        yield
    finally:
        campaign_config.CAMPAIGNS_ROOT = previous


@contextmanager
def use_env(extra: dict[str, str]) -> Iterator[None]:
    previous: dict[str, str | None] = {}
    for key, value in extra.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _stage_usd(plan: dict[str, Any], stage: str) -> float:
    return float(((plan.get("cost_estimate") or {}).get("by_stage") or {}).get(stage) or 0)


def _invoke_script(module_name: str, argv: list[str]) -> int:
    module = __import__(module_name, fromlist=["main"])
    old = sys.argv
    try:
        sys.argv = [module_name, *argv]
        return int(module.main() or 0)
    finally:
        sys.argv = old


def run_seed(ctx: Any) -> dict[str, Any]:
    plan = ctx.plan
    source = (plan.get("seed_source") or {}).get("id") or "google_search"
    config = (plan.get("seed_source") or {}).get("config") or {}
    kwargs: dict[str, Any] = {}
    if source == "csv_ingest":
        kwargs["csv_path"] = config.get("csv_path") or ""
    elif source == "google_search":
        kwargs["queries"] = config.get("queries")
        kwargs["max_results"] = config.get("max_results") or 40
        kwargs["country_code"] = config.get("country_code") or ""
        kwargs["exclude_domains"] = config.get("exclude_domains") or []
    elif source == "google_maps":
        kwargs["queries"] = config.get("queries")
        kwargs["max_results"] = config.get("max_results") or 40
    elif source == "linkedin_companies":
        kwargs["max_results"] = config.get("max_results") or 40
    with use_campaigns_root(ctx.data_root / "campaigns"):
        from agent.utils.seed_sources import run_source

        path = run_source(ctx.campaign_id, source, **kwargs)
    rows = _read_csv(Path(path))
    return {"ok": True, "rows": rows, "artifact": str(path), "usd": _stage_usd(plan, "seed")}


def run_match(ctx: Any) -> dict[str, Any]:
    with use_campaigns_root(ctx.data_root / "campaigns"):
        code = _invoke_script(
            "scripts.match_campaign_icp",
            ["--campaign", ctx.campaign_id],
        )
        from agent.utils.campaign_config import load_campaign
        from agent.utils.icp_rules import stage_path

        campaign = load_campaign(ctx.campaign_id)
        rows = _read_csv(stage_path(campaign, "icp_matched"))
    return {"ok": code == 0, "rows": rows, "usd": 0.0, "returncode": code}


def run_dedupe(ctx: Any) -> dict[str, Any]:
    with use_campaigns_root(ctx.data_root / "campaigns"):
        from agent.utils.campaign_config import load_campaign
        from agent.utils.icp_rules import load_icp_rules, stage_path
        from agent.utils.pre_apollo_gate import filter_pre_apollo
        from agent.utils.stage_artifacts import dedupe_seed_rows, write_stage_csv
        from agent.utils.website_seed_check import apply_website_seed_gate

        campaign = load_campaign(ctx.campaign_id)
        rules = load_icp_rules(campaign)
        matched = _read_csv(stage_path(campaign, "icp_matched"))
        kept, dropped = dedupe_seed_rows(matched)
        fields = list(matched[0].keys()) if matched else ["company_name", "website"]
        if "dedupe_reason" not in fields:
            fields.append("dedupe_reason")
        pre_kept, pre_rejected = filter_pre_apollo(kept, rules, campaign.campaign_dir)
        pre_kept, pre_rejected = apply_website_seed_gate(pre_kept, pre_rejected)
        write_stage_csv(campaign, "deduped", pre_kept, fields)
        if pre_rejected:
            rej_fields = list(fields)
            if "pre_apollo_reason" not in rej_fields:
                rej_fields.append("pre_apollo_reason")
            write_stage_csv(campaign, "pre_apollo_rejected", pre_rejected, rej_fields)
    return {
        "ok": True,
        "rows": pre_kept,
        "dropped": dropped + pre_rejected,
        "usd": 0.0,
    }


def run_contacts(ctx: Any) -> dict[str, Any]:
    provider = ctx.plan.get("contact_provider") or "apollo"
    if provider == "treg":
        return _run_treg_contacts(ctx)
    with use_campaigns_root(ctx.data_root / "campaigns"):
        code = _invoke_script(
            "scripts.stage_apollo",
            ["--campaign", ctx.campaign_id, "--max-leads", str(ctx.plan.get("target_leads") or 25)],
        )
        from agent.utils.campaign_config import load_campaign
        from agent.utils.icp_rules import stage_path

        rows = _read_csv(stage_path(load_campaign(ctx.campaign_id), "apollo_contactable"))
    return {"ok": code == 0, "rows": rows, "usd": _stage_usd(ctx.plan, "contacts"), "returncode": code}


def _run_treg_contacts(ctx: Any) -> dict[str, Any]:
    """Decision-maker email + LinkedIn via treg, then the same contactability gate."""
    from agent.models import Contact
    from agent.utils.apollo_contact_gate import apollo_contactable

    with use_campaigns_root(ctx.data_root / "campaigns"):
        from agent.utils.campaign_config import load_campaign
        from agent.utils.icp_match import _root_domain
        from agent.utils.icp_rules import stage_path
        from agent.utils.stage_artifacts import write_stage_csv
        from scripts.stage_treg_apollo_compare import _lookup_treg

        campaign = load_campaign(ctx.campaign_id)
        rows = _read_csv(stage_path(campaign, "deduped"))
        usd = 0.0
        kept: list[dict[str, Any]] = []
        for row in rows[: int(ctx.plan.get("target_leads") or 25)]:
            domain = _root_domain(row.get("website") or "")
            found = _lookup_treg(domain, company_name=row.get("company_name") or "", max_cost=0.20)
            try:
                usd += float(found.get("treg_search_usd") or 0) + float(found.get("treg_email_usd") or 0)
            except (TypeError, ValueError):
                pass
            enriched = dict(row)
            enriched["decision_maker_name"] = found.get("treg_name") or ""
            enriched["decision_maker_title"] = found.get("treg_title") or ""
            enriched["decision_maker_email"] = found.get("treg_email") or ""
            enriched["decision_maker_linkedin"] = found.get("treg_linkedin") or ""
            enriched["decision_maker_linkedin_url"] = found.get("treg_linkedin") or ""
            contact = Contact(
                email=enriched["decision_maker_email"] or None,
                linkedin_url=enriched["decision_maker_linkedin"] or None,
                name=enriched["decision_maker_name"] or None,
            )
            ok, reason = apollo_contactable(contact)
            if ok:
                kept.append(enriched)
            else:
                enriched["contact_reject_reason"] = reason
        fields = list(kept[0].keys()) if kept else ["company_name", "website", "decision_maker_email"]
        path = write_stage_csv(campaign, "apollo_contactable", kept, fields)
    return {"ok": True, "rows": kept, "artifact": str(path), "usd": round(usd, 4)}


def run_enrich(ctx: Any) -> dict[str, Any]:
    provider = str(ctx.plan.get("linkedin_enrich_provider") or "apify")
    with use_campaigns_root(ctx.data_root / "campaigns"), use_env({"LINKEDIN_ENRICH_PROVIDER": provider}):
        code = _invoke_script(
            "scripts.stage_enrich",
            ["--campaign", ctx.campaign_id, "--max-leads", str(ctx.plan.get("target_leads") or 25)],
        )
        from agent.utils.campaign_config import load_campaign
        from agent.utils.icp_rules import stage_path

        campaign = load_campaign(ctx.campaign_id)
        enriched = stage_path(campaign, "enriched")
        rows = _read_csv(enriched) if enriched.is_file() else _read_csv(stage_path(campaign, "apollo_contactable"))
    return {"ok": code == 0, "rows": rows, "usd": _stage_usd(ctx.plan, "enrich"), "returncode": code}


def run_outreach(ctx: Any) -> dict[str, Any]:
    from agent.autopilot.outreach_bridge import handoff_csv, run_outreach as bridge
    from agent.utils.campaign_handoff import build_outreach_handoff, write_outreach_handoff

    folder = campaign_dir(ctx.data_root, ctx.campaign_id)
    csv_path = handoff_csv(folder)
    if csv_path is None:
        return {
            "ok": False,
            "rows": [],
            "qa_pass": 0,
            "needs_review": 0,
            "failed": 0,
            "reasons": ["no lead CSV to hand to outreach"],
            "usd": 0.0,
        }
    handoff = build_outreach_handoff(
        campaign_id=ctx.campaign_id,
        run_id=ctx.state.get("run_id") or ctx.campaign_id,
        csv_path=str(csv_path),
        root=ctx.data_root / "campaigns",
    )
    write_outreach_handoff(folder / "outreach_handoff.json", handoff)
    retry = bool(ctx.state.get("_outreach_partial_retry"))
    result = bridge(
        data_root=ctx.data_root,
        plan=ctx.plan,
        csv_path=str(csv_path),
        command_runner=ctx.outreach_runner,
        retry=retry,
    )
    result["rows"] = []
    result.setdefault("usd", _stage_usd(ctx.plan, "outreach") if not result.get("usd") else result["usd"])
    return result


DEFAULT_RUNNERS = {
    "seed": run_seed,
    "icp_match": run_match,
    "dedupe": run_dedupe,
    "contacts": run_contacts,
    "enrich": run_enrich,
    "outreach": run_outreach,
}
