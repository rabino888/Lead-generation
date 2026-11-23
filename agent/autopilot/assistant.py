"""Assistant note at each human gate.

The note is deterministic: counts, sample companies, and the same adjustment
ideas the retry loop uses. It does not change a checker and it does not spend.
An optional narrator can add a paragraph; tests leave the narrator empty.
"""
from __future__ import annotations

import os
from collections import Counter
from typing import Any, Callable, Mapping

from agent.autopilot.adjust import diagnose

Narrator = Callable[[dict[str, Any], str], str]

_PREVIEW_KEYS = (
    "company_name",
    "website",
    "industry",
    "location",
    "company_size",
    "decision_maker_title",
    "website_summary",
    "icp_match_reasons",
    "pre_apollo_reason",
    "dedupe_reason",
)
_MAX_ROWS = 25
_MAX_CELL = 240

# Human checkpoints. Automated checkers still run inside each stretch.
HUMAN_GATES = ("plan", "companies", "sequence")


def preview_rows(rows: list[Mapping[str, Any]], *, limit: int = _MAX_ROWS) -> list[dict[str, Any]]:
    """Company fields for a person to scan. Emails and phones are not copied."""
    out: list[dict[str, Any]] = []
    for row in rows[:limit]:
        item: dict[str, Any] = {}
        for key in _PREVIEW_KEYS:
            value = row.get(key)
            if value is None or value == "":
                continue
            text = str(value).replace("\n", " ").strip()
            if len(text) > _MAX_CELL:
                text = text[: _MAX_CELL - 1] + "…"
            item[key] = text
        email = str(row.get("decision_maker_email") or row.get("email") or "").strip()
        linkedin = str(
            row.get("decision_maker_linkedin")
            or row.get("decision_maker_linkedin_url")
            or row.get("linkedin_url")
            or ""
        ).strip()
        if email:
            item["has_email"] = True
        if linkedin:
            item["has_linkedin"] = True
        if item:
            out.append(item)
    return out


def _top_counts(rows: list[Mapping[str, Any]], key: str, n: int = 5) -> list[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        value = str(row.get(key) or "").strip()
        if value:
            counts[value] += 1
    return [f"{name} ({count})" for name, count in counts.most_common(n)]


def _suggestions_from(stage: str, gate: Mapping[str, Any], plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    if gate.get("ok", True) and not gate.get("reasons"):
        return []
    adjustment = diagnose(stage, gate, plan, set())
    if not adjustment:
        return []
    return [
        {
            "action": adjustment.get("action"),
            "why": adjustment.get("why"),
            "set": adjustment.get("set") or {},
            "applies_automatically": False,
        }
    ]


def brief_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    warnings = list(plan.get("warnings") or [])
    seed = (plan.get("seed_source") or {}).get("id")
    findings = [
        f"Seed source: {seed}. {(plan.get('seed_source') or {}).get('why') or ''}".strip(),
        f"Contact provider: {plan.get('contact_provider')}.",
        f"Estimated spend ${((plan.get('cost_estimate') or {}).get('total_usd'))} "
        f"against a cap of ${plan.get('max_usd')}. Sequence cost is included in the estimate "
        "and is spent only if you ask for a sequence later.",
    ]
    findings.extend(warnings)
    ready = not any("No industry" in item or "No buyer country" in item for item in warnings)
    return _note(
        gate="plan",
        headline="Review the plan before any company search.",
        summary="Research is done. Approving this gate runs the seed source, ICP match, and dedupe, then stops so you can check the companies.",
        findings=findings,
        suggestions=[],
        sample=[],
        recommendation=(
            "Approve the plan to discover companies."
            if ready
            else "The site did not name an industry or a buyer country. Add that in a revision, then approve."
        ),
        proceed_label="Approve plan",
    )


def brief_companies(
    plan: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    *,
    gate: Mapping[str, Any] | None = None,
    dropped: int = 0,
) -> dict[str, Any]:
    sample = preview_rows(rows)
    industries = _top_counts(rows, "industry")
    findings = [
        f"{len(rows)} companies kept after ICP match and dedupe.",
        f"{dropped} rows dropped before this list." if dropped else "Nothing extra was dropped at dedupe.",
    ]
    if industries:
        findings.append("Industries in the kept list: " + ", ".join(industries) + ".")
    target = ", ".join((plan.get("icp") or {}).get("industries") or []) or "the ICP industries"
    findings.append(f"The ICP is aiming at {target}.")
    suggestions = _suggestions_from("icp_match", gate or {"ok": True, "reasons": []}, plan)
    if not rows:
        recommendation = "No companies survived. Apply a suggestion or revise the plan, then run discovery again."
    else:
        recommendation = (
            "Approve only if these companies are the kind of buyer you want. "
            "Approval spends on contact lookup and enrichment, then stops before any sequence."
        )
    return _note(
        gate="companies",
        headline="Check the companies before enrichment spend.",
        summary="Seed, ICP match, and dedupe have finished. The next gate is the enriched list.",
        findings=findings,
        suggestions=suggestions,
        sample=sample,
        recommendation=recommendation,
        proceed_label="Approve companies",
    )


def brief_sequence(
    plan: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    *,
    gate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    sample = preview_rows(rows)
    with_email = sum(1 for row in sample if row.get("has_email"))
    with_li = sum(1 for row in sample if row.get("has_linkedin"))
    findings = [
        f"{len(rows)} enriched rows are ready to review.",
        f"In the sample, {with_email} have an email and {with_li} have a LinkedIn URL.",
        "Generating a sequence calls outreach-content-agent with --dry-run. Nothing is sent.",
        "Skipping leaves the lead list and does not call the outreach repo.",
    ]
    if gate and gate.get("summary"):
        findings.insert(0, str(gate.get("summary")))
    suggestions = []
    if gate and not gate.get("ok", True):
        suggestions = _suggestions_from("enrich", gate, plan) or _suggestions_from("contacts", gate, plan)
    passed = gate is None or gate.get("ok", True)
    recommendation = (
        "Generate a sequence only if the rows have evidence worth citing. Otherwise skip it."
        if passed
        else "Enrichment or contactability did not clear its checker. Revise or retry before generating a sequence."
    )
    return _note(
        gate="sequence",
        headline="Enrichment is done. The sequence is optional.",
        summary="This is the last human gate. Choose whether to draft an outreach sequence.",
        findings=findings,
        suggestions=suggestions,
        sample=sample,
        recommendation=recommendation,
        proceed_label="Generate sequence" if passed else "Retry enrichment",
        skip_label="Skip sequence",
    )


def brief_halt(plan: Mapping[str, Any], stage: str, summary: str) -> dict[str, Any]:
    synthetic = {"ok": False, "kind": stage, "summary": summary, "reasons": [summary or stage], "rows": 0}
    suggestions = _suggestions_from(stage, synthetic, plan)
    return _note(
        gate="halted",
        headline=f"Stopped during {stage}.",
        summary=summary or "The checker failed and no safe input change is left.",
        findings=[
            "The existing checker was not turned down.",
            "You can revise the inputs and run this stretch again, or stop here.",
        ],
        suggestions=suggestions,
        sample=[],
        recommendation="Revise with a note, or apply a suggestion, then re-run this stretch.",
        proceed_label="Re-run this stretch",
    )


def _note(
    *,
    gate: str,
    headline: str,
    summary: str,
    findings: list[str],
    suggestions: list[dict[str, Any]],
    sample: list[dict[str, Any]],
    recommendation: str,
    proceed_label: str,
    skip_label: str = "",
) -> dict[str, Any]:
    return {
        "gate": gate,
        "headline": headline,
        "summary": summary,
        "findings": findings,
        "suggestions": suggestions,
        "sample": sample,
        "recommendation": recommendation,
        "proceed_label": proceed_label,
        "skip_label": skip_label,
        "narrative": "",
        "source": "deterministic",
    }


def apply_narrator(note: dict[str, Any], question: str = "", narrator: Narrator | None = None) -> dict[str, Any]:
    """Fill ``narrative``. The default narrator only restates the note."""
    fn = narrator or _default_narrator
    if (os.environ.get("AUTOPILOT_ASSISTANT") or "").strip().lower() == "off":
        note["narrative"] = ""
        note["source"] = "off"
        return note
    note["narrative"] = fn(note, question)
    if narrator is not None:
        note["source"] = "narrator"
    return note


def _default_narrator(note: Mapping[str, Any], question: str) -> str:
    lines = [str(note.get("headline") or ""), str(note.get("recommendation") or "")]
    findings = note.get("findings") or []
    if findings:
        lines.append(str(findings[0]))
    if question.strip():
        lines.append(f"On your question ({question.strip()}): use the findings above; do not relax a checker to force a pass.")
    return " ".join(part for part in lines if part).strip()


def attach_assistant(
    state: dict[str, Any],
    note: dict[str, Any],
    *,
    question: str = "",
    narrator: Narrator | None = None,
) -> dict[str, Any]:
    apply_narrator(note, question, narrator)
    state["assistant"] = note
    return note
