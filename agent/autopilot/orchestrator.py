"""Start a run from a company URL, approve it, then execute."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agent.autopilot.assistant import attach_assistant, brief_companies, brief_plan
from agent.autopilot.execute import execute as execute_stages
from agent.autopilot.execute import invalidate_from
from agent.autopilot.planner import build_plan, campaign_id_for, client_id_from_url, render_plan_md
from agent.autopilot.research import research_url
from agent.autopilot.runners import DEFAULT_RUNNERS
from agent.autopilot.scaffold import read_disk_overrides, write_campaign_files
from agent.autopilot.store import (
    SCHEMA_VERSION,
    append_log,
    load_applicable_learnings,
    load_state,
    save_state,
    utc_now,
)

Fetch = Callable[[str], tuple[bytes, str]]


def default_data_root() -> Path:
    import os

    root = Path(__file__).resolve().parents[2]
    raw = (os.environ.get("DATA_ROOT") or "").strip()
    if not raw:
        return root / "data"
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return path


def _run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"ap_{stamp}_{uuid.uuid4().hex[:6]}"


def start(
    url: str,
    *,
    data_root: Path | None = None,
    client_id: str = "",
    notes: str = "",
    target_leads: int = 25,
    max_usd: float = 25.0,
    seed_source: str = "",
    contact_provider: str = "",
    csv_path: str = "",
    max_attempts: int = 3,
    fetch: Fetch | None = None,
) -> dict[str, Any]:
    """Research the URL and write a plan. Does not spend on Apollo, Apify, or outreach."""
    root = Path(data_root) if data_root is not None else default_data_root()
    research = research_url(url, fetch=fetch, notes=notes)
    cid = (client_id or "").strip() or client_id_from_url(research["url"])
    campaign_id = campaign_id_for(cid, root)
    learnings = load_applicable_learnings(root, cid)
    plan = build_plan(
        research,
        campaign_id=campaign_id,
        client_id=cid,
        learnings=learnings,
        target_leads=target_leads,
        max_usd=max_usd,
        notes=notes,
        seed_source=seed_source,
        contact_provider=contact_provider,
        csv_path=csv_path,
        max_attempts=max_attempts,
    )
    run_id = _run_id()
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "phase": "awaiting_plan_approval",
        "client_id": cid,
        "campaign_id": campaign_id,
        "company_url": research["url"],
        "company_name": research.get("company_name") or cid,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "revision": 1,
        "research": research,
        "plan": plan,
        "spent_usd": 0.0,
        "completed_stages": [],
        "tried": [],
        "log": [],
        "halt_reason": "",
        "failed_stage": "",
        "generate_sequence": False,
        "sequence_skipped": False,
        "approvals": {},
        "previews": {},
    }
    attach_assistant(state, brief_plan(plan))
    append_log(state, "plan_ready", summary="Plan is waiting for approval. No paid stage has run.")
    write_campaign_files(root, plan)
    save_state(root, state)
    return state


_PLAN_PHASES = {"awaiting_plan_approval", "awaiting_approval"}
_COMPANY_PHASES = {"awaiting_company_approval"}
_SEQUENCE_PHASES = {"awaiting_sequence_approval"}


def _infer_gate(phase: str) -> str:
    if phase in _PLAN_PHASES or phase == "approved":
        return "plan"
    if phase in _COMPANY_PHASES or phase == "company_approved":
        return "companies"
    if phase in _SEQUENCE_PHASES or phase == "sequence_approved":
        return "sequence"
    raise RuntimeError(f"No approval gate is open (phase={phase}).")


def approve(
    run_id: str,
    *,
    data_root: Path | None = None,
    notes: str = "",
    gate: str = "",
    and_run: bool = True,
    generate_sequence: bool = False,
    runners: dict | None = None,
    outreach_runner: Any = None,
    narrator: Any = None,
) -> dict[str, Any]:
    """Approve the open human gate and, by default, run until the next one.

    ``generate_sequence`` matters only at the enrichment gate. Leaving it false
    finishes the run with the lead list and does not call outreach.
    """
    root = Path(data_root) if data_root is not None else default_data_root()
    state = load_state(root, run_id)
    which = (gate or "").strip() or _infer_gate(state.get("phase") or "")
    note = (notes or "").strip()
    approvals = state.setdefault("approvals", {})
    if which == "plan":
        if state.get("phase") not in _PLAN_PHASES | {"approved"}:
            raise RuntimeError(f"Plan approval is not open (phase={state.get('phase')}).")
        state["plan"] = read_disk_overrides(root, state["plan"])
        state["plan"]["approved"] = True
        magnet = ((state["plan"].get("offer") or {}).get("lead_magnet") or {})
        if magnet.get("status") == "proposed":
            magnet["status"] = "approved"
        state["plan"]["markdown"] = render_plan_md(state["plan"])
        state["phase"] = "approved"
    elif which == "companies":
        if state.get("phase") not in _COMPANY_PHASES | {"company_approved"}:
            raise RuntimeError(f"Company approval is not open (phase={state.get('phase')}).")
        state["phase"] = "company_approved"
    elif which == "sequence":
        if state.get("phase") not in _SEQUENCE_PHASES | {"sequence_approved"}:
            raise RuntimeError(f"Sequence approval is not open (phase={state.get('phase')}).")
        state["generate_sequence"] = bool(generate_sequence)
        state["phase"] = "sequence_approved"
    else:
        raise ValueError("gate must be plan, companies, or sequence")
    approvals[which] = {"at": utc_now(), "notes": note, "generate_sequence": bool(generate_sequence)}
    state["approval"] = approvals[which]
    append_log(state, "approved", gate=which, notes=note, generate_sequence=bool(generate_sequence))
    write_campaign_files(root, state["plan"])
    save_state(root, state)
    if and_run:
        return execute(
            run_id,
            data_root=root,
            runners=runners,
            outreach_runner=outreach_runner,
            narrator=narrator,
        )
    return state


def reject(
    run_id: str,
    notes: str,
    *,
    data_root: Path | None = None,
    rerun: bool = False,
    runners: dict | None = None,
    outreach_runner: Any = None,
    narrator: Any = None,
) -> dict[str, Any]:
    """Fold operator notes into the plan. Optionally re-run the stretch that just paused."""
    text = (notes or "").strip()
    if not text:
        raise ValueError("Notes are required so the plan can be revised.")
    root = Path(data_root) if data_root is not None else default_data_root()
    state = load_state(root, run_id)
    phase = state.get("phase") or ""
    if phase in {"running_companies", "running_enrichment", "running_sequence", "completed"}:
        raise RuntimeError(f"Cannot revise while phase={phase}.")
    research = dict(state.get("research") or {})
    prior = (research.get("operator_notes") or "").strip()
    research["operator_notes"] = (prior + "\n" + text).strip()
    research["text"] = (research.get("text") or "") + "\n\nOperator notes:\n" + text
    learnings = load_applicable_learnings(root, state.get("client_id") or "")
    plan = state.get("plan") or {}
    revised = build_plan(
        research,
        campaign_id=state["campaign_id"],
        client_id=state["client_id"],
        learnings=learnings,
        target_leads=int(plan.get("target_leads") or 25),
        max_usd=float(plan.get("max_usd") or 25),
        notes=text,
        seed_source=(plan.get("seed_source") or {}).get("id") if "seed_source" in (plan.get("locked_fields") or []) else "",
        contact_provider=plan.get("contact_provider") if "contact_provider" in (plan.get("locked_fields") or []) else "",
        max_attempts=int(plan.get("max_attempts") or 3),
    )
    revised["approved"] = bool(plan.get("approved"))
    failed = state.get("failed_stage") or ""
    state["research"] = research
    state["plan"] = revised
    state["revision"] = int(state.get("revision") or 1) + 1
    state["halt_reason"] = ""
    state["failed_stage"] = ""
    company_halt = phase == "halted" and failed in {"seed", "icp_match", "dedupe"}
    if phase in _PLAN_PHASES or (phase == "halted" and not failed and not (state.get("completed_stages") or [])):
        state["phase"] = "awaiting_plan_approval"
        attach_assistant(state, brief_plan(revised), question=text, narrator=narrator)
        rerun = False
    elif phase in _COMPANY_PHASES or company_halt:
        invalidate_from(state, "seed")
        state["phase"] = "approved" if rerun else "awaiting_plan_approval"
        if not rerun:
            attach_assistant(state, brief_plan(revised), question=text, narrator=narrator)
    elif phase in _SEQUENCE_PHASES or phase == "halted":
        invalidate_from(state, "contacts")
        state["phase"] = "company_approved" if rerun else "awaiting_company_approval"
        if not rerun:
            previews = state.get("previews") or {}
            attach_assistant(
                state,
                brief_companies(
                    revised,
                    list(previews.get("companies") or []),
                    dropped=int(previews.get("companies_dropped") or 0),
                ),
                question=text,
                narrator=narrator,
            )
    else:
        state["phase"] = "awaiting_plan_approval"
        attach_assistant(state, brief_plan(revised), question=text, narrator=narrator)
        rerun = False
    append_log(state, "revised", notes=text, revision=state["revision"], rerun=bool(rerun))
    write_campaign_files(root, revised)
    save_state(root, state)
    if rerun:
        return execute(
            run_id,
            data_root=root,
            runners=runners,
            outreach_runner=outreach_runner,
            narrator=narrator,
        )
    return load_state(root, run_id)


def execute(
    run_id: str,
    *,
    data_root: Path | None = None,
    runners: dict | None = None,
    outreach_runner: Any = None,
    narrator: Any = None,
) -> dict[str, Any]:
    root = Path(data_root) if data_root is not None else default_data_root()
    state = load_state(root, run_id)
    chosen = dict(DEFAULT_RUNNERS)
    if runners:
        chosen.update(runners)
    return execute_stages(root, state, chosen, outreach_runner=outreach_runner, narrator=narrator)


def status(run_id: str, *, data_root: Path | None = None) -> dict[str, Any]:
    root = Path(data_root) if data_root is not None else default_data_root()
    return load_state(root, run_id)
