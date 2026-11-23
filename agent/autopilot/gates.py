"""Stage gates. Each one calls an existing checker and reports the result.

Nothing here changes a threshold inside those checkers. A low yield can still
fail the autopilot stage so the retry loop changes inputs and runs the same
checker again.
"""
from __future__ import annotations

from typing import Any, Mapping

from agent.models import Contact
from agent.utils.apollo_contact_gate import apollo_contactable
from agent.utils.enrichment_audit import audit_enriched_rows
from agent.utils.icp_match import match_company
from agent.utils.icp_rules import IcpRules
from agent.utils.icp_seed_quality import weak_seed_reason
from agent.utils.list_qa import review_rows


def rules_from_icp(icp: Mapping[str, Any]) -> IcpRules:
    geo = icp.get("geo") or {}
    locations = list(icp.get("contact_locations") or [])
    primary = geo.get("primary_location") or (locations[0] if locations else None)
    return IcpRules(
        industries=list(icp.get("industries") or []),
        excluded_industries=list(icp.get("excluded_industries") or []),
        location=primary,
        location_hints=list(geo.get("location_hints") or []),
        company_size_min=icp.get("company_size_min"),
        company_size_max=icp.get("company_size_max"),
        keywords=list(icp.get("include_keywords") or []),
        excluded_keywords=list(icp.get("exclude_keywords") or []),
        excluded_name_patterns=str(icp.get("excluded_name_patterns") or ""),
        excluded_domains=list(icp.get("excluded_domains") or []),
        excluded_website_suffixes=list(icp.get("excluded_website_suffixes") or []),
        exclude_mega_outsourcers=bool(icp.get("exclude_mega_outsourcers")),
        job_titles=list(icp.get("job_titles") or []),
        contact_locations=locations,
        apply_weak_seed_checks=bool(icp.get("apply_weak_seed_checks")),
        weak_seed_rule_pack=str(icp.get("weak_seed_rule_pack") or ""),
        weak_seed_extra_patterns=list(icp.get("weak_seed_extra_patterns") or []),
        require_industry_match=bool(icp.get("require_industry_match") or False),
        require_location_match=bool(icp.get("require_location_match") or False),
        allow_recruiter_companies=icp.get("allow_recruiter_companies"),
    )


def _fail(kind: str, summary: str, **extra: Any) -> dict[str, Any]:
    out = {"ok": False, "kind": kind, "summary": summary}
    out.update(extra)
    return out


def _ok(kind: str, summary: str, **extra: Any) -> dict[str, Any]:
    out = {"ok": True, "kind": kind, "summary": summary}
    out.update(extra)
    return out


def gate_seed(rows: list[Mapping[str, Any]], icp: Mapping[str, Any], limits: Mapping[str, Any]) -> dict[str, Any]:
    min_seeds = int(limits.get("min_seeds") or 1)
    if len(rows) < min_seeds:
        return _fail(
            "seed",
            f"Seed source returned {len(rows)} row(s); need at least {min_seeds}.",
            rows=len(rows),
            reasons=["no_seeds"] if not rows else ["too_few_seeds"],
        )
    if not icp.get("apply_weak_seed_checks"):
        return _ok("seed", f"{len(rows)} seed(s).", rows=len(rows), weak=0, reasons=[])
    weak_rows = []
    for row in rows:
        reason = weak_seed_reason(
            str(row.get("company_name") or ""),
            str(row.get("website") or ""),
            str(row.get("industry") or ""),
            enabled=True,
            rule_pack=str(icp.get("weak_seed_rule_pack") or "generic"),
            extra_patterns=list(icp.get("weak_seed_extra_patterns") or []),
        )
        if reason:
            weak_rows.append({**dict(row), "weak_reason": reason})
    ratio = len(weak_rows) / len(rows)
    max_ratio = float(limits.get("max_weak_seed_ratio") or 0.5)
    if ratio > max_ratio:
        return _fail(
            "seed",
            f"Weak-seed check: {len(weak_rows)}/{len(rows)} seeds failed the campaign's weak-seed rules.",
            rows=len(rows),
            weak=len(weak_rows),
            weak_rows=weak_rows,
            reasons=[row["weak_reason"] for row in weak_rows],
        )
    return _ok(
        "seed",
        f"{len(rows)} seed(s), {len(weak_rows)} weak.",
        rows=len(rows),
        weak=len(weak_rows),
        reasons=[],
    )


def gate_match(
    rows: list[Mapping[str, Any]],
    icp: Mapping[str, Any],
    limits: Mapping[str, Any],
) -> dict[str, Any]:
    """ICP match rate on the rows this stage kept. Uses ``match_company``."""
    rules = rules_from_icp(icp)
    matched: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        ok, reasons = match_company(row, rules)
        if ok:
            matched.append(dict(row))
        else:
            rejected.append({**dict(row), "icp_match_reasons": ";".join(reasons)})
    min_matched = int(limits.get("min_matched") or 1)
    min_ratio = float(limits.get("min_match_ratio") or 0.15)
    checked = len(rows)
    ratio = (len(matched) / checked) if checked else 0.0
    reasons = []
    for row in rejected:
        reasons.extend(
            part for part in str(row.get("icp_match_reasons") or "").split(";") if part
        )
    passed = len(matched) >= min_matched and (ratio >= min_ratio or len(matched) >= min_matched)
    # A zero-match list never passes, even if min_matched was edited down to 0.
    if len(matched) < 1 or not passed:
        return _fail(
            "icp_match",
            f"ICP match kept {len(matched)}/{checked} companies.",
            matched=len(matched),
            checked=checked,
            ratio=ratio,
            rejected=rejected,
            reasons=reasons,
        )
    return _ok(
        "icp_match",
        f"ICP match kept {len(matched)}/{checked} companies.",
        matched=len(matched),
        checked=checked,
        ratio=ratio,
        matched_rows=matched,
        reasons=[],
    )


def gate_dedupe(kept: list[Mapping[str, Any]], dropped: list[Mapping[str, Any]]) -> dict[str, Any]:
    if kept:
        return _ok(
            "dedupe",
            f"Dedupe kept {len(kept)}; dropped {len(dropped)}.",
            kept=len(kept),
            dropped=len(dropped),
            reasons=[],
        )
    reasons = []
    for row in dropped:
        reason = str(row.get("pre_apollo_reason") or row.get("dedupe_reason") or "dropped")
        reasons.append(reason)
    return _fail(
        "dedupe",
        f"Dedupe and pre-Apollo gate kept 0 companies ({len(dropped)} dropped).",
        kept=0,
        dropped=len(dropped),
        rejected=list(dropped),
        reasons=reasons or ["all_dropped"],
    )


def _contact_from_row(row: Mapping[str, Any]) -> Contact:
    email = str(row.get("decision_maker_email") or row.get("email") or "").strip() or None
    linkedin = str(
        row.get("decision_maker_linkedin")
        or row.get("decision_maker_linkedin_url")
        or row.get("linkedin_url")
        or ""
    ).strip() or None
    return Contact(email=email, linkedin_url=linkedin, name=str(row.get("decision_maker_name") or "") or None)


def gate_contacts(rows: list[Mapping[str, Any]], limits: Mapping[str, Any]) -> dict[str, Any]:
    """Apollo/treg contactability. Missing decision-maker LinkedIn still fails."""
    kept = []
    reasons = []
    for row in rows:
        ok, reason = apollo_contactable(_contact_from_row(row))
        if ok:
            kept.append(dict(row))
        elif reason:
            reasons.append(reason)
    min_kept = int(limits.get("min_contactable") or 1)
    if len(kept) < min_kept:
        return _fail(
            "contacts",
            f"Contactability gate kept {len(kept)} of {len(rows)} rows.",
            kept=len(kept),
            checked=len(rows),
            reasons=reasons or ["no_contactable"],
        )
    return _ok(
        "contacts",
        f"Contactability gate kept {len(kept)} of {len(rows)} rows.",
        kept=len(kept),
        checked=len(rows),
        reasons=[],
    )


def gate_enrich(
    rows: list[Mapping[str, Any]],
    plan_modules: Mapping[str, Any],
    icp: Mapping[str, Any],
) -> dict[str, Any]:
    """Enrichment audit, then list QA on the same rows. Both must pass."""
    audit = audit_enriched_rows({"modules": dict(plan_modules or {})}, list(rows))
    if not audit.get("ok"):
        failed = [c["module_id"] for c in audit.get("checks") or [] if c.get("status") == "fail"]
        return _fail(
            "enrichment_audit",
            audit.get("summary") or "Enrichment check failed.",
            failed_modules=failed,
            audit=audit,
            reasons=failed or ["enrichment_audit"],
        )
    rules = rules_from_icp(icp)
    review = review_rows(list(rows), rules)
    if not review.get("ok"):
        outsiders = [row for row in review.get("companies") or [] if not row.get("fit")]
        return _fail(
            "list_qa",
            review.get("summary") or "Company fit failed.",
            review=review,
            outsiders=outsiders,
            reasons=[row.get("reason") or "icp_mismatch" for row in outsiders],
        )
    return _ok("enrich", audit.get("summary") or "Enrichment check passed.", audit=audit, review=review, reasons=[])


def gate_outreach(result: Mapping[str, Any], limits: Mapping[str, Any]) -> dict[str, Any]:
    """Outreach QA. ``needs_review`` and ``failed`` are not treated as a pass."""
    reasons = [str(item) for item in (result.get("reasons") or []) if str(item).strip()]
    qa_pass = int(result.get("qa_pass") or 0)
    needs = int(result.get("needs_review") or 0)
    failed = int(result.get("failed") or 0)
    min_pass = int(limits.get("min_outreach_passed") or 1)
    if result.get("ok") is False and qa_pass == 0 and not needs and not failed:
        return _fail(
            "outreach",
            result.get("summary") or "Outreach command failed.",
            qa_pass=qa_pass,
            needs_review=needs,
            failed=failed,
            reasons=reasons or ["outreach_command_failed"],
        )
    if needs or failed or qa_pass < min_pass:
        return _fail(
            "outreach",
            f"Outreach QA passed {qa_pass}, needs review {needs}, failed {failed}.",
            qa_pass=qa_pass,
            needs_review=needs,
            failed=failed,
            reasons=reasons or ["outreach_qa"],
        )
    return _ok(
        "outreach",
        f"Outreach QA passed {qa_pass}.",
        qa_pass=qa_pass,
        needs_review=0,
        failed=0,
        reasons=[],
    )
