"""
Deterministic person ICP matching for talent_search — seed fields only, no LLM.

Applies title / headline / location / keyword / exclusion rules before profile scrape.
See SPEC-talent-search.md §7 T03 + §8.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Optional

from pydantic import BaseModel, Field

from agent.utils.icp_match import RECRUITER_NAME_RE, match_reason_summary
from agent.utils.location_match import location_matches_icp
from agent.utils.talent_people import (
    STAGE_T02_DEDUPED,
    STAGE_T03_ICP_MATCHED,
    STAGE_T03_ICP_REJECTED,
    T01_FIELDS,
    read_people_csv,
    talent_stage_path,
)


def _as_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if x is not None and str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return []


class TalentPersonRules(BaseModel):
    """Flat rules for seed-field person ICP (no company-size gates)."""

    job_titles: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    contact_locations: list[str] = Field(default_factory=list)
    location: Optional[str] = None
    location_hints: list[str] = Field(default_factory=list)
    require_title_signal: bool = False
    min_keyword_hits: int = 0
    require_location_match: bool = False

    def title_signals(self) -> list[str]:
        """Titles preferred; person_match.skills fill in when titles empty."""
        if self.job_titles:
            return list(self.job_titles)
        return list(self.skills)


def rules_from_icp_dict(data: Mapping[str, Any] | None) -> TalentPersonRules:
    """Build rules from raw icp.json (SPEC §8 + company-style include/exclude aliases)."""
    raw = dict(data or {})
    pm = raw.get("person_match") if isinstance(raw.get("person_match"), dict) else {}
    geo = raw.get("geo") if isinstance(raw.get("geo"), dict) else {}

    job_titles = _as_list(raw.get("job_titles"))
    skills = _as_list(pm.get("skills"))
    keywords = _as_list(raw.get("keywords")) or _as_list(raw.get("include_keywords"))
    excluded = _as_list(raw.get("excluded_keywords")) or _as_list(raw.get("exclude_keywords"))
    contact_locations = _as_list(raw.get("contact_locations"))
    location = (geo.get("primary_location") or raw.get("location") or "").strip() or None
    location_hints = _as_list(geo.get("location_hints"))

    require_title = pm.get("require_title_signal")
    if require_title is None:
        require_title = bool(job_titles or skills)

    try:
        min_kw = int(pm.get("min_keyword_hits") if pm.get("min_keyword_hits") is not None else 0)
    except (TypeError, ValueError):
        min_kw = 0
    if min_kw < 0:
        min_kw = 0

    require_loc = pm.get("require_location_match")
    if require_loc is None:
        require_loc = raw.get("require_location_match")
    if require_loc is None:
        require_loc = bool(contact_locations or location or location_hints)
    else:
        require_loc = bool(require_loc)

    return TalentPersonRules(
        job_titles=job_titles,
        skills=skills,
        keywords=keywords,
        excluded_keywords=excluded,
        contact_locations=contact_locations,
        location=location,
        location_hints=location_hints,
        require_title_signal=bool(require_title),
        min_keyword_hits=min_kw,
        require_location_match=bool(require_loc),
    )


def load_talent_icp_rules(campaign_dir: Path) -> TalentPersonRules:
    path = Path(campaign_dir) / "icp.json"
    if not path.is_file():
        return TalentPersonRules()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return TalentPersonRules()
    return rules_from_icp_dict(data)


def _person_blob(row: Mapping[str, Any]) -> str:
    parts = [
        row.get("title") or "",
        row.get("headline") or "",
        row.get("location") or "",
        row.get("company_name") or "",
        row.get("notes") or "",
    ]
    return " ".join(str(p) for p in parts).lower()


def _title_headline(row: Mapping[str, Any]) -> str:
    return f"{row.get('title') or ''} {row.get('headline') or ''}".strip().lower()


def _has_title_signal(text: str, signals: list[str]) -> bool:
    if not text:
        return False
    for sig in signals:
        s = (sig or "").strip().lower()
        if s and s in text:
            return True
    return False


def _keyword_hit_count(blob: str, keywords: list[str]) -> int:
    hits = 0
    for kw in keywords:
        k = (kw or "").strip().lower()
        if k and k in blob:
            hits += 1
    return hits


def match_person(
    seed_row: Mapping[str, Any],
    rules: TalentPersonRules,
) -> tuple[bool, list[str]]:
    """
    Return (passed, reasons) using seed title/headline/location/company only.

    On fail, reasons explain the first hard rejection (short-circuit).
    On pass, reasons include ``icp_match``.
    """
    blob = _person_blob(seed_row)
    title_text = _title_headline(seed_row)
    company = (seed_row.get("company_name") or "").strip()
    location = (seed_row.get("location") or "").strip()

    for kw in rules.excluded_keywords:
        if kw and kw.lower() in blob:
            return False, [f"excluded_keyword:{kw}"]

    # Cheap recruiter / staffing gate on person-facing seed fields
    scan = f"{title_text} {company}".strip()
    if scan and RECRUITER_NAME_RE.search(scan):
        return False, ["recruiter_or_staffing_signal"]

    if rules.require_title_signal:
        signals = rules.title_signals()
        if not signals:
            return False, ["missing_title_signal"]
        if not _has_title_signal(title_text, signals):
            return False, ["missing_title_signal"]

    if rules.require_location_match:
        if not location_matches_icp(
            location,
            website="",
            primary_location=rules.location,
            location_hints=rules.location_hints,
            contact_locations=rules.contact_locations,
        ):
            return False, ["location_mismatch"]

    if rules.min_keyword_hits > 0 and rules.keywords:
        hits = _keyword_hit_count(blob, rules.keywords)
        if hits < rules.min_keyword_hits:
            return False, [f"missing_keyword_hits:{hits}<{rules.min_keyword_hits}"]

    return True, ["icp_match"]


def apply_talent_icp(
    rows: list[Mapping[str, Any]],
    rules: TalentPersonRules,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split rows into matched / rejected; rejected carry ``reject_reason``."""
    matched: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    for row in rows:
        out = {k: str(row.get(k, "") or "") for k in T01_FIELDS}
        for k, v in row.items():
            if k not in out and k not in ("reject_reason", "icp_match", "icp_match_reasons"):
                out[str(k)] = str(v if v is not None else "")
        passed, reasons = match_person(row, rules)
        summary = match_reason_summary(passed, reasons)
        if passed:
            out["reject_reason"] = ""
            matched.append(out)
        else:
            out["reject_reason"] = summary
            rejected.append(out)
    return matched, rejected


def _write_stage_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        # Still write header so downstream stages see an empty artifact
        fieldnames = list(T01_FIELDS) + ["reject_reason"]
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
        return path
    fieldnames = list(rows[0].keys())
    if "reject_reason" not in fieldnames:
        fieldnames.append("reject_reason")
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def run_talent_icp_stage(
    campaign_dir: Path,
    *,
    input_path: Path | None = None,
    rules: TalentPersonRules | None = None,
) -> tuple[Path, Path, int, int]:
    """
    Read T02 (or ``input_path``), write T03 matched + rejected.

    Returns (matched_path, rejected_path, matched_count, rejected_count).
    """
    camp = Path(campaign_dir)
    stages = camp / "stages"
    stages.mkdir(parents=True, exist_ok=True)

    src = Path(input_path) if input_path else talent_stage_path(camp, STAGE_T02_DEDUPED)
    if not src.is_file():
        raise FileNotFoundError(f"Talent ICP input not found: {src}")

    person_rules = rules if rules is not None else load_talent_icp_rules(camp)
    rows = read_people_csv(src)
    matched, rejected = apply_talent_icp(rows, person_rules)

    matched_path = talent_stage_path(camp, STAGE_T03_ICP_MATCHED)
    rejected_path = talent_stage_path(camp, STAGE_T03_ICP_REJECTED)
    _write_stage_csv(matched_path, matched)
    _write_stage_csv(rejected_path, rejected)
    return matched_path, rejected_path, len(matched), len(rejected)


__all__ = [
    "TalentPersonRules",
    "apply_talent_icp",
    "load_talent_icp_rules",
    "match_person",
    "match_reason_summary",
    "rules_from_icp_dict",
    "run_talent_icp_stage",
]
