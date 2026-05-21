"""
API key authentication via the LeadGen Clients Google Sheet.
Caches the sheet data for 5 minutes to avoid hammering the Sheets API.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from agent.models import ClientProfile, ICPProfile
from agent.utils.logger import log

# FastAPI security scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Cache: list of ClientProfile + timestamp
_cache: dict[str, object] = {"clients": [], "loaded_at": 0.0}
_CACHE_TTL = 300  # 5 minutes


def _load_clients() -> list[ClientProfile]:
    """Load clients from Google Sheets, with 5-minute cache."""
    now = time.time()
    if now - _cache["loaded_at"] < _CACHE_TTL and _cache["clients"]:
        return _cache["clients"]  # type: ignore

    try:
        # Import here to avoid circular imports at module load time
        from agent.integrations.sheets import read_clients_sheet
        clients = read_clients_sheet()
        _cache["clients"] = clients
        _cache["loaded_at"] = now
        log.info("Loaded %d clients from Google Sheets", len(clients))
        return clients
    except Exception as e:
        log.error("Failed to load clients from Sheets: %s", e)
        # Return cached data if available, even if stale
        if _cache["clients"]:
            log.warning("Using stale client cache due to Sheets error")
            return _cache["clients"]  # type: ignore
        return []


def authenticate(api_key: Optional[str]) -> ClientProfile:
    """
    Validate the API key and return the matching ClientProfile.
    Raises HTTP 401 if invalid or not found.
    """
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    clients = _load_clients()
    for client in clients:
        if client.api_key == api_key and client.active:
            return client

    raise HTTPException(status_code=401, detail="Invalid or inactive API key")


def get_current_client(api_key: str = Security(api_key_header)) -> ClientProfile:
    """FastAPI dependency: authenticate and return ClientProfile."""
    return authenticate(api_key)


def invalidate_cache() -> None:
    """Force a cache refresh on the next request (useful after adding a client)."""
    _cache["loaded_at"] = 0.0
    log.info("Client cache invalidated")
