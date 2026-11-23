# Lead Generation Agent

B2B **lead intelligence pipeline** for operators: turn a client brief into
contactable decision-maker lists, with clear cost tracking per list.

**What it does:** curated company seeds → deterministic ICP match → Apollo email +
LinkedIn reveal → website/LinkedIn enrichment → keyword scoring → Sheets/CSV delivery.

**What it is not:** outreach copy, email sequences, or CRM sync. It gathers pitch
intelligence (roles, hiring signals, site content, LinkedIn activity).

All client/geo/industry specifics live in **local** `data/` (gitignored) — never in
shared code defaults.

## Concepts

| Concept | Meaning |
|---------|---------|
| **Client** | The company you work for (`tbomedia`, `automata`, …) |
| **ICP** | Reusable targeting rules (titles, geo, size, industries). One ICP can power many campaigns |
| **Campaign / generated list** | One funnel run: seeds → stages → delivered leads + cost |

Canonical disk layout (see [DATA-LAYOUT.md](DATA-LAYOUT.md)):

```
data/clients/{client_id}/
  icps/{icp_id}/          # library ICP
  campaigns/{campaign_id}/  # list run (campaign.json → icp_id)
```

`data/campaigns/{id}/` remains a junction for older scripts.

## Operator surfaces

| URL | Purpose |
|-----|---------|
| `/dashboard` | Cost ledger — clients, executed lists, spend |
| `/dashboard#/client/{id}` | Client page — generated lists + **Existing ICPs** (from `icps/`) |
| `/builder?new=1` | Create client / ICP / campaign |
| `/builder?campaign={id}` | Edit ICP, seeds, enrichment modules |

Serve both locally:

```powershell
python scripts/serve_cost_dashboard.py --rebuild
```

Open **http://127.0.0.1:8765/dashboard**.

## Quick start

### 1. Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt
copy .env.example .env   # fill API keys — never commit .env
```

### 2. Create a campaign (portal)

1. Open `/builder?new=1` (or **Build new campaign** on the ledger).
2. Choose **existing or new client**.
3. Choose **new ICP** or **use existing ICP** (library under that client).
4. Name the campaign/list (e.g. `Headhunting US 1-50`).
5. Optional brief — local heuristics prefill ICP fields (no paid LLM for ICP).
6. Draft full Automata-depth `icp.json` in Cursor / Claude Code against the campaign
   folder (skill: `icp-draft`, SOP: [SOP-ICP-PLANNING.md](SOP-ICP-PLANNING.md)).
7. **Reload ICP from disk** → configure enrichment modules → save plan.

Gold shape: `automata_us_rnd` ICP under `data/clients/automata/`.

### 3. Run the deterministic funnel

```powershell
python scripts/run_seed_source.py --campaign {campaign_id}
python scripts/match_campaign_icp.py --campaign {campaign_id}
python scripts/stage_dedupe.py --campaign {campaign_id}
python scripts/stage_apollo.py --campaign {campaign_id}
python scripts/stage_enrich.py --campaign {campaign_id}
python scripts/stage_score.py --campaign {campaign_id}
python scripts/stage_segment.py --campaign {campaign_id}
python scripts/stage_qa_flags.py --campaign {campaign_id}
```

Or orchestrate with `scripts/run_deterministic_campaign.py` / `POST /run` (curated seeds).

### Autopilot (URL → plan → companies → optional sequence)

Three human gates. The outreach sequence is off unless you ask for it at the last gate. Nothing is sent.

```powershell
python scripts/autopilot.py plan --url https://example.com
python scripts/autopilot.py approve --run ap_... --gate plan
python scripts/autopilot.py approve --run ap_... --gate companies
python scripts/autopilot.py approve --run ap_... --gate sequence
python scripts/autopilot.py approve --run ap_... --gate sequence --generate-sequence
```

The same pauses are on `/autopilot` (also linked from the ledger and the builder).

| Gate | What you review | What approval runs next |
|------|-----------------|-------------------------|
| Plan | Seed source, ICP, enrichment, cost cap | Seed → ICP match → dedupe |
| Companies | The kept companies | Contacts → enrichment |
| Sequence | The enriched list | Optional sequence draft, or stop |

Each gate has an assistant note: what it sees, and a change it would try. Notes do not relax the checkers. Inside a stretch, a failed checker can still adjust inputs and retry (bounded, logged, under the spend cap). Learnings that helped are stored under `data/autopilot/learnings/` and `data/clients/{id}/autopilot_learnings.json`.

Set `OUTREACH_REPO` to a checkout of outreach-content-agent before `--generate-sequence`. The draft uses that repo's CLI (`sequence`, `kit`, `generate --dry-run` or `render`/`verify`). `deliver` is not called.

`python scripts/autopilot.py revise --run ap_... --notes "..."` rebuilds the plan from your notes. Add `--rerun` to repeat the stretch that just paused.

**Seed sources** (`--source`): `csv_ingest` (free), `linkedin_companies` (company search),
`google_search` (web SERP queries via Apify `apify/google-search-scraper`, one seed per
registrable domain, ~$0.14 per 150 seeds), `google_maps` (local businesses). Queries,
country and campaign-specific `exclude_domains` live in the campaign's `seed_sources.json`.

```powershell
# Add 150 Google seeds after an existing curated list
python scripts/run_seed_source.py --campaign {campaign_id} --source google_search --append
# Re-parse the saved SERP after editing exclude_domains (no SERP spend)
python scripts/run_seed_source.py --campaign {campaign_id} --source google_search --append --reuse-raw
```

**Audit sheet:** `python scripts/publish_audit_sheet.py --campaign {campaign_id}` publishes every
stage CSV (kept + rejected, with reject reasons) as one tab each in a Google Sheet in the client's
Drive folder; re-running refreshes the same file (`audit_sheet.json`).
`stage_apollo.py` reveals phones only when `enrichment_plan.json` has `modules.apollo_phone: true`.

After a run:

```powershell
python scripts/build_cost_dashboard.py
python scripts/serve_cost_dashboard.py
```

**Generated lists** on the ledger only show campaigns that have been executed
(stage CSVs and/or cost data). New ICPs appear under the client’s **Existing ICPs**.

### 4. Tests

```powershell
python -m pytest tests/ -q
```

### 5. Main API (optional)

```powershell
uvicorn main:app --reload
```

Same `/dashboard` and `/builder` routes are mounted. Production auth on `/run` is
not enabled yet — local or behind your own gateway only.

## Contactability

A lead is **contactable** only when Apollo returns:

1. Revealed **personal email**
2. Decision-maker **LinkedIn URL** (`linkedin_url`)

Missing LinkedIn → reject (unless `APOLLO_ALLOW_MISSING_DM_LINKEDIN=1` for legacy).

Company discovery is web/CSV seeded — **do not** use Apollo `mixed_companies/search`
for seeding (far more expensive than enrichment).

## Required API keys

| Service | Purpose |
|---------|---------|
| Apollo | People search, email reveal, org enrich |
| Firecrawl / Apify | Website crawl (Apify default) |
| Apify | LinkedIn company / person / posts |
| Gemini / OpenAI | Website LLM analysis only (`WEBSITE_PROVIDERS`) |
| Google service account | Sheets + contact dedup tabs |

## Project layout

```
agent/
  autopilot/        # URL → three approval gates → optional sequence
  dashboard/        # Ledger + builder + autopilot UI
  integrations/     # Apollo, Firecrawl, Apify, LLM, Sheets
  stages/           # Pipeline stages
  utils/            # ICP rules, client_store, cost dashboard, scoring
scripts/            # Stage CLIs, serve/build dashboard, migrate_client_layout
examples/           # Published campaign template + cost schema samples
data/               # Local only (gitignored) — clients / ICPs / campaigns
tests/
```

## Documentation

| File | Contents |
|------|----------|
| [DATA-LAYOUT.md](DATA-LAYOUT.md) | Client / ICP / campaign folders |
| [SOP-ICP-PLANNING.md](SOP-ICP-PLANNING.md) | Cursor-first ICP drafting |
| [PROJECT.md](PROJECT.md) | Architecture, endpoints, env vars |
| [REQUIREMENTS.md](REQUIREMENTS.md) | Functional requirements |
| [ROADMAP.md](ROADMAP.md) | Built vs planned |
| [SECURITY.md](SECURITY.md) | Secrets and reporting |

Optional local `docs/` (gitignored) may hold operator playbooks — not read by the pipeline.

## Security

See [SECURITY.md](SECURITY.md). Never commit `.env`, credentials, or live client `data/`.

## License

MIT — see [LICENSE](LICENSE).
