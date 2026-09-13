"""Shared static assets for cost ledger + list builder UIs."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent / "static"


def mount_portal_static(app: FastAPI) -> None:
    """Serve agent/dashboard/static at /static (portal.css, HTML shells)."""
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="portal_static")
