"""
Serve the cost dashboard and list-building control UI over HTTP.

Usage:
  python scripts/serve_cost_dashboard.py
  python scripts/serve_cost_dashboard.py --port 8765 --rebuild

Then open:
  http://127.0.0.1:8765/dashboard   (initial portal)
  http://127.0.0.1:8765/builder
  http://127.0.0.1:8765/dashboard#/campaign/automata_us_rnd
  http://127.0.0.1:8765/dashboard#/client/automata
"""
from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve cost dashboard + list builder over HTTP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("DASHBOARD_PORT", "8765")))
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild dashboard HTML before serving",
    )
    parser.add_argument(
        "--open",
        choices=("builder", "dashboard", "none"),
        default="dashboard",
        help="Which page to open in the browser (default: dashboard)",
    )
    parser.add_argument("--no-open", action="store_true", help="Deprecated: use --open none")
    args = parser.parse_args()

    if args.rebuild:
        from agent.dashboard.routes import rebuild_dashboard_files

        rebuild_dashboard_files(ROOT / "data")

    import uvicorn
    from fastapi import FastAPI

    from agent.dashboard.builder_routes import router as builder_router
    from agent.dashboard.routes import router as dashboard_router
    from agent.dashboard.static_mount import mount_portal_static

    app = FastAPI(title="Lead-gen cost dashboard + list builder")
    app.include_router(dashboard_router)
    app.include_router(builder_router)
    mount_portal_static(app)

    base = f"http://{args.host}:{args.port}"
    print(f"Cost ledger (home):    {base}/dashboard")
    print(f"Campaign builder:      {base}/builder?client=… or ?new=1")
    print("  /builder/api/campaigns              list campaigns")
    print("  /builder/api/campaigns/{id}         plan + ICP summary")
    print("  /builder/api/campaigns/{id}/estimate")
    print("  /builder/api/campaigns/{id}/plan    PUT save enrichment_plan.json")
    print("  /builder/api/campaigns/{id}/icp     GET/PUT icp.json (local, no LLM)")
    print("  /builder/api/campaigns/{id}/seed_sources  GET/PUT seed_sources.json")
    print("  /dashboard/api                      cost index JSON")
    open_target = "none" if args.no_open else args.open
    if open_target == "builder":
        webbrowser.open(f"{base}/builder?new=1")
    elif open_target == "dashboard":
        webbrowser.open(f"{base}/dashboard")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
