"""
Paths under data/clients/{client_id}/.

Full delivery runs:  …/runs/
Smoke / gated smoke: …/smoke/runs/   (subfolder of the same client — not a sibling client)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def is_smoke_run(*, keyword: Optional[str] = None, runs_subdir: Optional[str] = None) -> bool:
    sub = (runs_subdir or os.environ.get("LEADGEN_RUNS_SUBDIR") or "").strip().strip("/\\").lower()
    if sub == "smoke":
        return True
    kw = (keyword or "").strip().lower()
    return kw.startswith("smoke")


def client_runs_dir(
    client_id: str,
    *,
    keyword: Optional[str] = None,
    runs_subdir: Optional[str] = None,
    smoke: Optional[bool] = None,
) -> Path:
    """
    Return the runs directory for a client.

    Smoke stays under the same client as a subfolder:
      data/clients/{client_id}/smoke/runs/
    Full batch:
      data/clients/{client_id}/runs/
    """
    cid = (client_id or "unknown").strip() or "unknown"
    base = Path("data") / "clients" / cid
    if smoke is None:
        smoke = is_smoke_run(keyword=keyword, runs_subdir=runs_subdir)
    if smoke:
        return base / "smoke" / "runs"
    return base / "runs"
