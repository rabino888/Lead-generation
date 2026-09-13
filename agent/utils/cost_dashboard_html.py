"""
Offline / Artifact renderer for the lead-list cost dashboard.

Live UI is the master template at `agent/dashboard/static/dashboard.html`
(plus `portal.css`). This module only builds a self-contained HTML file under
`data/` for file:// open or Artifact publish — same template, data embedded,
CSS inlined so no `/static` server is required.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PAGE_TITLE = "Lead List Ledger"

_STATIC = Path(__file__).resolve().parents[1] / "dashboard" / "static"


def _embed(index: dict[str, Any]) -> str:
    payload = json.dumps(index, default=str, separators=(",", ":"))
    return payload.replace("<", "\\u003c").replace("&", "\\u0026")


def _template() -> str:
    path = _STATIC / "dashboard.html"
    if not path.is_file():
        raise FileNotFoundError(f"Missing portal template: {path}")
    return path.read_text(encoding="utf-8")


def _portal_css() -> str:
    path = _STATIC / "portal.css"
    if not path.is_file():
        raise FileNotFoundError(f"Missing portal CSS: {path}")
    return path.read_text(encoding="utf-8")


def render_document(index: dict[str, Any]) -> str:
    """Standalone HTML: master template + embedded index + inlined CSS."""
    html = _template().replace("__DATA__", _embed(index))
    css = _portal_css()
    # Drop the /static link so file:// open still styles correctly.
    html = html.replace(
        '<link rel="stylesheet" href="/static/portal.css">',
        f"<style>\n{css}\n</style>",
        1,
    )
    return html


def render_body(index: dict[str, Any]) -> str:
    """Page content for hosts that supply their own skeleton (legacy --body)."""
    doc = render_document(index)
    # Strip outer html/head/body wrappers roughly for Artifact-style embeds.
    start = doc.find("<body>")
    end = doc.rfind("</body>")
    if start >= 0 and end > start:
        return doc[start + len("<body>") : end].strip()
    return doc
