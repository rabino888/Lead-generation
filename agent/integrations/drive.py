"""
Google Drive integration — manages per-client folder structure on shared Drive.

Folder structure:
  {DRIVE_ROOT_FOLDER_ID}/
    └── {client_name}/                     ← auto-created on first run
          └── 2026-05-24 — keyword.gsheet  ← new Google Sheet per run

Service account: leads-generator@leads-generator-497313.iam.gserviceaccount.com
Must be a content manager on the shared Drive.
"""
from __future__ import annotations

import json
import os
import logging

from agent.utils.logger import log

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

_service = None


def _get_service():
    global _service
    if _service is not None:
        return _service

    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    file_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "").strip()

    if file_path and os.path.exists(file_path):
        creds = Credentials.from_service_account_file(file_path, scopes=DRIVE_SCOPES)
    elif json_str:
        info = json.loads(json_str)
        creds = Credentials.from_service_account_info(info, scopes=DRIVE_SCOPES)
    else:
        raise RuntimeError(
            "No Google credentials configured. "
            "Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT."
        )

    _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service


# ── Shared-drive-aware wrappers ───────────────────────────────────────────────

def _list(service, **kwargs):
    return service.files().list(
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        **kwargs,
    ).execute()


def _create(service, **kwargs):
    return service.files().create(
        supportsAllDrives=True,
        **kwargs,
    ).execute()


# ── Folder helpers ─────────────────────────────────────────────────────────────

def _find_folder(service, name: str, parent_id: str) -> str | None:
    q = (
        f"mimeType='application/vnd.google-apps.folder' "
        f"and name='{name}' "
        f"and '{parent_id}' in parents "
        f"and trashed=false"
    )
    result = _list(service, q=q, fields="files(id)", spaces="drive")
    files = result.get("files", [])
    return files[0]["id"] if files else None


def _create_folder(service, name: str, parent_id: str) -> str:
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = _create(service, body=metadata, fields="id")
    log.info("Drive: created folder '%s' id=%s", name, folder["id"])
    return folder["id"]


def _get_or_create_folder(service, name: str, parent_id: str) -> str:
    folder_id = _find_folder(service, name, parent_id)
    if folder_id:
        return folder_id
    return _create_folder(service, name, parent_id)


def _share_folder(service, folder_id: str, email: str) -> None:
    """Grant viewer access to a client so they can see their own leads folder."""
    existing = service.permissions().list(
        fileId=folder_id,
        supportsAllDrives=True,
        fields="permissions(emailAddress,role)",
    ).execute().get("permissions", [])

    if any(p.get("emailAddress", "").lower() == email.lower() for p in existing):
        log.info("Drive: %s already has access to folder %s", email, folder_id)
        return

    service.permissions().create(
        fileId=folder_id,
        supportsAllDrives=True,
        body={"type": "user", "role": "reader", "emailAddress": email},
        sendNotificationEmail=False,
        fields="id",
    ).execute()
    log.info("Drive: shared folder %s with %s", folder_id, email)


# ── Public API ─────────────────────────────────────────────────────────────────

def get_or_create_client_folder(client_name: str, client_email: str = "") -> tuple[str, str]:
    """
    Ensure {DRIVE_ROOT_FOLDER_ID}/{client_name}/ exists.
    Optionally shares it with client_email (viewer).
    Returns (folder_id, folder_url).
    """
    service = _get_service()
    root_id = os.environ["DRIVE_ROOT_FOLDER_ID"].strip()

    # Validate root folder is accessible
    try:
        service.files().get(
            fileId=root_id,
            supportsAllDrives=True,
            fields="id,name",
        ).execute()
    except Exception as e:
        raise RuntimeError(f"DRIVE_ROOT_FOLDER_ID is not accessible: {e}") from e

    folder_id = _get_or_create_folder(service, client_name.strip().title(), root_id)

    if client_email:
        try:
            _share_folder(service, folder_id, client_email)
        except Exception as e:
            log.warning("Drive: could not share folder with %s: %s", client_email, e)

    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
    log.info("Drive: client folder ready — %s", folder_url)
    return folder_id, folder_url


def create_sheet_in_folder(sheet_name: str, folder_id: str) -> str:
    """
    Create a new Google Sheets file inside the given Drive folder.
    Returns the spreadsheet file ID.
    """
    service = _get_service()
    metadata = {
        "name": sheet_name[:100],
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": [folder_id],
    }
    file = _create(service, body=metadata, fields="id")
    sheet_id = file["id"]
    log.info("Drive: created spreadsheet '%s' id=%s", sheet_name, sheet_id)
    return sheet_id
