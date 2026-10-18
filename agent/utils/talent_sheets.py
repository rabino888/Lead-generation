"""
Publish talent campaign CSVs to a client Google Sheet (Seeds + Contactable).

Writes ``campaign_sheet.json`` next to the campaign folder.
"""
from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from agent.utils.company_name import humanize_company_name
from agent.utils.logger import log
from agent.utils.talent_people import (
    STAGE_T01_RAW_PEOPLE,
    STAGE_T05_APOLLO_CONTACTABLE,
    talent_stage_path,
)

SEED_HEADERS = [
    "person_id",
    "full_name",
    "title",
    "headline",
    "company_name",
    "company_name_humanized",
    "location",
    "linkedin_url",
    "source_url",
    "seed_source_id",
]

CONTACTABLE_HEADERS = [
    "full_name",
    "email",
    "email_status",
    "title",
    "current_position",
    "headline",
    "company_name",
    "company_name_humanized",
    "company_linkedin_url",
    "company_industry",
    "company_size",
    "company_linkedin_description",
    "website",
    "website_summary",
    "website_brief_findings",
    "website_brief_findings_summary",
    "website_services",
    "website_listing_urls",
    "location",
    "outreach_language",
    "linkedin_url",
    "experience_summary",
    "about",
    "education_summary",
    "linkedin_post_summary",
    "linkedin_interests",
    "linkedin_recent_posts",
    "person_id",
    "apollo_person_id",
    "profile_status",
]


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        rows = [{k: (v or "") for k, v in row.items()} for row in csv.DictReader(fh)]
    from agent.utils.outreach_language import infer_outreach_language_from_row

    for row in rows:
        # Always refresh — rules evolve and aliases like "Brand @ Legal" need re-humanizing.
        row["company_name_humanized"] = humanize_company_name(row.get("company_name"))
        if not (row.get("outreach_language") or "").strip():
            row["outreach_language"] = infer_outreach_language_from_row(row)
    return rows


def _rows_as_matrix(rows: list[dict[str, str]], headers: list[str]) -> list[list[str]]:
    return [[(r.get(h) or "") for h in headers] for r in rows]


def _client_label(campaign_dir: Path, campaign_id: str) -> tuple[str, str, str]:
    """Return (client_id, client_name, display_name)."""
    meta: dict[str, Any] = {}
    camp_json = Path(campaign_dir) / "campaign.json"
    if camp_json.is_file():
        try:
            meta = json.loads(camp_json.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    client_id = str(meta.get("client_id") or "local").strip() or "local"
    client_name = str(meta.get("client_name") or client_id).strip() or client_id
    display = (
        str(meta.get("display_name") or meta.get("name") or campaign_id).strip()
        or campaign_id
    )
    return client_id, client_name, display


def publish_talent_sheet(
    campaign_dir: Path,
    campaign_id: str = "",
    *,
    update: bool = True,
) -> dict[str, Any]:
    """
    Create or refresh a Google Sheet with Seeds (T01) + Contactable (T05) tabs.

    Returns metadata dict (also written to ``campaign_sheet.json``).
    """
    from agent.integrations.drive import create_sheet_in_folder, get_or_create_client_folder
    from agent.integrations.sheets import _get_gspread_client

    camp = Path(campaign_dir)
    cid = (campaign_id or camp.name).strip()
    client_id, client_name, display = _client_label(camp, cid)

    seeds = _read_csv_rows(talent_stage_path(camp, STAGE_T01_RAW_PEOPLE))
    contactable = _read_csv_rows(talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE))
    if not seeds and not contactable:
        raise FileNotFoundError(
            f"No T01/T05 CSVs under {camp / 'stages'} — nothing to publish"
        )

    meta_path = camp / "campaign_sheet.json"
    gc = _get_gspread_client()
    now = datetime.now(UTC).isoformat()

    if update and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        file_id = str(meta.get("spreadsheet_id") or "").strip()
        if file_id:
            spreadsheet = gc.open_by_key(file_id)
            _upsert_tab(spreadsheet, "Seeds", SEED_HEADERS, seeds)
            _upsert_tab(spreadsheet, "Contactable", CONTACTABLE_HEADERS, contactable)
            sheet_url = str(meta.get("sheet_url") or f"https://docs.google.com/spreadsheets/d/{file_id}")
            meta.update(
                {
                    "campaign_id": cid,
                    "client_id": client_id,
                    "seed_count": len(seeds),
                    "contactable_count": len(contactable),
                    "updated_at": now,
                    "sheet_url": sheet_url,
                }
            )
            meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
            log.info(
                "Talent sheet updated: %s (%d seeds, %d contactable)",
                sheet_url,
                len(seeds),
                len(contactable),
            )
            _sync_sheet_url_into_list_run(camp, sheet_url)
            return meta

    sheet_title = f"{display} — Talent — {datetime.now(UTC).strftime('%Y-%m-%d')}"
    folder_id, folder_url = get_or_create_client_folder(client_name)
    file_id = create_sheet_in_folder(sheet_title, folder_id)
    spreadsheet = gc.open_by_key(file_id)

    seeds_ws = spreadsheet.sheet1
    seeds_ws.update_title("Seeds")
    seeds_ws.update(
        [SEED_HEADERS] + _rows_as_matrix(seeds, SEED_HEADERS),
        value_input_option="USER_ENTERED",
    )
    try:
        seeds_ws.format("1:1", {"textFormat": {"bold": True}})
    except Exception:
        pass

    _upsert_tab(spreadsheet, "Contactable", CONTACTABLE_HEADERS, contactable)

    sheet_url = f"https://docs.google.com/spreadsheets/d/{file_id}"
    meta = {
        "campaign_id": cid,
        "client_id": client_id,
        "client_name": client_name,
        "spreadsheet_id": file_id,
        "sheet_url": sheet_url,
        "folder_url": folder_url,
        "seed_count": len(seeds),
        "contactable_count": len(contactable),
        "created_at": now,
        "updated_at": now,
        "kind": "talent_search",
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    log.info(
        "Talent sheet created: %s (%d seeds, %d contactable)",
        sheet_url,
        len(seeds),
        len(contactable),
    )
    _sync_sheet_url_into_list_run(camp, sheet_url)
    return meta


def _sync_sheet_url_into_list_run(campaign_dir: Path, sheet_url: str) -> None:
    """So dashboard / list_run consumers see the link without reading campaign_sheet.json."""
    if not sheet_url:
        return
    lr_path = Path(campaign_dir) / "list_run.json"
    data: dict[str, Any] = {}
    if lr_path.is_file():
        try:
            raw = json.loads(lr_path.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, json.JSONDecodeError):
            data = {}
    data["sheet_url"] = sheet_url
    try:
        lr_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _upsert_tab(spreadsheet, title: str, headers: list[str], rows: list[dict[str, str]]) -> None:
    try:
        ws = spreadsheet.worksheet(title)
        ws.clear()
    except Exception:
        ws = spreadsheet.add_worksheet(
            title=title, rows=max(2000, len(rows) + 10), cols=max(12, len(headers))
        )
    ws.update(
        [headers] + _rows_as_matrix(rows, headers),
        value_input_option="USER_ENTERED",
    )
    try:
        ws.format("1:1", {"textFormat": {"bold": True}})
    except Exception:
        pass


def maybe_publish_talent_sheet(
    campaign_dir: Path,
    campaign_id: str = "",
    *,
    min_contactable: int = 1,
) -> Optional[dict[str, Any]]:
    """Publish when T05 has at least ``min_contactable`` rows; return None on skip/error."""
    camp = Path(campaign_dir)
    t05 = talent_stage_path(camp, STAGE_T05_APOLLO_CONTACTABLE)
    n = len(_read_csv_rows(t05))
    if n < min_contactable:
        log.info(
            "Talent sheet skip for %s: contactable=%d < %d",
            campaign_id or camp.name,
            n,
            min_contactable,
        )
        return None
    try:
        return publish_talent_sheet(camp, campaign_id or camp.name, update=True)
    except Exception:
        log.exception(
            "Talent sheet publish failed for %s", campaign_id or camp.name
        )
        return None
