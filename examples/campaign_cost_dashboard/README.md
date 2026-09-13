# Campaign cost dashboard template

Reports use **schema v4** — one USD column, billing basis codes, no separate “estimated” column.

## Live ledger (preferred)

```powershell
python scripts/build_cost_dashboard.py --no-open
python scripts/serve_cost_dashboard.py
```

Opens `/dashboard` (overview → client → campaign). Create/edit campaigns at `/builder?new=1`.

## Preview template

```powershell
python scripts/open_campaign_cost_dashboard.py --preview-template
```

## Single campaign offline report

```powershell
python scripts/open_campaign_cost_dashboard.py --campaign automata_us_rnd
```

Writes `cost_log.json`, `cost_log.md`, `cost_dashboard.html` under `data/campaigns/{id}/`.
Prefer the SPA campaign view: `/dashboard#/campaign/{id}`.

## Rebuild SPA index

```powershell
python scripts/open_campaign_cost_dashboard.py --ledger
```

Writes `data/cost_dashboard_index.json` (+ optional `data/cost_dashboard.html` offline export).
`--portal` is a deprecated alias of `--ledger`.

## Schema

See [SCHEMA.md](./SCHEMA.md) for every field on campaign cost reports.
