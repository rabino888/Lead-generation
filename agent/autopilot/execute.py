"""Run one stretch at a time, then stop for a person.

Stretches:
  companies  — seed, ICP match, dedupe
  enrichment — contacts, enrichment modules
  sequence   — optional outreach draft

A failed checker still retries with a bounded input change. The human gates
are separate: the stretch stops even when the checkers passed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent.autopilot.adjust import apply_adjustment, diagnose
from agent.autopilot.assistant import (
    attach_assistant,
    brief_companies,
    brief_halt,
    brief_sequence,
    preview_rows,
)
from agent.autopilot.gates import (
    gate_contacts,
    gate_dedupe,
    gate_enrich,
    gate_match,
    gate_outreach,
    gate_seed,
    rules_from_icp,
)
from agent.autopilot.learnings import record_avoid, record_success
from agent.autopilot.scaffold import write_campaign_files
from agent.autopilot.store import append_log, save_state
from agent.utils.icp_match import match_company

STAGES = ("seed", "icp_match", "dedupe", "contacts", "enrich", "outreach")

# Stages that run inside one human-to-human stretch, and the phase to enter when they finish.
STRETCHES = {
    "companies": (("seed", "icp_match", "dedupe"), "awaiting_company_approval"),
    "enrichment": (("contacts", "enrich"), "awaiting_sequence_approval"),
    "sequence": (("outreach",), "completed"),
}
PHASE_STRETCH = {
    "approved": "companies",
    "running_companies": "companies",
    "company_approved": "enrichment",
    "running_enrichment": "enrichment",
    "sequence_approved": "sequence",
    "running_sequence": "sequence",
}
_COMPANY_STAGES = set(STRETCHES["companies"][0])
_ENRICH_STAGES = set(STRETCHES["enrichment"][0])

Runner = Callable[[Any], dict[str, Any]]


def stage_estimate(plan: dict[str, Any], stage: str) -> float:
    return float(((plan.get("cost_estimate") or {}).get("by_stage") or {}).get(stage) or 0)


def evaluate_stage(stage: str, result: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    limits = plan.get("gates") or {}
    icp = plan.get("icp") or {}
    rows = list(result.get("rows") or [])
    if stage == "seed":
        gate = gate_seed(rows, icp, limits)
    elif stage == "icp_match":
        gate = gate_match(rows, icp, limits)
    elif stage == "dedupe":
        gate = gate_dedupe(rows, list(result.get("dropped") or []))
    elif stage == "contacts":
        gate = gate_contacts(rows, limits)
    elif stage == "enrich":
        modules = (plan.get("enrichment") or {}).get("modules") or {}
        gate = gate_enrich(rows, modules, icp)
    elif stage == "outreach":
        gate = gate_outreach(result, limits)
    else:
        gate = {"ok": False, "kind": stage, "summary": f"Unknown stage {stage}", "reasons": ["unknown_stage"]}
    if result.get("ok") is False and not rows and stage != "outreach":
        extra = [str(item) for item in (result.get("reasons") or [])]
        if result.get("error"):
            extra.append(str(result["error"]))
        gate = {
            "ok": False,
            "kind": gate.get("kind") or stage,
            "summary": result.get("error") or gate.get("summary") or "Stage command failed.",
            "reasons": extra or gate.get("reasons") or ["stage_error"],
            "rows": 0,
        }
    return gate


def invalidate_from(state: dict[str, Any], stage: str) -> None:
    if stage not in STAGES:
        stage = "seed"
    index = STAGES.index(stage)
    state["completed_stages"] = [
        name for name in (state.get("completed_stages") or []) if STAGES.index(name) < index
    ]


class _Context:
    def __init__(self, data_root: Path, state: dict[str, Any], outreach_runner: Any) -> None:
        self.data_root = data_root
        self.state = state
        self.outreach_runner = outreach_runner

    @property
    def plan(self) -> dict[str, Any]:
        return self.state["plan"]

    @property
    def campaign_id(self) -> str:
        return self.state["campaign_id"]


def stretch_for(state: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    """Return (stretch name, stages, phase when the stretch finishes)."""
    phase = state.get("phase") or ""
    if phase == "halted":
        failed = state.get("failed_stage") or ""
        if failed in _COMPANY_STAGES or not failed:
            name = "companies"
        elif failed in _ENRICH_STAGES:
            name = "enrichment"
        else:
            name = "sequence"
    else:
        name = PHASE_STRETCH.get(phase, "")
    if not name:
        raise RuntimeError(f"Nothing to run from phase={phase}. Approve the current gate first.")
    stages, pause = STRETCHES[name]
    if name == "sequence" and not state.get("generate_sequence"):
        return name, tuple(), "completed"
    return name, stages, pause


def execute(
    data_root: Path,
    state: dict[str, Any],
    runners: dict[str, Runner],
    *,
    outreach_runner: Any = None,
    narrator: Any = None,
) -> dict[str, Any]:
    """Run the stretch the current phase allows, then stop at the next human gate."""
    phase = state.get("phase")
    if phase == "completed":
        return state
    if phase in {"awaiting_plan_approval", "awaiting_approval", "awaiting_company_approval", "awaiting_sequence_approval"}:
        raise RuntimeError(f"Run is waiting at a human gate (phase={phase}).")
    stretch, stages, pause = stretch_for(state)
    running = {
        "companies": "running_companies",
        "enrichment": "running_enrichment",
        "sequence": "running_sequence",
    }[stretch]
    state["phase"] = running
    state["plan"]["approved"] = True
    state.setdefault("completed_stages", [])
    state.setdefault("tried", [])
    state.setdefault("spent_usd", 0.0)
    state.setdefault("previews", {})
    write_campaign_files(data_root, state["plan"])
    save_state(data_root, state)
    ctx = _Context(data_root, state, outreach_runner)

    if not stages:
        state["phase"] = "completed"
        state["sequence_skipped"] = True
        append_log(state, "sequence_skipped", summary="Enrichment approved. Outreach sequence was not requested.")
        attach_assistant(
            state,
            brief_sequence(state["plan"], list((state.get("previews") or {}).get("enrichment") or []), gate={"ok": True, "summary": "Sequence skipped."}),
            narrator=narrator,
        )
        save_state(data_root, state)
        return state

    for stage in stages:
        if stage in state["completed_stages"]:
            continue
        while True:
            outcome = _run_stage(ctx, runners, stage)
            if outcome == "restart":
                save_state(data_root, state)
                break
            if outcome != "ok":
                state["phase"] = "halted"
                state["failed_stage"] = stage
                attach_assistant(
                    state,
                    brief_halt(state["plan"], stage, state.get("halt_reason") or ""),
                    narrator=narrator,
                )
                save_state(data_root, state)
                return state
            state["completed_stages"].append(stage)
            _store_preview(state, stage)
            state.pop("_last_result", None)
            save_state(data_root, state)
            break
        if state.get("phase") == "halted":
            return state
        if outcome == "restart":
            return execute(data_root, state, runners, outreach_runner=outreach_runner, narrator=narrator)

    state["failed_stage"] = ""
    state["halt_reason"] = ""
    state["phase"] = pause
    _attach_pause(state, stretch, narrator)
    if pause == "completed":
        append_log(state, "completed", summary="Sequence draft is local. No email was sent.")
    else:
        append_log(state, "gate_waiting", gate=pause, summary=(state.get("assistant") or {}).get("headline"))
    save_state(data_root, state)
    return state


def _store_preview(state: dict[str, Any], stage: str) -> None:
    result = state.get("_last_result") or {}
    rows = list(result.get("rows") or [])
    previews = state.setdefault("previews", {})
    if stage == "dedupe":
        previews["companies"] = preview_rows(rows)
        previews["companies_dropped"] = len(result.get("dropped") or [])
    elif stage == "enrich":
        previews["enrichment"] = preview_rows(rows)
    elif stage == "icp_match":
        previews["matched"] = preview_rows(rows)


def _attach_pause(state: dict[str, Any], stretch: str, narrator: Any) -> None:
    plan = state.get("plan") or {}
    previews = state.get("previews") or {}
    if stretch == "companies":
        note = brief_companies(
            plan,
            list(previews.get("companies") or []),
            dropped=int(previews.get("companies_dropped") or 0),
        )
    elif stretch == "enrichment":
        note = brief_sequence(plan, list(previews.get("enrichment") or previews.get("companies") or []))
    else:
        note = brief_sequence(
            plan,
            list(previews.get("enrichment") or []),
            gate={"ok": True, "summary": "Sequence draft finished."},
        )
        note["headline"] = "Sequence draft is ready."
        note["recommendation"] = "Read the draft in the outreach campaign folder. Nothing was sent."
        note["proceed_label"] = ""
        note["skip_label"] = ""
    attach_assistant(state, note, narrator=narrator)


def _run_stage(ctx: _Context, runners: dict[str, Runner], stage: str) -> str:
    plan = ctx.plan
    attempts = int(plan.get("max_attempts") or 3)
    last_adjustment: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        estimate = stage_estimate(plan, stage)
        spent = float(ctx.state.get("spent_usd") or 0)
        cap = float(plan.get("max_usd") or 0)
        if cap and spent + estimate > cap:
            append_log(
                ctx.state,
                "cost_halt",
                stage=stage,
                attempt=attempt,
                spent_usd=round(spent, 4),
                estimate_usd=estimate,
                max_usd=cap,
                why=f"Next {stage} estimate ${estimate:.2f} would pass the ${cap:.2f} cap.",
            )
            ctx.state["halt_reason"] = "cost_cap"
            return "halt"
        append_log(ctx.state, "stage_started", stage=stage, attempt=attempt)
        try:
            if stage == "outreach":
                ctx.state["_outreach_partial_retry"] = bool(
                    last_adjustment and last_adjustment.get("action") == "outreach_constraints"
                )
            result = runners[stage](ctx)
        except Exception as exc:  # noqa: BLE001 — recorded, then the retry loop decides
            result = {"ok": False, "rows": [], "error": f"{type(exc).__name__}: {exc}", "reasons": [str(exc)]}
        usd = float(result.get("usd") or 0)
        ctx.state["spent_usd"] = round(spent + usd, 4)
        gate = evaluate_stage(stage, result, plan)
        append_log(
            ctx.state,
            "gate_passed" if gate.get("ok") else "gate_failed",
            stage=stage,
            attempt=attempt,
            summary=gate.get("summary"),
            reasons=(gate.get("reasons") or [])[:8],
            usd=usd,
        )
        if gate.get("ok"):
            if last_adjustment:
                _remember_success(ctx, last_adjustment)
            ctx.state["_last_result"] = result
            return "ok"
        tried = set(ctx.state.get("tried") or [])
        adjustment = diagnose(stage, gate, plan, tried)
        if adjustment and adjustment.get("refilter_only"):
            filtered = _refilter(result.get("rows") or [], plan)
            result = {**result, "rows": filtered}
            apply_adjustment(plan, adjustment)
            tried.add(adjustment["action"])
            ctx.state["tried"] = sorted(tried)
            write_campaign_files(ctx.data_root, plan)
            gate = evaluate_stage(stage, result, plan)
            append_log(
                ctx.state,
                "adjusted",
                stage=stage,
                attempt=attempt,
                action=adjustment["action"],
                why=adjustment.get("why"),
                changed={"excluded_domains": (adjustment.get("set") or {}).get("excluded_domains")},
            )
            if gate.get("ok"):
                _remember_success(ctx, adjustment)
                ctx.state["_last_result"] = result
                return "ok"
            last_adjustment = adjustment
            if attempt == attempts:
                ctx.state["halt_reason"] = gate.get("summary")
                return "halt"
            continue
        if adjustment is None or attempt == attempts:
            append_log(
                ctx.state,
                "halted",
                stage=stage,
                attempt=attempt,
                why=(
                    "No further input change is safe. The gate was not relaxed."
                    if adjustment is None
                    else f"Stopped after {attempts} attempts."
                ),
                summary=gate.get("summary"),
            )
            ctx.state["halt_reason"] = gate.get("summary")
            _remember_failure(ctx, last_adjustment)
            return "halt"
        changed = apply_adjustment(plan, adjustment)
        tried.add(adjustment["action"])
        ctx.state["tried"] = sorted(tried)
        last_adjustment = adjustment
        append_log(
            ctx.state,
            "adjusted",
            stage=stage,
            attempt=attempt,
            action=adjustment["action"],
            why=adjustment.get("why"),
            changed=changed,
        )
        # Prove we did not silence a module to pass the enrichment audit.
        disabled = [
            key
            for key in (plan.get("_modules_on_at_plan") or [])
            if not ((plan.get("enrichment") or {}).get("modules") or {}).get(key, True)
        ]
        if disabled:
            raise RuntimeError(f"Refusing to disable enrichment modules: {disabled}")
        write_campaign_files(ctx.data_root, plan)
        if adjustment.get("set", {}).get("reseed") or (
            "seed_source" in (adjustment.get("set") or {}) and stage != "seed"
        ):
            invalidate_from(ctx.state, "seed")
            return "restart"
    ctx.state["halt_reason"] = f"{stage} stopped after {attempts} attempts"
    _remember_failure(ctx, last_adjustment)
    return "halt"


def _refilter(rows: list[dict[str, Any]], plan: dict[str, Any]) -> list[dict[str, Any]]:
    rules = rules_from_icp(plan.get("icp") or {})
    kept = []
    for row in rows:
        ok, _reasons = match_company(row, rules)
        if ok:
            kept.append(row)
    return kept


def _learning_fields(adjustment: dict[str, Any]) -> dict[str, Any]:
    when = dict(adjustment.get("when") or {})
    fields = {}
    for key in ("seed_source", "contact_provider", "linkedin_enrich_provider"):
        if key in when:
            fields[key] = when[key]
    return fields


def _remember_success(ctx: _Context, adjustment: dict[str, Any]) -> None:
    set_fields = {}
    raw = adjustment.get("set") or {}
    for key in ("seed_source", "contact_provider", "linkedin_enrich_provider"):
        if key in raw and not isinstance(raw[key], bool):
            set_fields[key] = raw[key]
    if not set_fields:
        return
    record_success(
        ctx.data_root,
        client_id=ctx.state.get("client_id") or "",
        stage=str(adjustment.get("stage") or ""),
        when=dict(adjustment.get("when") or {}),
        set_fields=set_fields,
        why=str(adjustment.get("why") or ""),
        generalize=bool(adjustment.get("generalize")),
    )


def _remember_failure(ctx: _Context, adjustment: dict[str, Any] | None) -> None:
    if not adjustment or not adjustment.get("generalize"):
        return
    blocked = _learning_fields(adjustment)
    if not blocked:
        return
    record_avoid(
        ctx.data_root,
        client_id=ctx.state.get("client_id") or "",
        stage=str(adjustment.get("stage") or ""),
        when=dict(adjustment.get("when") or {}),
        set_fields=blocked,
        why=str(adjustment.get("why") or "This choice did not clear the gate."),
        generalize=True,
    )


__all__ = ["STAGES", "evaluate_stage", "execute", "invalidate_from"]
