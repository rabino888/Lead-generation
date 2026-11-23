"""
Suggest cheap collectors from a campaign's offer, lead magnet, and ICP.

The model may only pick kinds in SUGGESTION_KINDS. Accepting a row turns an
existing enrichment module on, or appends a website/jobs extract to the plan.
Nothing here calls Apollo or Apify, and nothing writes outreach copy.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agent.utils.enrichment_plan import (
    EXTRACT_COLUMN_RE,
    EXTRACT_ID_RE,
    load_plan,
    save_plan,
)

SUGGESTIONS_FILENAME = "collector_suggestions.json"
SCHEMA_VERSION = 1
MAX_SUGGESTIONS = 6
MAX_LEAVE_OFF = 4
MAX_PAGE_HINTS = 5

SUGGESTION_KINDS = ("module", "website_extract", "jobs_extract")

# Toggleable enrichment_plan.modules keys. icp_match and dedupe stay on.
MODULE_PARENTS = (
    "apollo_email",
    "linkedin_dm_profile",
    "linkedin_dm_posts",
    "website_scrape",
    "linkedin_company_jobs",
    "linkedin_company_profile",
    "facebook_company_posts",
    "instagram_company_posts",
    "meta_ads",
    "apollo_phone",
)

KIND_PARENT = {
    "module": MODULE_PARENTS,
    "website_extract": ("website_scrape",),
    "jobs_extract": ("linkedin_company_jobs",),
}

HIRING_FUNCTION_BUCKETS = (
    "frontend",
    "engineering",
    "digital",
    "claims_or_underwriting",
    "other",
)

# First match wins per title. Frontend and claims are checked before the
# broader engineering and digital rules.
_TITLE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "frontend",
        re.compile(
            r"\b(front[\s-]?end|react|angular|vue(?:\.js)?|javascript|typescript)\b",
            re.I,
        ),
    ),
    (
        "claims_or_underwriting",
        re.compile(r"\b(claims?|underwrit\w*|fnol)\b", re.I),
    ),
    (
        "digital",
        re.compile(
            r"\b(digital|product manager|product owner|\bux\b|user experience|designer)\b",
            re.I,
        ),
    ),
    (
        "engineering",
        re.compile(
            r"\b(engineers?|developers?|software|devops|sre|back[\s-]?end|"
            r"platform|full[\s-]?stack|\bqa\b|sdet|data)\b",
            re.I,
        ),
    ),
)

_SUBJECT_RE = re.compile(
    r"^\s*((re|fw|fwd|subject)\s*:|quick question\b|following up\b)",
    re.I,
)


class CollectorInputError(ValueError):
    """Campaign files cannot be turned into a suggestion prompt."""


def suggestions_path(campaign_dir: Path) -> Path:
    return Path(campaign_dir) / SUGGESTIONS_FILENAME


def load_suggestions(campaign_dir: Path) -> dict[str, Any]:
    path = suggestions_path(campaign_dir)
    if not path.is_file():
        raise CollectorInputError(f"No {SUGGESTIONS_FILENAME} in {campaign_dir}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CollectorInputError(f"{path.name} must be a JSON object")
    return data


def write_suggestions(campaign_dir: Path, doc: dict[str, Any]) -> Path:
    path = suggestions_path(campaign_dir)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").casefold()).strip()


def why_drop_reason(why: str, pitch: str = "") -> Optional[str]:
    """Return a reason to drop this why, or None when it may be stored."""
    text = (why or "").strip()
    if not text:
        return "empty"
    if _SUBJECT_RE.match(text):
        return "email_subject"
    if len(text) <= 90 and not re.search(r"[.!?]", text):
        return "email_subject"
    if pitch and _contains_pitch(text, pitch):
        return "magnet_pitch"
    return None


def _contains_pitch(why: str, pitch: str) -> bool:
    norm_why = _norm_text(why)
    norm_pitch = _norm_text(pitch)
    if norm_pitch and norm_pitch in norm_why:
        return True
    for part in re.split(r"\[[^\]]+\]", pitch):
        fragment = _norm_text(part)
        if len(fragment) >= 24 and fragment in norm_why:
            return True
    return False


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def _string_list(raw: Any, *, limit: int | None = None) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
        if limit is not None and len(out) >= limit:
            break
    return out


def _clean_suggestion(row: Any, *, pitch: str, reset_status: bool) -> Optional[dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    kind = str(row.get("kind") or "").strip()
    if kind not in SUGGESTION_KINDS:
        return None
    parent = str(row.get("parent_module") or "").strip()
    if parent not in KIND_PARENT.get(kind, ()):
        return None
    suggestion_id = str(row.get("id") or "").strip()
    if not EXTRACT_ID_RE.fullmatch(suggestion_id):
        return None
    columns = [
        name
        for name in _string_list(row.get("columns"))
        if EXTRACT_COLUMN_RE.fullmatch(name)
    ]
    already = _as_bool(row.get("already_collected"))
    if kind in ("website_extract", "jobs_extract") and not columns and not already:
        return None
    why = re.sub(r"\s+", " ", str(row.get("why") or "").strip())
    if why_drop_reason(why, pitch):
        return None
    paid_extra = _as_bool(row.get("paid_extra"))
    cost_note = re.sub(r"\s+", " ", str(row.get("cost_note") or "").strip())
    if paid_extra and not cost_note:
        return None
    status = "proposed"
    if not reset_status:
        raw_status = str(row.get("status") or "").strip()
        if raw_status in ("proposed", "accepted", "rejected"):
            status = raw_status
    existing = [
        name
        for name in _string_list(row.get("existing_fields"))
        if EXTRACT_COLUMN_RE.fullmatch(name)
    ]
    return {
        "id": suggestion_id,
        "status": status,
        "kind": kind,
        "parent_module": parent,
        "columns": columns,
        "page_hints": _string_list(row.get("page_hints"), limit=MAX_PAGE_HINTS),
        "already_collected": already,
        "existing_fields": existing,
        "why": why,
        "cost_note": cost_note,
        "paid_extra": paid_extra,
    }


def _clean_leave_off(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    kept: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        module = str(item.get("module") or "").strip()
        if module not in MODULE_PARENTS or module in seen:
            continue
        reason = re.sub(r"\s+", " ", str(item.get("reason") or "").strip())
        if not reason:
            continue
        cost_note = re.sub(r"\s+", " ", str(item.get("cost_note") or "").strip())
        seen.add(module)
        kept.append({"module": module, "reason": reason, "cost_note": cost_note})
        if len(kept) >= MAX_LEAVE_OFF:
            break
    return kept


def validate_suggestion_document(
    doc: dict[str, Any],
    *,
    pitch: str = "",
    reset_status: bool = False,
) -> dict[str, Any]:
    """Drop unknown kinds, parents, and unsafe why text. Cap list lengths."""
    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in doc.get("suggestions") or []:
        cleaned = _clean_suggestion(row, pitch=pitch, reset_status=reset_status)
        if cleaned is None or cleaned["id"] in seen:
            continue
        seen.add(cleaned["id"])
        suggestions.append(cleaned)
        if len(suggestions) >= MAX_SUGGESTIONS:
            break
    return {
        "suggestions": suggestions,
        "leave_off": _clean_leave_off(doc.get("leave_off")),
    }


def merge_generated_suggestions(
    existing: Optional[dict[str, Any]],
    fresh: dict[str, Any],
) -> dict[str, Any]:
    """Keep accepted and rejected rows. Replace rows that are still proposed."""
    locked: list[dict[str, Any]] = []
    locked_ids: set[str] = set()
    for row in (existing or {}).get("suggestions") or []:
        if not isinstance(row, dict):
            continue
        if row.get("status") in ("accepted", "rejected") and row.get("id"):
            locked.append(row)
            locked_ids.add(str(row["id"]))
    proposed: list[dict[str, Any]] = []
    for row in fresh.get("suggestions") or []:
        if row.get("id") in locked_ids:
            continue
        proposed.append(row)
    room = max(0, MAX_SUGGESTIONS - len(locked))
    suggestions = locked + proposed[:room]
    return {
        "suggestions": suggestions,
        "leave_off": list(fresh.get("leave_off") or []),
    }


def stamp_suggestion_document(
    body: dict[str, Any],
    *,
    campaign_id: str,
    client_id: str,
    icp_id: str,
    offer_id: int,
    lead_magnet_id: int,
    magnet_status: str,
    generator: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "client_id": client_id,
        "icp_id": icp_id,
        "offer_id": offer_id,
        "lead_magnet_id": lead_magnet_id,
        "magnet_status": magnet_status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": generator,
        "suggestions": body.get("suggestions") or [],
        "leave_off": body.get("leave_off") or [],
    }


def _ids(raw: list[str] | tuple[str, ...] | str) -> list[str]:
    if isinstance(raw, str):
        parts = raw.split(",")
    else:
        parts = list(raw)
    out: list[str] = []
    for part in parts:
        text = str(part).strip()
        if text and text not in out:
            out.append(text)
    return out


def accept_suggestions(campaign_dir: Path, ids: list[str] | str) -> dict[str, Any]:
    """Mark ids accepted and merge them into enrichment_plan.json."""
    return _apply_statuses(Path(campaign_dir), accept=_ids(ids), reject=[])


def reject_suggestions(campaign_dir: Path, ids: list[str] | str) -> dict[str, Any]:
    """Mark ids rejected. The enrichment plan is not changed."""
    return _apply_statuses(Path(campaign_dir), accept=[], reject=_ids(ids))


def _apply_statuses(
    campaign_dir: Path,
    *,
    accept: list[str],
    reject: list[str],
) -> dict[str, Any]:
    overlap = sorted(set(accept) & set(reject))
    if overlap:
        raise CollectorInputError(
            "id cannot be both accepted and rejected: " + ", ".join(overlap)
        )
    doc = load_suggestions(campaign_dir)
    rows = doc.get("suggestions")
    if not isinstance(rows, list):
        raise CollectorInputError("suggestions must be a list")
    by_id = {
        str(row.get("id")): row
        for row in rows
        if isinstance(row, dict) and row.get("id")
    }
    missing = [item for item in accept + reject if item not in by_id]
    if missing:
        raise CollectorInputError("unknown suggestion id: " + ", ".join(missing))

    for item in reject:
        by_id[item]["status"] = "rejected"

    plan_changed = False
    plan: Optional[dict[str, Any]] = None
    if accept:
        campaign_id = str(doc.get("campaign_id") or campaign_dir.name)
        plan = load_plan(campaign_dir, campaign_id)
        modules = plan.setdefault("modules", {})
        extracts = [row for row in (plan.get("extracts") or []) if isinstance(row, dict)]
        for item in accept:
            row = by_id[item]
            row["status"] = "accepted"
            if _as_bool(row.get("already_collected")):
                continue
            kind = str(row.get("kind") or "")
            parent = str(row.get("parent_module") or "")
            if kind == "module":
                if modules.get(parent) is not True:
                    modules[parent] = True
                    plan_changed = True
                continue
            if kind not in ("website_extract", "jobs_extract"):
                continue
            extract = {
                "id": row.get("id"),
                "kind": kind,
                "parent_module": parent,
                "columns": list(row.get("columns") or []),
                "page_hints": list(row.get("page_hints") or [])[:MAX_PAGE_HINTS],
                "instruction": str(row.get("why") or ""),
            }
            replaced = False
            for index, current in enumerate(extracts):
                if current.get("id") == extract["id"]:
                    if current != extract:
                        extracts[index] = extract
                        plan_changed = True
                    replaced = True
                    break
            if not replaced:
                extracts.append(extract)
                plan_changed = True
            if modules.get(parent) is not True:
                modules[parent] = True
                plan_changed = True
        if plan_changed:
            plan["extracts"] = extracts
            save_plan(campaign_dir, plan)

    write_suggestions(campaign_dir, doc)
    return doc


def _split_titles(text: str) -> list[str]:
    parts = re.split(r"[;\n|]+", text or "")
    return [part.strip() for part in parts if part.strip()]


def collect_job_titles(
    website_open_roles: Any = None,
    company_open_jobs_summary: Any = None,
) -> list[str]:
    """Titles already stored on the lead. Does not fetch job descriptions."""
    titles: list[str] = []
    if isinstance(website_open_roles, str):
        titles.extend(_split_titles(website_open_roles))
    elif isinstance(website_open_roles, list):
        for item in website_open_roles:
            titles.extend(_split_titles(str(item)))
    if company_open_jobs_summary:
        titles.extend(_split_titles(str(company_open_jobs_summary)))
    seen: set[str] = set()
    unique: list[str] = []
    for title in titles:
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(title)
    return unique


def label_hiring_function(titles: Any) -> str:
    """
    Bucket titles into frontend, engineering, digital, claims_or_underwriting, or other.

    One bucket per title. The returned value lists the buckets that occurred,
    in that fixed order, separated by '; '. Empty when there are no titles.
    """
    if isinstance(titles, str):
        items = _split_titles(titles)
    elif isinstance(titles, list):
        items = collect_job_titles(titles, None)
    else:
        items = []
    if not items:
        return ""
    found: set[str] = set()
    for title in items:
        bucket = "other"
        for name, pattern in _TITLE_RULES:
            if pattern.search(title):
                bucket = name
                break
        found.add(bucket)
    return "; ".join(name for name in HIRING_FUNCTION_BUCKETS if name in found)


def apply_jobs_extracts(
    analysis: Any,
    *,
    website_open_roles: Any = None,
    company_open_jobs_summary: Any = None,
    extracts: Optional[list[dict[str, Any]]] = None,
) -> None:
    """Set hiring_function when an accepted jobs extract asks for that column."""
    wanted = False
    for row in extracts or []:
        if not isinstance(row, dict) or row.get("kind") != "jobs_extract":
            continue
        columns = row.get("columns") or []
        if "hiring_function" in columns:
            wanted = True
            break
    if not wanted or analysis is None:
        return
    titles = collect_job_titles(website_open_roles, company_open_jobs_summary)
    current = dict(getattr(analysis, "collector_extracts", None) or {})
    current["hiring_function"] = label_hiring_function(titles)
    analysis.collector_extracts = current


def website_page_hints(extracts: Optional[list[dict[str, Any]]]) -> list[str]:
    """Path hints from accepted website extracts. Empty when there are none."""
    hints: list[str] = []
    for row in extracts or []:
        if not isinstance(row, dict) or row.get("kind") != "website_extract":
            continue
        for hint in row.get("page_hints") or []:
            text = str(hint).strip()
            if text and text not in hints:
                hints.append(text)
    return hints


def _markdown_section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$", text, re.M)
    if not match:
        return ""
    rest = text[match.end() :]
    nxt = re.search(r"^## ", rest, re.M)
    body = rest[: nxt.start()] if nxt else rest
    return body.strip()


def company_brief_excerpt(markdown: str) -> str:
    """What they do, plus the core offer paragraph. Not magnets or proof lines."""
    parts: list[str] = []
    what = _markdown_section(markdown, "What they do")
    offer = _markdown_section(markdown, "Core paid offer")
    if what:
        parts.append("What they do:\n" + what)
    if offer:
        parts.append("Offer:\n" + offer)
    return "\n\n".join(parts)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise CollectorInputError(f"{label} not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CollectorInputError(f"{label} must be a JSON object: {path}")
    return data


def _as_int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CollectorInputError(f"{label} must be an integer") from exc


def load_collector_context(campaign_dir: Path, *, data_root: Optional[Path] = None) -> dict[str, Any]:
    """
    Read the campaign, ICP, offer, magnet, proof pain themes, and brief excerpt.

    Magnet pitch and proof_line are loaded only so callers can keep them out of
    the prompt. They are not copied into the returned prompt fields.
    """
    from agent.utils.client_store import client_dir, find_icp_dir

    campaign_dir = Path(campaign_dir)
    campaign = _read_json(campaign_dir / "campaign.json", "campaign.json")
    if campaign.get("offer_id") in (None, ""):
        raise CollectorInputError("campaign.json needs offer_id")
    if campaign.get("lead_magnet_id") in (None, ""):
        raise CollectorInputError("campaign.json needs lead_magnet_id")
    offer_id = _as_int(campaign.get("offer_id"), "offer_id")
    lead_magnet_id = _as_int(campaign.get("lead_magnet_id"), "lead_magnet_id")
    client_id = str(campaign.get("client_id") or "").strip()
    icp_id = str(campaign.get("icp_id") or "").strip()
    if not client_id or not icp_id:
        raise CollectorInputError("campaign.json needs client_id and icp_id")

    if data_root is None:
        from agent.utils.client_store import data_root as _data_root

        root = _data_root()
        if not root.is_absolute():
            root = Path(__file__).resolve().parents[2] / root
    else:
        root = Path(data_root)

    client = client_dir(root, client_id)
    offer = _read_json(client / "offers" / f"{offer_id}.json", "offer")
    magnet = _read_json(client / "lead_magnets" / f"{lead_magnet_id}.json", "lead magnet")
    magnet_offer = _as_int(magnet.get("offer_id"), "lead magnet offer_id")
    if magnet_offer != offer_id:
        raise CollectorInputError(
            f"lead magnet offer_id {magnet_offer} does not match campaign offer_id {offer_id}"
        )
    proof_path = client / "proofs" / f"{offer_id}.json"
    pain_themes: list[str] = []
    proof_line = ""
    if proof_path.is_file():
        proof = _read_json(proof_path, "proof")
        pain_themes = [str(item) for item in (proof.get("pain_themes") or []) if str(item).strip()]
        proof_line = str(proof.get("proof_line") or "")

    icp_path = campaign_dir / "icp.json"
    if not icp_path.is_file():
        library = find_icp_dir(root, client_id, icp_id)
        if library is None:
            raise CollectorInputError(f"ICP not found for {client_id}/{icp_id}")
        icp_path = library / "icp.json"
    icp = _read_json(icp_path, "ICP")

    brief_path = client / "company_brief.md"
    brief = ""
    if brief_path.is_file():
        brief = company_brief_excerpt(brief_path.read_text(encoding="utf-8"))

    warnings: list[str] = []
    magnet_icp = str(magnet.get("icp_id") or "").strip()
    if magnet_icp and magnet_icp != icp_id:
        warnings.append(
            f"lead magnet icp_id {magnet_icp} differs from campaign icp {icp_id}"
        )
    magnet_status = str(magnet.get("status") or "").strip() or "unknown"
    if magnet_status.lower() == "proposed":
        warnings.append("lead magnet status is proposed")

    plan = load_plan(campaign_dir, str(campaign.get("campaign_id") or campaign_dir.name))
    geo = icp.get("geo") if isinstance(icp.get("geo"), dict) else {}
    icp_summary = {
        "icp_id": icp.get("icp_id") or icp_id,
        "industries": icp.get("industries") or [],
        "primary_location": geo.get("primary_location"),
        "company_size_min": icp.get("company_size_min"),
        "company_size_max": icp.get("company_size_max"),
        "job_titles": icp.get("job_titles") or [],
        "include_keywords": icp.get("include_keywords") or [],
        "website_analysis_mode": icp.get("website_analysis_mode") or "full",
        "contact_locations": icp.get("contact_locations") or [],
    }
    return {
        "campaign_id": str(campaign.get("campaign_id") or campaign_dir.name),
        "client_id": client_id,
        "icp_id": icp_id,
        "offer_id": offer_id,
        "lead_magnet_id": lead_magnet_id,
        "magnet_status": magnet_status,
        "warnings": warnings,
        "pitch": str(magnet.get("pitch") or ""),
        "proof_line": proof_line,
        "brief_excerpt": brief,
        "offer": {
            "id": offer.get("id"),
            "title": offer.get("title") or "",
            "description": offer.get("description") or "",
        },
        "magnet": {
            "id": magnet.get("id"),
            "offer_id": magnet.get("offer_id"),
            "icp_id": magnet.get("icp_id"),
            "status": magnet_status,
            "title": magnet.get("title") or "",
            "description": magnet.get("description") or "",
        },
        "pain_themes": pain_themes,
        "icp_summary": icp_summary,
        "modules": plan.get("modules") or {},
    }


def build_collector_prompt(context: dict[str, Any]) -> str:
    """Prompt for a later model call. Magnet pitch and proof_line are omitted."""
    modules = context.get("modules") or {}
    module_lines = "\n".join(
        f"- {name}: {'on' if modules.get(name) else 'off'}" for name in MODULE_PARENTS
    )
    return f"""Suggest data collectors for this campaign. Return one JSON object only.

You may only use these kinds: {", ".join(SUGGESTION_KINDS)}.
Module parents, and the only parent for kind "module": {", ".join(MODULE_PARENTS)}.
kind "website_extract" must use parent_module "website_scrape".
kind "jobs_extract" must use parent_module "linkedin_company_jobs".
Column names must match {EXTRACT_COLUMN_RE.pattern}.
page_hints: at most {MAX_PAGE_HINTS} short path fragments.
At most {MAX_SUGGESTIONS} suggestions and {MAX_LEAVE_OFF} leave_off rows.
When paid_extra is true, cost_note is required.
why is one sentence that cites the offer or the magnet. It must not be an email subject and must not repeat the magnet pitch.
Do not write outreach copy.

Already collected when the module is on, so mark already_collected true and name existing_fields instead of inventing a new column:
- website_scrape: services_offered, about_summary, website_summary, website_open_roles, website_is_hiring, website_careers_url
- linkedin_company_jobs: company_open_jobs_summary, company_open_jobs_count, company_is_hiring
The website model is told not to extract tech stack unless a website_extract names that column.

Current modules:
{module_lines}

Company and offer:
{context.get("brief_excerpt") or ""}

Offer JSON:
{json.dumps(context.get("offer") or {}, ensure_ascii=False)}

Lead magnet JSON (pitch omitted):
{json.dumps(context.get("magnet") or {}, ensure_ascii=False)}

Proof pain_themes only:
{json.dumps(context.get("pain_themes") or [], ensure_ascii=False)}

ICP summary:
{json.dumps(context.get("icp_summary") or {}, ensure_ascii=False)}

Return JSON:
{{
  "suggestions": [
    {{
      "id": "snake_case",
      "kind": "module | website_extract | jobs_extract",
      "parent_module": "one allowed parent",
      "columns": ["new_column"],
      "page_hints": [],
      "already_collected": false,
      "existing_fields": [],
      "why": "One sentence citing the offer or magnet.",
      "cost_note": "",
      "paid_extra": false
    }}
  ],
  "leave_off": [
    {{"module": "apollo_phone", "reason": "One sentence.", "cost_note": ""}}
  ]
}}
"""
