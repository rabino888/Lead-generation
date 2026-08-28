# Lead Generation Agent — Project Reference

## Current Status
**Deterministic funnel only** (2026-08+). Stage CLIs write inspectable CSVs
under `data/campaigns/{id}/stages/01_*` … `07_*`. Architecture: `PROJECT.md`.
Operator notes may live in local `docs/` (gitignored — not used by the pipeline).

**`POST /run`** accepts curated seeds or Apollo CSV import — same deterministic gates and keyword scoring.
Sender/offer context comes from the request body or campaign `run_payload.json`. Google Sheets
output and Dedup / ContactDedup tabs remain.

**Automata US R&D (`automata_us_rnd`, 2026-08):** 53 contactable enriched; pre-Apollo gate,
hiring-first website mode, cost auto-manifest + dashboard, Apollo-only DM LinkedIn gate. See
`docs/Improvements 24-08.md`.

**Automata batch2 (2026-07-19):** 50 contactable enriched leads (LinkedIn profiles + posts),
Drive sheet + cost log.

**Allyjob Spain (2026-07-06):** 52 contactable leads, 45 phones. Apollo phone webhook on Railway
as a **standalone** service — [apollo-phone-webhook](https://github.com/rabino888/apollo-phone-webhook).

Main Lead-generation app is not yet on Railway; phone webhook **is** live.

| | |
|-|-|
| Decision log | `docs/DECISIONS.md` |
| Pipeline improvements | `docs/Improvements 24-08.md` |
| Automata batch2 sheet | See `data/campaigns/automata/cost_log_batch2_enrich.md` |
| Allyjob master sheet | `data/campaigns/allyjob_spain/campaign_sheet.json` |
| Phone webhook | https://github.com/rabino888/apollo-phone-webhook |

---

## What This Is

A multi-tenant B2B lead generation service built as a FastAPI agent.
Given a curated company+website seed list or Apollo CSV export plus sender offer context,
it produces contactable decision-maker leads with enrichment and keyword scoring.

**Funnel:** web seed scrape → deterministic ICP match → dedup → Apollo people + email reveal
→ Firecrawl + Apify enrich → keyword-overlap score → deliver.
Entry: `scripts/run_deterministic_campaign.py`, `scripts/stage_*.py`, or `POST /run` (curated seeds).
See `docs/OPERATOR-WORKFLOW.md`.

Locked rules for new campaigns:

- Company ICP gate = **deterministic rules only** (`icp.json` — no LLM)
- Apollo gate = people search + email reveal + **DM LinkedIn URL from Apollo**; missing either = drop
- Post-enrichment score = **thin keyword overlap** vs `sender.services` / `target_pain_points`
- Apollo `mixed_companies/search` for seeding = **deprecated**

## Who Uses It

- **Internal operators:** Run curated-seed campaigns for any client
- **External clients:** Paid subscribers who want qualified B2B leads in their industry

For the current local/request-driven flow, clients are not loaded from the LeadGen Clients
Google Sheet. The run request must include a `sender` object with the client's business,
offer, services, proof points, and target pain points — or use a **campaign profile**
under `data/campaigns/{campaign_id}/`.

---

## Architecture

### Deterministic campaign funnel (built)

Operator playbook: `docs/OPERATOR-WORKFLOW.md`. Stage CSVs under
`data/campaigns/{campaign_id}/stages/`.

```
seed_sources.json  →  scrape  →  match_campaign_icp.py  →  dedup + pre_apollo_gate
        │                │              (icp.json)              │
        ▼                ▼                    ▼                   ▼
 01_raw_seeds    02_icp_matched      03_deduped          03_pre_apollo_rejected
                                                      │
                                                      ▼
                              Apollo people + email reveal (no email = drop)
                              + Apollo DM LinkedIn required + write_apollo_contactable_csv
                                                      │
                                                      ▼
                                         04_apollo_contactable
                                                      │
                    ┌─────────────────────────────────┴──────────────────────┐
                    ▼                                                        ▼
         Contact dedup (Sheets ContactDedup)     Firecrawl + Apify (LI profile/posts; jobs optional)
                    │                                                        │
                    └────────────────────────► 05_enriched ◄─────────────────┘
                                                      │
                                                      ▼
                         keyword overlap (+ hiring boost if hiring_first)
                                                      │
                                                      ▼
                                            06_scored → QA → 07_qa_flags
                                                      │
                                    optional: phones / cost_dashboard.html
```

Entry points: `scripts/run_seed_source.py`, `scripts/match_campaign_icp.py`,
independent `scripts/stage_*.py`, `scripts/run_deterministic_campaign.py`.
Post-run: `scripts/open_campaign_cost_dashboard.py`.

---

## File Structure

```
Lead generation/
├── main.py                          FastAPI app, all endpoints, admin routes
├── agent/
│   ├── pipeline.py                  Deterministic orchestrator (curated seeds / Apollo CSV)
│   ├── models.py                    All Pydantic models (Lead, RunState, ICP, etc.)
│   ├── stages/
│   │   ├── discovery.py             Curated seed import
│   │   ├── website.py               Firecrawl + website LLM analysis
│   │   ├── contacts.py              Apollo reveal + Apify LinkedIn posts
│   │   ├── apollo_csv.py            Manual Apollo CSV import
│   │   └── report.py                CSV + Google Sheets writer
│   ├── integrations/
│   │   ├── apollo.py                Apollo REST API (companies + people)
│   │   ├── firecrawl.py             Firecrawl targeted crawl + scrape
│   │   ├── apify.py                 Apify Google Maps + LinkedIn post scraping
│   │   ├── sheets.py                Google Sheets reader/writer + Dedup tab
│   │   ├── drive.py                 Google Drive folder management
│   │   └── llm.py                   Gemini/OpenAI for website JSON
│   ├── webhooks/
│   │   └── apollo_phone.py          Apollo phone callback storage (optional co-deploy)
│   └── utils/
│       ├── auth.py                  Legacy/admin API key auth helper
│       ├── deduplication.py         Domain-based dedup via Sheets Dedup tab
│       ├── run_tracker.py           In-memory run state tracker
│       ├── campaign_config.py       Campaign profile loader (multi-client/geo)
│       └── logger.py                Per-run structured logger
├── data/
│   ├── campaigns/{campaign_id}/     Per-campaign config (see below)
│   └── clients/{client_id}/
│       ├── runs/                    Run logs (.log), CSVs, summary JSONs
│       ├── feedback_log.json        Lead outcome feedback (local — needs migration)
│       └── icp_profile.json         ICP threshold tuning state (local — needs migration)
├── scripts/
│   ├── build_seed_list.py           Build seeds — requires --campaign
│   ├── publish_campaign_sheet.py
│   ├── run_deterministic_campaign.py
│   ├── stage_*.py                   Independent stage CLIs
│   ├── upload_campaign_enriched.py
│   ├── request_apollo_phone_reveals.py   Async Apollo phone reveal requests
│   └── import_apollo_phone_webhooks.py   Merge webhook payloads into CSV/sheets
├── .env                             Local secrets (never committed)
├── requirements.txt
├── railway.toml
├── PROJECT.md                       (this file)
├── REQUIREMENTS.md
└── ROADMAP.md
```

---

## Tech Stack

| Layer | Choice |
|-------|--------|
| Framework | FastAPI + uvicorn |
| Hosting | Railway |
| Primary LLM | Claude `claude-sonnet-4-5` (Anthropic SDK) |
| LLM fallbacks | OpenAI `gpt-4o-mini` → Gemini |
| Company discovery | Apollo.io REST API v1 |
| Local/niche discovery | Apify — `compass/crawler-google-places` |
| Decision-maker post context | Apify LinkedIn posts actor, default `data-slayer/linkedin-profile-posts-scraper` |
| Website crawling | Firecrawl `/crawl` (targeted) + `/scrape` fallback |
| Run client context | Request body `sender` + `icp` |
| Output storage | Google Drive + local CSV |
| Run auth | Temporarily disabled for `/run`, `/runs/{id}`, `/feedback` |
| Models | Pydantic v2 |
| Retries | Tenacity |

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Railway health check |
| `POST` | `/run` | None (temporary) | Start a request-driven pipeline run (async) |
| `GET` | `/runs/{run_id}` | None (temporary) | Poll run status + get results |
| `POST` | `/feedback` | None (temporary) | Submit lead outcome for feedback loop |
| `POST` | `/admin/clients` | Admin secret | Create a new client (writes to Clients sheet) |
| `GET` | `/admin/clients/{id}/dedup` | Admin secret | How many unique companies delivered |
| `DELETE` | `/admin/clients/{id}/dedup` | Admin secret | Reset dedup registry for a client |
| `POST` | `/webhooks/apollo-phone/{secret}` | Path secret | Apollo async phone callback (also in standalone repo) |
| `GET` | `/admin/webhooks/apollo-phone` | Admin secret | Export stored phone webhook payloads |
| `GET` | `/admin/webhooks/apollo-phone/url` | Admin secret | Show configured webhook URL |

**Apollo phone webhook (production):** deployed separately at
https://github.com/rabino888/apollo-phone-webhook — Railway service
`apollo-phone-webhook-production.up.railway.app`. Lead-generation scripts call it via
`APOLLO_PHONE_WEBHOOK_URL` + `--fetch-railway` import. Do not use free webhook.site (>50 requests).

---

## Request Client Context

The normal `/run` path is now request-driven. The request must include:

- `sender.client_id`: used for output paths and dedup/report ownership
- `sender.name`: display name for reports and prompts
- `sender.business_description`: what the sender company does
- `sender.core_offer`: the main offer the agent should sell toward
- `sender.services`: allowed or preferred services for recommendations
- `sender.proof_points`: credibility points qualification can reference
- `sender.target_pain_points`: problems the sender wants to find in prospects
- `sender.positioning_notes`: optional guidance for tone or market positioning

The legacy LeadGen Clients Google Sheet code remains for admin/client management and dedup,
but it is no longer used to authenticate or load client context for `/run`.

---

## Campaign Profiles

Multi-client, multi-geography campaigns are configured per folder — not hard-coded in scripts.
Intake template: `docs/ICP-INTAKE-TEMPLATE.md`.

```
data/campaigns/{campaign_id}/
├── icp.json                 Deterministic company + person rules (Phase 5, Apollo)
├── seed_sources.json        Registered scrapers + source URLs (Phase 2–4)
├── run_payload.json         sender + icp for Apollo/enrichment compatibility
├── campaign.json            Optional: segments, display names, legacy discovery fields
├── seeds.csv                Optional working copy; canonical flow uses stages/
├── stages/                  Funnel artifacts — row counts = run metrics
│   ├── 01_raw_seeds.csv
│   ├── 02_icp_matched.csv
│   ├── 03_deduped.csv
│   ├── 04_apollo_contactable.csv
│   ├── 05_enriched.csv
│   ├── 06_scored.csv
│   └── 07_qa_delivered.csv
├── curated_supplement.json  Optional manual additions
├── campaign_sheet.json      Generated — master Google Sheet metadata
├── batch_state.json         Generated — per-segment progress
└── run_report.md            Post-campaign report (from docs/RUN-REPORT-TEMPLATE.md)
```

**`icp.json`** — deterministic company gate + person ICP:

- `company` — include/exclude keywords, locations, employee range, domain/name exclusions
- `person` — job titles, secondary titles, `contact_locations`, seniorities

**`seed_sources.json`** — ordered list of scrape sources (`curated_csv`, `clutch_directory`,
`linkedin_jobs`, etc.). **No Apollo company search** on new campaigns.

**`run_payload.json`** — offer context for enrichment and legacy runners:

- `sender` — business description, core offer, services, `target_pain_points` (keyword scoring)
- `icp` — mirrors person/company fields for Apollo stage compatibility

**`campaign.json`** (optional / legacy) — segments, `seed_target`, `client_id`, display names.
Legacy `apollo_queries` retained for old folders only — **deprecated for seeding**.

Loader: `agent/utils/campaign_config.py` → `load_campaign(campaign_id)`.

### Campaign script usage

All campaign scripts require `--campaign {campaign_id}`.

**Deterministic funnel:**

```bash
python scripts/run_seed_source.py --campaign {campaign_id} --source csv_ingest --csv path/to/seeds.csv
python scripts/match_campaign_icp.py --campaign {campaign_id}
python scripts/run_deterministic_campaign.py --campaign {campaign_id} --skip-enrichment
python scripts/run_deterministic_campaign.py --campaign {campaign_id} --max-leads 40
```

**Supporting scripts (built today):**

```bash
python scripts/publish_campaign_sheet.py --campaign {campaign_id}
python scripts/upload_campaign_enriched.py --campaign {campaign_id}
python scripts/build_seed_list.py --campaign {campaign_id}   # deprecated — use seed_sources.json
```

ICP intake template: `docs/ICP-INTAKE-TEMPLATE.md`.
Operator playbook: `docs/OPERATOR-WORKFLOW.md`.

---

## Environment Variables

```bash
# LLMs
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=

# Data sources
APOLLO_API_KEY=
FIRECRAWL_API_KEY=
APIFY_TOKEN=
APIFY_LINKEDIN_POSTS_ACTOR=harvestapi/linkedin-profile-posts
APIFY_SKIP_LINKEDIN_JOBS=1              # 0 to enable company open jobs (after batch smoke)
APOLLO_ALLOW_MISSING_DM_LINKEDIN=0       # 1 = legacy email-only contactable (no LI URL required)
APIFY_JOBS_BATCH_USD_GATE=2.0           # smoke_company_jobs_batch.py pass threshold
APIFY_MAX_LINKEDIN_POSTS=5

# Cost tracking
LEAD_COST_CAP_USD=0.70
LEAD_COST_CONSECUTIVE_LIMIT=2
APOLLO_CREDIT_USD=0.02
FIRECRAWL_PAGE_USD=0.01
LLM_PER_1K_TOKENS_USD=0.003

# Pipeline concurrency (optional — defaults shown)
APOLLO_CONTACT_CONCURRENCY=3
APOLLO_MAX_REVEAL_ATTEMPTS=2
LINKEDIN_POST_CONCURRENCY=2
WEBSITE_ENRICHMENT_CONCURRENCY=2
COMPANY_WEBSITE_LOOKUP_CONCURRENCY=3
APOLLO_PHONE_WEBHOOK_URL=   # Full URL, or leave blank and set PUBLIC_BASE_URL + APOLLO_PHONE_WEBHOOK_SECRET
APOLLO_PHONE_WEBHOOK_SECRET=   # Random token; URL becomes {PUBLIC_BASE_URL}/webhooks/apollo-phone/{secret}
PUBLIC_BASE_URL=   # e.g. https://your-app.up.railway.app (RAILWAY_PUBLIC_DOMAIN also works on Railway)

# Google — use FILE for local dev, JSON string for Railway
GOOGLE_SERVICE_ACCOUNT_FILE=leads-generator-497313-b14b9118507e.json
GOOGLE_SERVICE_ACCOUNT={"type":"service_account",...}   # Railway only

CLIENTS_SHEET_ID=14vvUZRZwz8i0D90alhXFv_4coUgxDXQ_c7Cx7gMrAuw
DRIVE_ROOT_FOLDER_ID=1bTO79a7Co5O7THFji4xPj5LcSXQL2dOF

# Agent
ADMIN_SECRET=
AGENT_ENV=development

# Firecrawl uses max 5 pages per company by default; no run-wide page cap is used.
```

---

## Key Design Decisions

**Pre-Apollo hard gate (2026-08)**
`pre_apollo_gate.py` filters megacorps, recruiters, oversize companies, and domains already
attempted in prior Apollo runs before `stage_apollo.py`. Default on in `stage_dedupe.py`.

**Pitch intelligence / hiring-first website (2026-08)**
`icp.json` → `website_analysis_mode: hiring_first` prioritizes careers probe, open roles,
and keyword-score boost for pitch-driven campaigns (e.g. R&D). Agency campaigns use `full`.

**Cost manifest + Apify truth (2026-08)**
Paid stage CLIs append `cost_runs.json` via `finalize_stage_cost()`. Prefer `apify_truth_usd`
from Apify API reconcile over CostTracker alone for PPE actors. Dashboard:
`open_campaign_cost_dashboard.py`.

**DM LinkedIn integrity (2026-08)**
`write_apollo_contactable_csv` merges by email; Apollo must supply DM LinkedIn URL (no Google backup).
Company LinkedIn open jobs remain optional (`APIFY_SKIP_LINKEDIN_JOBS=1`) until batch smoke passes.

**Deterministic company gate**
`icp.json` rules filter seeds before any Apollo or Firecrawl spend. No LLM company prefilter.

**Apollo-first contact gate**
Apollo people search + email reveal is the contactability gate on all paths. No email = drop
before enrichment.

**Qualified vs Partial leads**
A "qualified" lead requires a verified personal email for a decision-maker.
Leads without a revealed email are treated as not delivered/rejected and do not proceed to
Firecrawl/LLM enrichment in the normal Apollo flow.

**Targeted website crawl**
Firecrawl only fetches signal-rich pages: about, team, services, contact, news, case studies.
Avoids crawling pagination, tag archives, individual blog posts. English + Spanish path patterns
are supported. The default is now `max_pages=5` per company with no run-wide blocker.

**Offer-aware prompts**
Qualification must use `RunRequest.sender`. Pain points and opportunities should map to the
sender's actual offer; prompts explicitly forbid generic web design,
SEO, marketing, or IT support recommendations unless those are part of the sender offer.

**Decision-maker LinkedIn context**
After Apollo reveals the selected contact, Apify attempts to fetch up to 5 recent public
LinkedIn posts for that person. This is best-effort and should never block delivery.

**Web-seeded company-list workflow**
For narrow sectors where Apollo company discovery is weak, use web search/directories first
to build a prospect company list, then use Apollo only for decision-maker search and email
reveal. This worked better for the Heify BPO/contact-center ICP: a web-seeded run produced
34 contactable leads from 79 seed companies, later cleaned to 26 unique contacts after
removing duplicate people across aliases and regional subsidiaries. The production version
now supports `mode: "curated_seeds"` with `company_name + website` seeds directly to avoid
slow, noisy per-company website lookup through Apify Google Search.

**Cloud-persistent deduplication**
Domain registry lives in a "Dedup" tab on the Clients Google Sheet — survives Railway redeploys.
Format: `client_id | domain | company_name | date_delivered`.
Contact registry lives in a "ContactDedup" tab on the same sheet — same person is never
delivered twice even across different domains (parent vs subsidiary).
Format: `client_id | contact_key | contact_name | company_name | date_delivered`, where
`contact_key` is `email:<addr>` or `linkedin:<url>`. Both in-run and cross-run contact dedup
run after the contactability gate and **before** LinkedIn posts / Firecrawl / qualification,
so no enrichment spend goes to duplicates.

**Apollo async phone reveal**
Phone numbers are revealed via `people/match` + `reveal_phone_number=true`. Apollo POSTs results
to a webhook URL seconds later (not in the sync response). Production uses the standalone
[apollo-phone-webhook](https://github.com/rabino888/apollo-phone-webhook) Railway service.
Import: `scripts/import_apollo_phone_webhooks.py --fetch-railway`. Request:
`scripts/request_apollo_phone_reveals.py`. Skips CSV rows that already have a phone.

**Apollo search fallback chain**
`full filters → no industry filter → no location filter` — prevents zero-result searches on
Apollo Basic which rejects too-narrow queries.

**Employee cap on Apollo**
Default `organization_num_employees_ranges: ["1,5000"]` prevents Fortune 500 companies
from dominating results when no size filter is specified in the ICP.

---

## Running Locally

```bash
cd "C:\Users\ravi_\TBO-agents\Lead generation"
pip install -r requirements.txt
# Ensure .env exists with all keys
python -m uvicorn main:app --host 127.0.0.1 --port 8001

# Start a run
curl -X POST http://localhost:8001/run \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "keyword",
    "keyword": "law firm Madrid",
    "max_leads": 2,
    "sender": {
      "client_id": "tbo-local",
      "name": "TBO Media",
      "business_description": "AI automation consultancy",
      "core_offer": "AI intake and workflow automation",
      "services": ["AI intake automation", "CRM workflow automation"],
      "proof_points": ["Built lead automation systems"],
      "target_pain_points": ["manual intake", "slow follow-up"]
    },
    "icp": {
      "industry": "law practice",
      "location": "Madrid",
      "company_size_min": 2,
      "company_size_max": 50,
      "job_titles": ["Managing Partner", "Founding Partner", "Director"]
    }
  }'
```

### Curated seed run

Use when companies and websites are already known. Run via campaign orchestrator or API:

```bash
python scripts/run_deterministic_campaign.py --campaign {campaign_id} --max-leads 50
```

Or `POST /run` with `mode: "curated_seeds"` and `company_seeds` / `company_seeds_csv`.

```json
{
  "mode": "curated_seeds",
  "max_leads": 50,
  "sender": {
    "client_id": "heify",
    "name": "Heify",
    "core_offer": "AI-powered call QA automation and conversation intelligence for contact centers",
    "services": ["Automated call QA", "Agent performance analytics"]
  },
  "icp": {
    "industry": "outsourcing/offshoring",
    "location": "Spain, Portugal",
    "job_titles": ["Contact Center Director", "Operations Director", "QA Manager", "CEO"],
    "prefilter_threshold": 25
  },
  "company_seeds": [
    {
      "company_name": "Pluricall",
      "website": "https://www.pluricall.pt/en/pluricall/",
      "location": "Portugal",
      "source_url": "https://www.goodfirms.co/bpo-services/portugal"
    },
    {
      "company_name": "Konecta Spain",
      "website": "https://konecta.com/",
      "location": "Spain"
    }
  ]
}
```
