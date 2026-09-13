# Lead Generation Agent — Roadmap

## Current Status: Deterministic Funnel Only (2026-08-28)

Locked decisions and rationale: **`docs/DECISIONS.md`**. Operator playbook:
`docs/OPERATOR-WORKFLOW.md`. Automata pass improvements: **`docs/Improvements 24-08.md`**.
Pre-run list-building control: **`SPEC-dashboard.md`**; MVP at **`GET /builder`**
(plan save → `enrichment_plan.json`, live $/lead estimate). Post-run cost ledger: `/dashboard`.
Talent search pipeline: **`SPEC-talent-search.md`** (board live; stages TBD).

| Campaign snapshot | Status |
|-------------------|--------|
| **Automata US R&D** (`automata_us_rnd`, 2026-08) | 53 contactable; hiring-first; HTTP cost dashboard; pre-Apollo gate |
| **Automata batch2** (2026-07-19) | 50 contactable, full LinkedIn+website enrich, ~$0.13/lead est. |
| **Allyjob Spain** (2026-07-06) | 52 contactable, 45 phones |
| Phone webhook | Railway standalone (`apollo-phone-webhook` repo) |
| Main FastAPI app | Local only (not on Railway yet) |

The funnel uses `icp.json` rules + keyword-overlap scoring only — no LLM company prefilter or LLM qualify. Entry points:
`scripts/run_seed_source.py`, `scripts/match_campaign_icp.py`, independent `stage_*.py` CLIs,
`scripts/run_deterministic_campaign.py`, and `POST /run` (curated seeds). Stage CSVs under `data/campaigns/{id}/stages/`.

**2026-09 dashboard + billing:** lead-list dashboard (`build_cost_dashboard.py`) over all campaigns;
cost schema v4 (`agent/utils/cost_report.py`); HTTP via `serve_cost_dashboard.py` or `/dashboard` on
`main.py`; LLM mid-tier auto-discovery (`llm_models.py`, website = Gemini → OpenAI only).

**2026-08 hardening (`automata_us_rnd`):** pre-Apollo gate; `website_analysis_mode`
(`hiring_first`); Apollo-only DM LinkedIn gate; `write_apollo_contactable_csv`;
auto-append `cost_runs.json`; optional LinkedIn jobs
gated by `smoke_company_jobs_batch.py`; pytest coverage for gates/scoring/cost manifest.
See `docs/Improvements 24-08.md`.

**2026-07-19 hardening:** stage CSVs persist full enrichment fields; `stage_enrich.py` writes
cost logs; Automata first-list unstructured JSONs uploadable via
`scripts/upload_unstructured_enrichment.py`.

### First Completed Run — `run_20260524_203709_73b3ef`
| Field | Value |
|-------|-------|
| Keyword | `digital marketing agency Madrid` |
| Duration | ~2 hours (22:37 → 00:31) |
| Qualified leads | 0 (Apollo Basic plan has no verified emails) |
| Partial leads | 60 (website intel + scores on all) |
| Firecrawl pages | 50 |
| LLM tokens | 108,061 |
| Google Sheet | https://docs.google.com/spreadsheets/d/1wPXOF6XnfFJsEcMm06F9hr5MRzM3u7ph6NhPzFeUChw |
| Drive folder | https://drive.google.com/drive/folders/1V3XsGQHJ-eXly4P-N8gWlOzQ_HUs1LBJ |
| Dedup domains registered | 60 (written to Sheets Dedup tab ✅) |

---

## What Is Built and Working

### Legacy Pipeline (`agent/pipeline.py` — Stages 1–6)

Used by `POST /run`, `scripts/run_curated_csv.py`, `scripts/run_enrichment_batch.py`.

- **Stage 1 — Discovery:** Apollo company search + Apify Google Maps fallback. Keyword/location
  splitting. Domain dedup vs previous runs. *(Apollo company search deprecated for new seeding.)*
- **Stage 2 — Pre-filter:** Claude batch scoring (LLM). Configurable threshold. **Not used on
  deterministic curated path — FUTURE optional layer only.**
- **Stage 3 — Contacts:** Apollo people search + `people/match` email reveal. No email = drop.
- **Stage 4 — Enrichment:** Apify LinkedIn posts + Firecrawl (5 pages/company) + Claude website
  analysis.
- **Stage 5 — Qualification:** Claude scores vs ICP + sender (LLM). **Replaced on deterministic
  path by keyword overlap — full LLM scoring = FUTURE enhancement.**
- **Stage 6 — Report:** CSV + Google Sheet + Dedup / ContactDedup tabs.

### Deterministic Funnel — Built (2026-07-18)

| Step | Stage artifact | Status |
|------|----------------|--------|
| Seed scrape | `stages/01_raw_seeds.csv` | `run_seed_source.py` — `csv_ingest`, `linkedin_jobs` (Apollo blocked) |
| ICP match | `stages/02_icp_matched.csv` | `match_campaign_icp.py` + `agent/utils/icp_match.py` |
| Dedup + pre-Apollo | `stages/03_deduped.csv` | `stage_dedupe.py` (gate default on) + `stage_pre_apollo.py` |
| Apollo contact | `stages/04_apollo_contactable.csv` | `stage_apollo.py` + merge guard; CostTracker |
| Enrich | `stages/05_enriched.csv` | Firecrawl + Apify; `hiring_first` website mode; repair/LinkedIn-only scripts |
| Keyword score | `stages/06_scored.csv` | `keyword_score.py` (+ hiring boost when configured) |
| QA flags | `stages/07_qa_flags.csv` | Borderline / location / empty-match flags |
| Cost dashboard | SPA `/dashboard` + `data/cost_dashboard_index.json` | `build_cost_dashboard.py`; serve: `serve_cost_dashboard.py` or FastAPI; create/edit: `/builder` |

Campaign contract: `icp.json` + `seed_sources.json` + `stages/` + `run_payload.json`.
Intake template: `docs/ICP-INTAKE-TEMPLATE.md`. Example: `data/campaigns/allyjob_spain/`.

### FUTURE Work (explicit — not on deterministic path today)

1. **LLM company prefilter** — Score ambiguous companies 0–100 before enrichment when
   deterministic rules cannot decide (`agent/stages/prefilter.py` exists for legacy path).
2. **Full LLM lead scoring** — Extract pain points from website + LinkedIn vs sender core
   services and proof points (`agent/stages/qualify.py` exists for legacy path). Keyword
   overlap is the current thin placeholder.
3. **More seed scrapers** — Clutch/GoodFirms/etc. registered beside `csv_ingest` /
   `linkedin_jobs`.

### Infrastructure
- FastAPI async server with `POST /run`, `GET /runs/{id}`, `POST /feedback`, `GET /health`
- Admin endpoints: create client, check/reset dedup registry, export Apollo phone payloads
- Apollo phone webhook: **deployed** as standalone Railway service (`apollo-phone-webhook` repo)
- `/run`, `/runs/{id}`, and `/feedback` are temporarily request-driven and unauthenticated
- Legacy/admin client-sheet API key helpers remain in place for admin/client utilities
- Structured per-run logging to `data/clients/{client_id}/runs/{run_id}.log`
- Apollo + Firecrawl + LLM token usage tracked per run
- LLM fallback chain: Claude → OpenAI → Gemini (any provider failure is silent)

### Infrastructure — Verified
- Apollo company search: ✅ responsive (11,548 results on direct API test)
- Apollo people search: ✅ works in pipeline (returns empty on Basic plan — expected)
- Google Sheet creation per run: ✅ verified
- Google Drive folder creation per client: ✅ verified
- LLM fallback chain: ✅ Claude primary confirmed working (`claude-sonnet-4-5`)
- Request-driven client construction: implemented
- API key auto-generation from blank sheet rows: implemented, not yet tested

### Persistence (Cloud-Ready)
- Dedup registry: Google Sheets "Dedup" tab — survives Railway redeploys ✅ (60 domains written)
- Run client context: request body `sender` + `icp`
- Legacy client management: Google Sheets "LeadGen Clients" remains available for admin utilities
- feedback_log.json: ⚠️ still local — lost on Railway redeploy (not yet migrated)
- icp_profile.json: ⚠️ still local — lost on Railway redeploy (not yet migrated)
- run_tracker: ⚠️ in-memory — run history lost on server restart (not yet persisted)

---

## Immediate Next Steps

### 0. Deterministic Funnel Implementation [DONE 2026-07-18]

Shipped:

- `scripts/match_campaign_icp.py`, `run_seed_source.py`, `run_deterministic_campaign.py`
- `icp.json` / `seed_sources.json` loaders in `campaign_config.py` + `icp_rules.py`
- Stage CSV writers under `data/campaigns/{id}/stages/`
- Keyword-overlap scorer (`agent/utils/keyword_score.py`) on `RunRequest.deterministic=True`

Do **not** invoke LLM prefilter/qualify on the deterministic curated path.

### 1. Campaign-Config-Driven Seed Building [DONE]
Campaign scripts now load client, geography, segments, and discovery rules from
`data/campaigns/{campaign_id}/campaign.json` + `run_payload.json`. Pass
`--campaign tbomedia_bpo` or `--campaign allyjob_spain` to all campaign scripts.
Shared loader: `agent/utils/campaign_config.py`.

### 2. Optimize Direct `company_name + website` Seed Mode [DONE 2026-07-08]
The Heify BPO/contact-center run proved that web-seeded discovery works better than Apollo
company discovery for narrow sectors. `mode: "curated_seeds"` accepts structured seeds
with `company_name`, `website`, optional `location`, and optional `source_url`, then skips
website lookup and goes straight to prefilter and Apollo people reveal.
CSV import shipped: `RunRequest.company_seeds_csv` (header aliases via `agent/utils/seeds.py`,
merged with inline `company_seeds`) and `scripts/run_curated_csv.py` to run any seed CSV with
a campaign's `run_payload.json` or a standalone `{sender, icp}` JSON — no campaign folder
needed. Remaining nice-to-have: a review UI/export helper for building seed lists from web
directories.

### 3. Batch and Parallelize Slow Stages
- Resolve seed websites in batches instead of one Apify actor run per company.
- Run Apollo contact searches with controlled concurrency and rate-limit backoff.
- Run LinkedIn post enrichment only after deduping revealed contacts.
- Cache Firecrawl results by domain and avoid crawling duplicate domains/subsidiaries.

### 4. Validate Apify LinkedIn Posts Actor
The implementation defaults to `data-slayer/linkedin-profile-posts-scraper`, with
`APIFY_LINKEDIN_POSTS_ACTOR` available as an override. Actor input/output formats can vary, so
test one real LinkedIn profile and adjust normalization in `agent/integrations/apify.py` if needed.

### 5. Decide Production Auth Model
`/run`, `/runs/{id}`, and `/feedback` are intentionally unauthenticated for the current local
request-driven workflow. Before external deployment, add auth that still allows sender context
to be request-provided.

### 6. Migrate Local Files to Google Sheets
Two files still live locally and will be lost on Railway redeploy:
- `feedback_log.json` → new "Feedback" tab in Clients spreadsheet
- `icp_profile.json` → new "Thresholds" column on the Clients sheet

### 7. Deploy Main Agent to Railway
Pre-requisites: production auth decision for `/run`.
The **Apollo phone webhook** is already deployed separately — do not bundle it required for
phone reveals. Required steps for the main agent:
- Set all env vars in Railway dashboard (use `GOOGLE_SERVICE_ACCOUNT` JSON string)
- Push repo to GitHub
- `railway up` from the project directory
- Verify `/health` passes Railway health check
- Run one smoke test from the Railway URL

### 8. Apollo Phone Reveal Pipeline [DONE 2026-07-06]
- Standalone repo: https://github.com/rabino888/apollo-phone-webhook
- Railway: `apollo-phone-webhook-production.up.railway.app`
- Scripts: `request_apollo_phone_reveals.py`, `import_apollo_phone_webhooks.py --fetch-railway`
- Allyjob result: 45/52 phones across canonical + evening expansion runs
- **Do not use webhook.site** — 50-request free cap silently drops Apollo callbacks

### 9. Website Content Compression Before Downstream Use [ADDED 2026-08-07]

**Problem.** The website stage hands raw Firecrawl markdown straight to whatever consumes it.
Measured across the 22-lead Automata enrichment batch, per-company payloads ranged from
**826 to 115,707 characters** — a 140× spread, with a median around 26k. That raw text goes
into the CSV `website_summary`, into scoring, and into the `outreach-content-agent` prompts.

Three costs, in order of how much they actually hurt:

1. **Evidence-tracing gets harder, and that's the expensive one.** The QA gate traces every
   claim to a source field. When the source field is 90k characters of navigation, cookie
   banners, and boilerplate, "traces to `website_summary`" stops meaning anything — the check
   passes while the claim floats. This is the same class of failure as the fabricated hiring
   column: a field that *looks* like a legitimate source.
2. **Signal dilution.** The useful content — services, credentials, team, recent events, social
   proof — is a small fraction of the payload.
3. **Token cost**, which is the least interesting of the three but scales linearly with batch size.

**Proposed approach — extraction first, RAG only if extraction proves insufficient.**

- **Phase 9a — structured extraction (do this first).** One LLM pass per company producing a
  fixed schema: `services[]`, `credentials[]`, `team[]`, `recent_events[]` (each with a date and
  a verbatim quote), `social_proof[]`, `content_activity`, plus `raw_char_count` and
  `extraction_confidence`. Deterministic boilerplate stripping (cookie banners, nav, footers)
  runs *before* the LLM sees the page. Every extracted item keeps a **verbatim source quote**,
  so downstream claim-tracing points at a sentence rather than a document.
- **Phase 9b — RAG, only if 9a is insufficient.** A vector store over page chunks, queried per
  generation need. Worth doing only if 9a demonstrably loses information the outreach needs;
  it adds an index to build, keep fresh, and debug, and per the deterministic-first rule in
  `.claude/rules/agent-architecture.md` that infrastructure should have to earn its place.

**Keep the raw markdown.** Compression is additive — `website_markdown` stays on disk so an
extraction bug is re-runnable without re-crawling (Firecrawl pages are the paid resource here).

**Detect thin crawls properly.** The existing length heuristic gets it wrong in both directions:
Ralio's 826 characters is a genuinely failed crawl, but Hadlow Edwards' crawl opened with a
cookie preamble followed by real content and was wrongly discarded. Post-boilerplate-strip
character count is the right signal, not raw length.

**Acceptance:** re-run extraction over the 22 existing enrichment JSONs and confirm every claim
in the current audited emails is still supported by an extracted field with its source quote.
That batch is a ready-made regression fixture — it's already been through a human grounding
audit, so anything the extractor drops is a measurable regression rather than a judgement call.

Source: `Leads generation unstructured/docs/specs/2026-08-06-outreach-newsletter-orchestrator-spec.md` §10.2

---

## Short-Term Improvements (Next 1–2 Weeks)

### Better Discovery Quality
- Deterministic ICP match replaces LLM prefilter for curated campaigns — tune `icp.json` rules
  when good companies drop (low keyword hit ≠ bad company when Apollo data is sparse)
- **FUTURE:** Optional LLM prefilter for ambiguous rows only (`score < threshold AND completeness < 30%`)
- Add Hunter.io as an email enrichment fallback when Apollo returns no personal email
- For niche ICPs, prefer web-seeded/scraped discovery over Apollo company search.
- ~~Deduplicate seed companies by domain and deduplicate revealed contacts by email/LinkedIn before
  LinkedIn post scraping, Firecrawl, qualification, and final output.~~ **DONE 2026-07-08** —
  seed domain dedup in discovery, in-run contact dedup in pipeline, cross-run contact dedup via
  Sheets ContactDedup tab.

### Apollo Contact Search Hardening
- Current: Apollo people search followed by paid reveal for selected candidates
- Improve: try multiple job title variants if first search returns nothing
- Add rate-limit retry with exponential backoff specifically for Apollo 429 responses

### Run Completion Notification
- Add optional `callback_url` field to `RunRequest`
- On pipeline completion, POST the result JSON to the callback URL
- Enables integration with Zapier/Make/n8n workflows for client notification

### Persist Run History
- `run_tracker` is in-memory — history lost on server restart
- Option A: write each run's summary JSON to a "Runs" tab in the Clients spreadsheet
- Option B: use Railway's attached SQLite volume
- Impact: enables run history endpoint, analytics, billing tracking

---

## Medium-Term (Next Month)

### Email Finding Fallback
Apollo Basic returns verified personal emails for <10% of contacts.
Add a secondary enrichment step after Apollo comes back empty:
1. Find the decision-maker's name from Apollo or website
2. Try Hunter.io `email-finder` API to construct the likely email
3. Verify with Hunter.io `email-verifier`
4. If confidence > 80%, classify as qualified

### Simple Admin Dashboard
A minimal read-only web page (or Notion/Airtable view) showing:
- All client runs with status and link to Google Sheet
- Credit usage per run
- Dedup count per client

### Smarter ICP Inference
Currently uses keyword → fixed industry string mapping.
Improve to have Claude infer the best Apollo industry filters from the keyword + ICP context.
This reduces the need for the no-industry fallback search.

---

## Long-Term Vision

### Multi-Tenant Billing
- Track Apollo credits and Firecrawl pages consumed per client per month
- Add a `billing_tier` to the Clients sheet (free / starter / pro)
- Block runs when a client exceeds their monthly quota

### Automated Client Onboarding
- Webhook from Stripe/payment provider triggers client row creation
- Welcome email with API key sent automatically
- First run auto-triggered with default settings

### Feedback-Trained Scoring
- Accumulate feedback entries across runs
- Use them as few-shot examples in the qualification prompt
- Over time the scoring model learns what "converted" looks like for each client

### Alternative Discovery Sources
- LinkedIn Sales Navigator (when Apollo has gaps)
- Crunchbase for funding-signal discovery
- G2/Capterra for intent data (companies actively evaluating tools)
