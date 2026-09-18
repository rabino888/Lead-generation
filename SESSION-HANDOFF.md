# Session Handoff — Lead-generation

## Status
Portal cleanup done — four live surfaces only; legacy per-entity HTML and cost_portal removed.

## Surfaces (canonical)
1. **Overview** — `GET /dashboard` (`agent/dashboard/static/dashboard.html`): Total spend → Clients → Where the money went → Generated lists
2. **Per-client** — `#/client/{id}`
3. **Per-campaign** — `#/campaign/{id}`
4. **Create / edit** — `/builder?new=1` or `/builder?campaign=…`

## Build / serve
- `python scripts/build_cost_dashboard.py` → `data/cost_dashboard_index.json` (+ optional offline `data/cost_dashboard.html`)
- `python scripts/serve_cost_dashboard.py` → http://127.0.0.1:8765/dashboard
- FastAPI `main.py` also mounts `/dashboard` and `/builder`

## Removed / do not resurrect
- `data/dashboards/client_*.html`, `campaign_*.html`
- `data/cost_portal.html`, `data/cost_portal_index.json`
- `build_portal_index` / `write_portal_html` / `scope_index`
- Builder “Select a client” home (ledger is the only home)
- Design dumps under `SPEC-dashboard-design/` (deleted)

## Gotchas
- Prefer HTTP serve over `file://` so `/dashboard/api` and `/static/portal.css` work
- Optional `DASHBOARD_SECRET`; rebuild needs `ADMIN_SECRET` for `POST /dashboard/rebuild`
- Per-campaign `data/campaigns/{id}/cost_dashboard.html` still writable for offline reports; SPA is preferred
