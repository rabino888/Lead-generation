# Lead Generation Agent — Claude Context

See the reference documents for full context:

- **README.md** — quick start and funnel commands
- **PROJECT.md** — architecture, file structure, tech stack, endpoints, environment variables
- **REQUIREMENTS.md** — functional and technical requirements, known limitations
- **ROADMAP.md** — what is built and working

Optional local **`docs/`** (gitignored) may hold operator playbooks and client ICP notes — not read by the pipeline.

The pipeline is **industry- and country-agnostic**. All client/industry/geo specifics live under
`data/clients/{client_id}/` (ICPs + campaigns) — never in code defaults or shared scripts.
Compat: `data/campaigns/{id}/` may be a junction into the client campaign folder. See `DATA-LAYOUT.md`.

## Quick orientation for Claude

- FastAPI + uvicorn on Railway. Entry point: `main.py`.
- **Deterministic funnel:** `run_seed_source.py` → `match_campaign_icp.py` →
 `stage_dedupe.py` (pre-Apollo gate) → `stage_apollo.py` → enrich stages → `stage_score.py` →
 stage CSVs under `data/campaigns/{id}/stages/01_*` … `07_qa_flags.csv`.
 Or orchestrate via `run_deterministic_campaign.py` / `POST /run` (curated seeds).
 Post-run: `scripts/build_cost_dashboard.py` then `scripts/serve_cost_dashboard.py` (or `GET /dashboard` on `main.py`).
 Pre-run list builder: `GET /builder` — module toggles + $/lead estimate; saves `enrichment_plan.json` per campaign.
- Campaign scaffolding: per client under `data/clients/{client_id}/icps/` +
 `campaigns/` (`icp.json`, `campaign.json` with `icp_id`, `seed_sources.json`,
 `enrichment_plan.json`, `stages/`). Prefer `/builder` create flow.
- All external API clients are in `agent/integrations/`.
- Current `/run` flow is request-driven: `RunRequest.sender` + `RunRequest.icp` provide client,
  offer, and ICP context directly. Do not reintroduce LeadGen Clients Sheet auth unless asked.
- Google Sheets output and the "Dedup" tab are still used.
- LLM for website stage only: **Gemini Flash → OpenAI mini** (`WEBSITE_PROVIDERS`; auto-discovered via `llm_models.py`). No LLM company prefilter or LLM qualify.
- Apollo email reveal is the contactability gate. Delivered leads require a revealed personal email.
- Post-enrichment score: **keyword overlap** vs `sender.services` / `target_pain_points`; hiring boost when `website_analysis_mode: hiring_first`.
- **Pre-Apollo hard gate** runs in `stage_dedupe.py` (default) — size/mega/recruiter + skip re-attempted domains.
- **04_apollo_contactable.csv:** `write_apollo_contactable_csv` merges by email; never email-only rows.
- **DM LinkedIn:** Apollo must return `linkedin_url` on the decision-maker — no Google-search backup; missing URL = reject. `APOLLO_ALLOW_MISSING_DM_LINKEDIN=1` opts out (legacy).
- Website crawl: **Apify first** (`WEBSITE_CRAWLER=apify` default) via
  `apify/website-content-crawler`; Firecrawl optional when credits restored
  (`WEBSITE_CRAWLER=firecrawl`). Sitemap.xml for URL discovery when not using Firecrawl map.
  Careers: probe + **+1 portal hop** when marketing `/careers` has a CTA/ATS link but no role listings.
- Apify: company profile (`harvestapi/linkedin-company`), person profile (`apimaestro/linkedin-profile-detail`),
  posts (`harvestapi/linkedin-profile-posts`, max 5). **Company open jobs optional** (`APIFY_SKIP_LINKEDIN_JOBS=1` default).
- **Broken / blocked actors:** `harvestapi/linkedin-profile-scraper`, `automation-lab/linkedin-company-scraper`,
  `afanasenko/linkedin-jobs-scraper`. Do not revert.
- Website stage: careers probe (`careers.py`) + `website_analysis_mode` (`hiring_first` | `full`).
- Cost: paid stage CLIs auto-append `cost_runs.json`; prefer `apify_truth_usd` over CostTracker for Apify billing. Lead-list dashboard: `build_cost_dashboard.py` → `data/cost_dashboard.html`; serve via `serve_cost_dashboard.py` or `/dashboard` on FastAPI.
- For all ICPs: web-seeded company discovery (curated seeds / scrape), then Apollo only for
  people/email/phone reveal. **Do not use Apollo `mixed_companies/search` for seeding** — it is
  ~10–50× more expensive than enrichment (`data/campaigns/revolut_smb/run_report.md`).
- `mode: "curated_seeds"` accepts structured `company_seeds` with `company_name`, `website`,
  optional `location`, and optional `source_url`.
- `icp.contact_locations` (e.g. `["Spain"]`) filters the decision-maker PERSON's location
  via Apollo `person_locations[]`. Without it, global companies return contacts anywhere
  in the world. Strict: no matching person = company rejected. Set it for any geo campaign.
- This agent gathers prospect intelligence only — no outreach hooks, emails, or client-facing copy.
- `load_dotenv(override=True)` is required in main.py to override empty system env vars.
- Secrets in `.env` (local dev) or Railway dashboard environment variables (production).

## Current Known Issues (fix before Railway deploy)

1. **Optimize direct company+website seed mode** — **DONE 2026-07-08.** `curated_seeds`
   accepts `company_seeds_csv` (path to a seed CSV; header aliases handled by
   `agent/utils/seeds.py`), and `run_deterministic_campaign.py` runs from stage CSVs or inline seeds.

2. **Production auth is temporarily removed from run endpoints** — `/run`, `/runs/{id}`, and
   `/feedback` are local/request-driven for now. Add a new auth model before exposing publicly.

3. **Deduplicate before expensive enrichment** — **DONE 2026-07-08.** In-run dedupe by
   revealed email/LinkedIn (`_dedupe_leads_by_contact`) plus cross-run contact dedup via the
   Sheets **ContactDedup** tab (`filter_seen_contacts` / `mark_contacts_as_delivered` in
   `agent/utils/deduplication.py`) — both run before LinkedIn posts, Firecrawl, and
   qualification. Catches the same person delivered via parent + subsidiary domains.

4. **Apollo phone webhook** — **deployed** on Railway as standalone repo
   [apollo-phone-webhook](https://github.com/rabino888/apollo-phone-webhook)
   (`apollo-phone-webhook-production.up.railway.app`). Set `APOLLO_PHONE_WEBHOOK_URL` +
   `PUBLIC_BASE_URL` in Lead-generation `.env`. Import via
   `scripts/import_apollo_phone_webhooks.py --fetch-railway`. Never use free webhook.site for batches.

5. **feedback_log.json + icp_profile.json are local files** — wiped on Railway redeploy.
   Must be migrated to Google Sheets before production use.

6. **Deterministic funnel** — **DONE 2026-07-18.** Prefer stage CLIs + `run_deterministic_campaign.py`.
   2026-08 additions: pre-Apollo gate, cost manifest, hiring-first mode, Apollo-only DM LinkedIn gate.

## First Completed Run
`run_20260524_203709_73b3ef` — 60 partial leads, Google Sheet and CSV written, 60 domains
registered in Sheets Dedup tab. All legacy pipeline stages ran successfully end-to-end.
