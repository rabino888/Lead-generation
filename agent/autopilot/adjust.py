"""Decide the next input change after a gate failure.

The checkers themselves are not edited. A change is a different seed source,
a wider inferred size band, another contact provider, another LinkedIn
enrichment provider, tighter excludes, or a constraint handed to outreach.
The same change is not proposed twice.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping
from urllib.parse import urlparse

SEED_LADDER = ("google_search", "linkedin_companies", "google_maps")
CONTACT_LADDER = ("apollo", "treg")

_TITLE_EXPANSION = ("Founder", "CEO", "Owner", "Managing Director", "President")


def _root(website: str) -> str:
    raw = (website or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if raw.startswith("http") else f"https://{raw}")
    return parsed.netloc.lower().removeprefix("www.")


def _dominant(reasons: list[str]) -> str:
    stems = []
    for reason in reasons:
        stem = (reason or "").split(":", 1)[0].strip()
        if stem:
            stems.append(stem)
    if not stems:
        return ""
    return Counter(stems).most_common(1)[0][0]


def _locked(plan: Mapping[str, Any], field: str) -> bool:
    return field in set(plan.get("locked_fields") or [])


def _proposal(
    *,
    action: str,
    stage: str,
    why: str,
    set_fields: dict[str, Any] | None = None,
    generalize: bool = False,
    when: dict[str, Any] | None = None,
    refilter_only: bool = False,
) -> dict[str, Any]:
    return {
        "action": action,
        "stage": stage,
        "why": why,
        "set": set_fields or {},
        "generalize": generalize,
        "when": when or {},
        "refilter_only": refilter_only,
    }


def _next_in(ladder: tuple[str, ...], current: str, tried_values: set[str]) -> str:
    ordered = [item for item in ladder if item not in tried_values]
    if current in ordered:
        ordered.remove(current)
    for item in ladder:
        if item != current and item not in tried_values and item not in ordered:
            ordered.append(item)
    # Prefer anything not yet used, starting after the current choice.
    if current in ladder:
        idx = ladder.index(current)
        rotated = list(ladder[idx + 1 :]) + list(ladder[:idx])
    else:
        rotated = list(ladder)
    for item in rotated:
        if item != current and item not in tried_values:
            return item
    return ""


def diagnose(
    stage: str,
    gate: Mapping[str, Any],
    plan: Mapping[str, Any],
    tried: set[str],
) -> dict[str, Any] | None:
    """Return one adjustment, or None when nothing safe is left to try."""
    kind = str(gate.get("kind") or stage)
    reasons = [str(item) for item in (gate.get("reasons") or [])]
    dominant = _dominant(reasons)

    if stage == "seed":
        return _diagnose_seed(gate, plan, tried, dominant)
    if stage == "icp_match":
        return _diagnose_match(gate, plan, tried, dominant)
    if stage == "dedupe":
        return _diagnose_dedupe(gate, plan, tried, dominant)
    if stage == "contacts":
        return _diagnose_contacts(plan, tried, dominant)
    if stage == "enrich":
        return _diagnose_enrich(gate, plan, tried, kind)
    if stage == "outreach":
        return _diagnose_outreach(gate, tried)
    return None


def _diagnose_seed(
    gate: Mapping[str, Any],
    plan: Mapping[str, Any],
    tried: set[str],
    dominant: str,
) -> dict[str, Any] | None:
    current = str((plan.get("seed_source") or {}).get("id") or "")
    if dominant in {"no_seeds", "too_few_seeds"} or int(gate.get("rows") or 0) == 0:
        nxt = _next_in(SEED_LADDER, current, _tried_values(tried, "seed_source"))
        if not nxt or _locked(plan, "seed_source"):
            return None
        return _proposal(
            action=f"seed_source:{nxt}",
            stage="seed",
            why=f"{current or 'seed source'} returned no usable companies. Try {nxt}.",
            set_fields={"seed_source": nxt},
            generalize=True,
            when={"seed_source": current, "page_kind": plan.get("page_kind") or ""},
        )
    domains = []
    for row in gate.get("weak_rows") or []:
        domain = _root(str(row.get("website") or ""))
        if domain:
            domains.append(domain)
    action = "exclude_weak_domains"
    if action in tried or not domains:
        nxt = _next_in(SEED_LADDER, current, _tried_values(tried, "seed_source"))
        if not nxt or _locked(plan, "seed_source"):
            return None
        return _proposal(
            action=f"seed_source:{nxt}",
            stage="seed",
            why="Weak-seed rules still reject most of this source. Switch source.",
            set_fields={"seed_source": nxt},
            generalize=False,
            when={"seed_source": current},
        )
    return _proposal(
        action=action,
        stage="seed",
        why="Add weak-seed domains to this campaign's exclude list and search again.",
        set_fields={"excluded_domains": domains},
        generalize=False,
    )


def _diagnose_match(
    gate: Mapping[str, Any],
    plan: Mapping[str, Any],
    tried: set[str],
    dominant: str,
) -> dict[str, Any] | None:
    icp = plan.get("icp") or {}
    if dominant in {"company_size_above_max", "company_size_below_min"} and "company_size" not in tried:
        if _locked(plan, "company_size"):
            return _switch_seed(plan, tried, "Matched companies fall outside the locked size band.")
        return _proposal(
            action="company_size",
            stage="icp_match",
            why="Inferred headcount band rejected the matches. Widen it once and run the same ICP gate.",
            set_fields={"company_size": dominant},
            generalize=False,
        )
    if dominant in {"industry_mismatch", "excluded_industry", "excluded_keyword", "service_provider"}:
        domains = _rejected_domains(gate)
        if domains and "exclude_match_domains" not in tried:
            return _proposal(
                action="exclude_match_domains",
                stage="icp_match",
                why="These domains failed the ICP gate. Exclude them and keep the ICP filters.",
                set_fields={"excluded_domains": domains},
                generalize=False,
            )
        return _switch_seed(plan, tried, "The seed source is outside this ICP. Switch source; do not drop ICP filters.")
    if dominant == "weak_seed" and "exclude_match_domains" not in tried:
        domains = _rejected_domains(gate)
        if domains:
            return _proposal(
                action="exclude_match_domains",
                stage="icp_match",
                why="Weak-seed rejects are excluded by domain, and the weak-seed checker stays on.",
                set_fields={"excluded_domains": domains},
                generalize=False,
            )
    return _switch_seed(plan, tried, "ICP match kept nothing. Try the next seed source.")


def _diagnose_dedupe(
    gate: Mapping[str, Any],
    plan: Mapping[str, Any],
    tried: set[str],
    dominant: str,
) -> dict[str, Any] | None:
    if dominant == "unknown_employee_count":
        return _switch_seed(
            plan,
            tried,
            "Pre-Apollo gate dropped companies with no headcount. Try a source that returns size.",
        )
    if dominant in {"recruiter_or_staffing_name", "excluded_name_pattern"}:
        domains = _rejected_domains(gate)
        if domains and "exclude_dedupe_domains" not in tried:
            return _proposal(
                action="exclude_dedupe_domains",
                stage="dedupe",
                why="Pre-Apollo gate rejected these domains. Exclude them and re-seed.",
                set_fields={"excluded_domains": domains, "reseed": True},
                generalize=False,
            )
    return _switch_seed(plan, tried, "Dedupe and the pre-Apollo gate kept nothing.")


def _diagnose_contacts(plan: Mapping[str, Any], tried: set[str], dominant: str) -> dict[str, Any] | None:
    current = str(plan.get("contact_provider") or "apollo")
    used = _tried_values(tried, "contact_provider")
    used.add(current)
    if not _locked(plan, "contact_provider"):
        for candidate in CONTACT_LADDER:
            action = f"contact_provider:{candidate}"
            if candidate in used or action in tried:
                continue
            return _proposal(
                action=action,
                stage="contacts",
                why=(
                    f"{current} did not return a contactable decision-maker "
                    "(personal email and LinkedIn URL). Try {candidate}."
                ).format(candidate=candidate),
                set_fields={"contact_provider": candidate},
                generalize=True,
                when={
                    "contact_provider": current,
                    "primary_geo": ((plan.get("icp") or {}).get("geo") or {}).get("primary_location") or "",
                },
            )
    titles = list((plan.get("icp") or {}).get("job_titles") or [])
    inferred = "job_titles" not in set(plan.get("locked_fields") or [])
    extra = [title for title in _TITLE_EXPANSION if title not in titles]
    if inferred and extra and "expand_titles" not in tried and plan.get("titles_inferred"):
        return _proposal(
            action="expand_titles",
            stage="contacts",
            why="Inferred titles found nobody. Add the standard owner titles and search again.",
            set_fields={"job_titles": extra},
            generalize=False,
        )
    return None


def _diagnose_enrich(
    gate: Mapping[str, Any],
    plan: Mapping[str, Any],
    tried: set[str],
    kind: str,
) -> dict[str, Any] | None:
    if kind == "list_qa":
        domains = []
        for row in gate.get("outsiders") or []:
            domain = _root(str(row.get("website") or ""))
            if domain:
                domains.append(domain)
        if domains and "list_qa_excludes" not in tried:
            return _proposal(
                action="list_qa_excludes",
                stage="enrich",
                why="List QA found companies outside the ICP. Exclude those domains and drop the rows.",
                set_fields={"excluded_domains": domains},
                generalize=False,
                refilter_only=True,
            )
        return None
    provider = str(plan.get("linkedin_enrich_provider") or "apify")
    failed = set(gate.get("failed_modules") or [])
    linkedin_failed = bool(failed & {"linkedin_dm_posts", "linkedin_dm_profile", "linkedin_company_jobs", "linkedin_company_profile"})
    if linkedin_failed and not _locked(plan, "linkedin_enrich_provider"):
        nxt = "treg" if provider != "treg" else "apify"
        action = f"linkedin_enrich_provider:{nxt}"
        if action not in tried and nxt != provider:
            return _proposal(
                action=action,
                stage="enrich",
                why=f"LinkedIn enrichment via {provider} stored nothing. Retry with {nxt}. Modules stay on.",
                set_fields={"linkedin_enrich_provider": nxt},
                generalize=True,
                when={"linkedin_enrich_provider": provider},
            )
    if "enrich_retry" not in tried:
        return _proposal(
            action="enrich_retry",
            stage="enrich",
            why="Enrichment check failed. Retry the same modules once.",
            set_fields={},
            generalize=False,
        )
    return None


def _diagnose_outreach(gate: Mapping[str, Any], tried: set[str]) -> dict[str, Any] | None:
    text = " ".join(str(item) for item in (gate.get("reasons") or [])).lower()
    command_failed = "outreach_command_failed" in text or "command" in text
    if command_failed and "outreach_fresh" not in tried:
        return _proposal(
            action="outreach_fresh",
            stage="outreach",
            why="Outreach command failed. Retry generation once.",
            set_fields={"generate_fresh": True},
            generalize=False,
        )
    if "outreach_constraints" not in tried and int(gate.get("needs_review") or 0) > 0:
        return _proposal(
            action="outreach_constraints",
            stage="outreach",
            why="QA held leads. Add the gate reasons as constraints and regenerate. The gate is unchanged.",
            set_fields={"constraints": list(gate.get("reasons") or [])[:8]},
            generalize=False,
        )
    if "outreach_fresh" not in tried and int(gate.get("failed") or 0) > 0:
        return _proposal(
            action="outreach_fresh",
            stage="outreach",
            why="Generation errors. Retry once with a fresh outreach run.",
            set_fields={"generate_fresh": True},
            generalize=False,
        )
    return None


def _switch_seed(plan: Mapping[str, Any], tried: set[str], why: str) -> dict[str, Any] | None:
    if _locked(plan, "seed_source"):
        return None
    current = str((plan.get("seed_source") or {}).get("id") or "")
    nxt = _next_in(SEED_LADDER, current, _tried_values(tried, "seed_source"))
    if not nxt:
        return None
    return _proposal(
        action=f"seed_source:{nxt}",
        stage="seed",
        why=why + f" Next source: {nxt}.",
        set_fields={"seed_source": nxt, "reseed": True},
        generalize=True,
        when={"seed_source": current, "page_kind": plan.get("page_kind") or ""},
    )


def _tried_values(tried: set[str], prefix: str) -> set[str]:
    values = set()
    for item in tried:
        head, _, tail = item.partition(":")
        if head == prefix or item.startswith(prefix + ":"):
            if tail:
                values.add(tail)
    return values


def _rejected_domains(gate: Mapping[str, Any]) -> list[str]:
    domains: list[str] = []
    for row in list(gate.get("rejected") or []) + list(gate.get("outsiders") or []):
        domain = _root(str(row.get("website") or ""))
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def apply_adjustment(plan: dict[str, Any], adjustment: Mapping[str, Any]) -> dict[str, Any]:
    """Mutate ``plan`` in place. Returns the fields that actually changed."""
    changed: dict[str, Any] = {}
    icp = plan.setdefault("icp", {})
    updates = dict(adjustment.get("set") or {})
    if "seed_source" in updates:
        source = plan.setdefault("seed_source", {})
        source["id"] = updates["seed_source"]
        source["type"] = updates["seed_source"]
        source["why"] = adjustment.get("why") or source.get("why")
        _retarget_seed_config(source, icp)
        changed["seed_source"] = updates["seed_source"]
    if "excluded_domains" in updates:
        existing = list(icp.get("excluded_domains") or [])
        for domain in updates["excluded_domains"]:
            if domain and domain not in existing:
                existing.append(domain)
        icp["excluded_domains"] = existing
        config = (plan.get("seed_source") or {}).setdefault("config", {})
        config_excludes = list(config.get("exclude_domains") or [])
        for domain in existing:
            if domain not in config_excludes:
                config_excludes.append(domain)
        config["exclude_domains"] = config_excludes
        changed["excluded_domains"] = list(updates["excluded_domains"])
    if "company_size" in updates:
        which = updates["company_size"]
        if which == "company_size_above_max" and icp.get("company_size_max") is not None:
            before = int(icp["company_size_max"])
            icp["company_size_max"] = max(before + 10, int(before * 1.5))
            changed["company_size_max"] = icp["company_size_max"]
        elif which == "company_size_below_min" and icp.get("company_size_min") is not None:
            before = int(icp["company_size_min"])
            icp["company_size_min"] = max(1, before // 2)
            changed["company_size_min"] = icp["company_size_min"]
    if "contact_provider" in updates:
        plan["contact_provider"] = updates["contact_provider"]
        changed["contact_provider"] = updates["contact_provider"]
    if "job_titles" in updates:
        titles = list(icp.get("job_titles") or [])
        for title in updates["job_titles"]:
            if title not in titles:
                titles.append(title)
        icp["job_titles"] = titles
        changed["job_titles"] = list(updates["job_titles"])
    if "linkedin_enrich_provider" in updates:
        plan["linkedin_enrich_provider"] = updates["linkedin_enrich_provider"]
        changed["linkedin_enrich_provider"] = updates["linkedin_enrich_provider"]
    if "constraints" in updates:
        plan.setdefault("outreach_constraints", [])
        for item in updates["constraints"]:
            text = str(item).strip()
            if text and text not in plan["outreach_constraints"]:
                plan["outreach_constraints"].append(text)
        changed["constraints"] = list(updates["constraints"])
    if updates.get("generate_fresh"):
        plan["outreach_fresh"] = True
        changed["generate_fresh"] = True
    modules = (plan.get("enrichment") or {}).get("modules")
    if isinstance(modules, dict):
        # Adjustments never turn a module off to make the audit pass.
        changed["modules_disabled"] = [
            key for key, value in modules.items() if value is False and key in set(plan.get("_modules_on_at_plan") or [])
        ]
    return changed


def _retarget_seed_config(source: dict[str, Any], icp: Mapping[str, Any]) -> None:
    """Refresh queries when the source id changes, using the current ICP."""
    from agent.autopilot.planner import seed_config_for

    source["config"] = seed_config_for(source.get("id") or "", icp, source.get("config") or {})
