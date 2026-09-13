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

PREVIEW_COLS = [
    "company_name",
    "decision_maker_name",
    "decision_maker_title",
    "decision_maker_email",
    "decision_maker_linkedin",
    "lead_score",
    "run_id",
]


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
    raise FileNotFoundError("No contactable/enriched/scored stage CSV on disk")


def _slim_row(row: dict[str, str]) -> dict[str, str]:
    return {k: (row.get(k) or "") for k in PREVIEW_COLS}


def lead_summary(
    campaign_dir: Path,
    *,
    stage: str = "auto",
    preview: int = 25,
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    stage_name, path = resolve_stage_file(campaign_dir, stage)
    with path.open(encoding="utf-8", newline="") as f:
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
    sample = [_slim_row(r) for r in rows[:preview_n]]

    return {
        "campaign_id": campaign_dir.name,
        "stage_file": stage_name,
        "stage_path": str(path.as_posix()),
        "total_rows": len(rows),
        "preview": sample,
        "columns": PREVIEW_COLS,
        "run_id_filter": run_id,
        "exports_dir": str((campaign_dir / "exports").as_posix()),
        "note": "CSV lives under data/campaigns/{id}/stages/. Export copies to exports/.",
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
