"""Lead list summaries + CSV export for the cost portal (stage CSVs under data/)."""
from __future__ import annotations

import csv
import shutil
from pathlib import Path
from typing import Any, Optional

from agent.utils.campaign_config import (
    STAGE_APOLLO_CONTACTABLE,
    STAGE_ENRICHED,
    STAGE_SCORED,
)

STAGE_ALIASES = {
    "scored": STAGE_SCORED,
    "enriched": STAGE_ENRICHED,
    "contactable": STAGE_APOLLO_CONTACTABLE,
    "06_scored": STAGE_SCORED,
    "05_enriched": STAGE_ENRICHED,
    "04_apollo_contactable": STAGE_APOLLO_CONTACTABLE,
}

# Campaign dashboard: compact operator summary
SUMMARY_COLS = [
    "company_name",
    "website",
    "location",
    "decision_maker_name",
    "decision_maker_title",
    "decision_maker_email",
    "decision_maker_linkedin",
    "lead_score",
    "run_id",
]

# Full-results page: Sheet-like width
FULL_COLS = [
    "company_name",
    "website",
    "location",
    "industry",
    "company_size",
    "decision_maker_name",
    "decision_maker_title",
    "decision_maker_email",
    "decision_maker_email_status",
    "decision_maker_linkedin",
    "decision_maker_location",
    "decision_maker_direct_phone",
    "decision_maker_mobile_phone",
    "company_linkedin",
    "company_phone",
    "company_generic_email",
    "website_summary",
    "about_summary",
    "services_offered",
    "hiring_signals",
    "website_is_hiring",
    "qualification_notes",
    "lead_score",
    "score_method",
    "enrichment_status",
    "run_id",
]

# Back-compat alias
PREVIEW_COLS = FULL_COLS


def _columns_for_view(view: str) -> list[str]:
    mode = (view or "summary").strip().lower()
    if mode in ("full", "wide", "all"):
        return list(FULL_COLS)
    return list(SUMMARY_COLS)


def resolve_stage_file(campaign_dir: Path, stage: str = "auto") -> tuple[str, Path]:
    """Pick the richest available stage CSV (scored → enriched → contactable)."""
    stages_dir = campaign_dir / "stages"
    if stage and stage != "auto":
        name = STAGE_ALIASES.get(stage, stage)
        path = stages_dir / name
        if path.is_file():
            return name, path
        raise FileNotFoundError(f"Stage file not found: {name}")

    for key in ("scored", "enriched", "contactable"):
        name = STAGE_ALIASES[key]
        path = stages_dir / name
        if path.is_file() and path.stat().st_size > 0:
            return name, path

    # Gated full-batch deliveries often land only under client runs/ until mirrored
    fallback = _fallback_client_run_csv(campaign_dir)
    if fallback is not None:
        return "client_run", fallback
    raise FileNotFoundError("No contactable/enriched/scored stage CSV on disk")


def _fallback_client_run_csv(campaign_dir: Path) -> Optional[Path]:
    """Use list_run full_run_id / latest client run CSV when stages are empty."""
    import json

    run_id = ""
    lr_path = campaign_dir / "list_run.json"
    if lr_path.is_file():
        try:
            lr = json.loads(lr_path.read_text(encoding="utf-8-sig"))
            run_id = (lr.get("full_run_id") or lr.get("smoke_run_id") or "").strip()
        except (OSError, json.JSONDecodeError):
            run_id = ""
    client_id = ""
    camp_meta = campaign_dir / "campaign.json"
    if camp_meta.is_file():
        try:
            meta = json.loads(camp_meta.read_text(encoding="utf-8-sig"))
            client_id = (meta.get("client_id") or "").strip()
        except (OSError, json.JSONDecodeError):
            client_id = ""
    if not client_id:
        return None
    from agent.utils.client_paths import client_runs_dir

    dirs = [client_runs_dir(client_id, smoke=False), client_runs_dir(client_id, smoke=True)]
    if run_id:
        for d in dirs:
            cand = d / f"{run_id}.csv"
            if cand.is_file() and cand.stat().st_size > 0:
                return cand
    # Latest non-empty CSV for this client that mentions the campaign id
    candidates: list[Path] = []
    for d in dirs:
        if d.is_dir():
            candidates.extend(p for p in d.glob("run_*.csv") if p.stat().st_size > 0)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    camp_id = campaign_dir.name
    for cand in candidates:
        try:
            with cand.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for i, row in enumerate(reader):
                    if (row.get("campaign_id") or "").strip() == camp_id:
                        return cand
                    if i > 5:
                        break
        except OSError:
            continue
    return None


def _slim_row(row: dict[str, str], columns: list[str]) -> dict[str, str]:
    return {k: (row.get(k) or "") for k in columns}


def lead_summary(
    campaign_dir: Path,
    *,
    stage: str = "auto",
    preview: int = 25,
    run_id: Optional[str] = None,
    view: str = "summary",
) -> dict[str, Any]:
    import json as _json

    cols = _columns_for_view(view)
    stage_name, path = resolve_stage_file(campaign_dir, stage)
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    if run_id:
        rid = run_id.strip().lower()
        rows = [
            r
            for r in rows
            if (r.get("run_id") or "").strip().lower() == rid
            or (r.get("batch") or "").strip().lower() == rid
        ]

    preview_n = max(0, min(preview, 200))
    sample = [_slim_row(r, cols) for r in rows[:preview_n]]

    sheet_url = ""
    lr_path = campaign_dir / "list_run.json"
    run_id_guess = ""
    if lr_path.is_file():
        try:
            lr = _json.loads(lr_path.read_text(encoding="utf-8-sig"))
            sheet_url = (lr.get("sheet_url") or "").strip()
            if not run_id:
                run_id_guess = (lr.get("full_run_id") or lr.get("smoke_run_id") or "").strip()
            else:
                run_id_guess = run_id.strip()
        except (OSError, _json.JSONDecodeError):
            run_id_guess = (rows[0].get("run_id") or "").strip() if rows else ""
    else:
        run_id_guess = (rows[0].get("run_id") or "").strip() if rows else ""

    if not sheet_url and run_id_guess:
        try:
            meta: dict[str, Any] = {}
            camp_meta = campaign_dir / "campaign.json"
            if camp_meta.is_file():
                meta = _json.loads(camp_meta.read_text(encoding="utf-8-sig"))
            cid = (meta.get("client_id") or "").strip()
            if cid:
                from agent.utils.client_paths import client_runs_dir

                summary = client_runs_dir(cid, smoke=False) / f"{run_id_guess}_summary.json"
                if summary.is_file():
                    sheet_url = (
                        _json.loads(summary.read_text(encoding="utf-8-sig")).get("sheet_url") or ""
                    ).strip()
        except Exception:
            sheet_url = sheet_url or ""

    view_mode = "full" if cols is FULL_COLS or cols == FULL_COLS else "summary"
    # normalize
    view_mode = "full" if (view or "").strip().lower() in ("full", "wide", "all") else "summary"

    return {
        "campaign_id": campaign_dir.name,
        "stage_file": stage_name if stage_name != "client_run" else path.name,
        "stage_path": str(path.as_posix()),
        "total_rows": len(rows),
        "preview": sample,
        "columns": cols,
        "view": view_mode,
        "run_id_filter": run_id,
        "sheet_url": sheet_url or None,
        "exports_dir": str((campaign_dir / "exports").as_posix()),
        "note": (
            "Full column set for operator review. Export CSV for every field; open Google Sheet when linked."
            if view_mode == "full"
            else "Summary columns on the campaign page. Open Full results for the wide field set."
        ),
    }


def export_leads_csv(
    campaign_dir: Path,
    *,
    stage: str = "auto",
    run_id: Optional[str] = None,
) -> Path:
    """Copy (or filter) stage CSV into data/campaigns/{id}/exports/ and return path."""
    stage_name, src = resolve_stage_file(campaign_dir, stage)
    exports = campaign_dir / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    stem = stage_name.replace(".csv", "")
    if run_id:
        safe = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in run_id)[:80]
        dest = exports / f"{stem}_{safe}.csv"
        with src.open(encoding="utf-8", newline="") as fin, dest.open(
            "w", encoding="utf-8", newline=""
        ) as fout:
            reader = csv.DictReader(fin)
            fieldnames = reader.fieldnames or []
            writer = csv.DictWriter(fout, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            rid = run_id.strip().lower()
            for row in reader:
                if (row.get("run_id") or "").strip().lower() == rid or (
                    row.get("batch") or ""
                ).strip().lower() == rid:
                    writer.writerow(row)
    else:
        dest = exports / stage_name
        shutil.copy2(src, dest)
    return dest
