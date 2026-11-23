"""Apply and record adjustments that improved a later run.

A learning is a small rule: when the plan matches ``when``, set the fields in
``set``. Client entries override global ones. Locked plan fields are never
overwritten.
"""
from __future__ import annotations

import uuid
from typing import Any

from agent.autopilot.store import append_learning, utc_now


def _matches(when: dict[str, Any], plan: dict[str, Any]) -> bool:
    seed = (plan.get("seed_source") or {}).get("id")
    facts = {
        "seed_source": seed,
        "contact_provider": plan.get("contact_provider"),
        "page_kind": plan.get("page_kind"),
        "primary_geo": ((plan.get("icp") or {}).get("geo") or {}).get("primary_location") or "",
        "linkedin_enrich_provider": plan.get("linkedin_enrich_provider") or "apify",
    }
    for key, expected in (when or {}).items():
        if str(facts.get(key) or "") != str(expected):
            return False
    return True


def apply_learnings(plan: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a copy of ``plan`` with matching learnings applied."""
    out = dict(plan)
    out["seed_source"] = dict(plan.get("seed_source") or {})
    applied: list[dict[str, Any]] = []
    locked = set(plan.get("locked_fields") or [])
    # ``entries`` is oldest first. Later matches override earlier ones.
    for entry in entries:
        if entry.get("polarity") not in {"prefer", "avoid"}:
            continue
        if not _matches(entry.get("when") or {}, out):
            continue
        if entry.get("polarity") == "avoid":
            applied.append(
                {
                    "id": entry.get("id"),
                    "why": entry.get("why") or "prior run said to avoid this choice",
                    "set": {},
                }
            )
            _apply_avoid(out, entry, locked)
            continue
        updates = dict(entry.get("set") or {})
        kept = {}
        for key, value in updates.items():
            if key in locked:
                continue
            if key == "seed_source":
                out["seed_source"]["id"] = value
                out["seed_source"]["type"] = value
            else:
                out[key] = value
            kept[key] = value
        if kept:
            applied.append({"id": entry.get("id"), "why": entry.get("why") or "", "set": kept})
    out["learnings_applied"] = applied
    return out


def _apply_avoid(plan: dict[str, Any], entry: dict[str, Any], locked: set[str]) -> None:
    """Move off a choice a previous run recorded as a dead end."""
    target = entry.get("set") or {}
    if "seed_source" in target and "seed_source" not in locked:
        blocked = str(target["seed_source"])
        current = (plan.get("seed_source") or {}).get("id")
        if current == blocked:
            ladder = list((plan.get("seed_source") or {}).get("ladder") or [])
            for candidate in ladder:
                if candidate != blocked:
                    plan["seed_source"]["id"] = candidate
                    plan["seed_source"]["type"] = candidate
                    break
    if "contact_provider" in target and "contact_provider" not in locked:
        if plan.get("contact_provider") == target["contact_provider"]:
            plan["contact_provider"] = "treg" if target["contact_provider"] == "apollo" else "apollo"


def new_learning(
    *,
    scope: str,
    client_id: str,
    stage: str,
    polarity: str,
    when: dict[str, Any],
    set_fields: dict[str, Any],
    why: str,
) -> dict[str, Any]:
    return {
        "id": "ln_" + uuid.uuid4().hex[:10],
        "scope": scope,
        "client_id": client_id if scope == "client" else "",
        "stage": stage,
        "polarity": polarity,
        "when": when,
        "set": set_fields,
        "why": why,
        "created_at": utc_now(),
    }


def record_success(
    data_root: Any,
    *,
    client_id: str,
    stage: str,
    when: dict[str, Any],
    set_fields: dict[str, Any],
    why: str,
    generalize: bool,
) -> None:
    client_entry = new_learning(
        scope="client",
        client_id=client_id,
        stage=stage,
        polarity="prefer",
        when=when,
        set_fields=set_fields,
        why=why,
    )
    append_learning(data_root, client_entry)
    if generalize:
        append_learning(
            data_root,
            new_learning(
                scope="global",
                client_id="",
                stage=stage,
                polarity="prefer",
                when=when,
                set_fields=set_fields,
                why=why,
            ),
        )


def record_avoid(
    data_root: Any,
    *,
    client_id: str,
    stage: str,
    when: dict[str, Any],
    set_fields: dict[str, Any],
    why: str,
    generalize: bool,
) -> None:
    """Remember a choice that did not clear the gate, so the next plan skips it."""
    entry = new_learning(
        scope="client",
        client_id=client_id,
        stage=stage,
        polarity="avoid",
        when=when,
        set_fields=set_fields,
        why=why,
    )
    append_learning(data_root, entry)
    if generalize:
        append_learning(
            data_root,
            new_learning(
                scope="global",
                client_id="",
                stage=stage,
                polarity="avoid",
                when=when,
                set_fields=set_fields,
                why=why,
            ),
        )
