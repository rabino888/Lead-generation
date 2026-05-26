# Lead Generation Agent — Project Reference

## Current Status
**First full end-to-end run completed.** All 7 stages verified working locally.
Not yet deployed to Railway — Apollo contact search stalling (~2 hrs/run) must be fixed first.

| | |
|-|-|
| Last completed run | `run_20260524_203709_73b3ef` |
| Output sheet | https://docs.google.com/spreadsheets/d/1wPXOF6XnfFJsEcMm06F9hr5MRzM3u7ph6NhPzFeUChw |
| Client Drive folder | https://drive.google.com/drive/folders/1V3XsGQHJ-eXly4P-N8gWlOzQ_HUs1LBJ |
| Result | 0 qualified / 60 partial leads (Apollo Basic — no emails) |
| Blocking issue | Apollo contact search rate-throttles on Basic plan (~87 min stall) |

---

## What This Is

A multi-tenant B2B lead generation service built as a FastAPI agent on Railway.
Given a keyword, structured ICP, or company list, it autonomously:

1. Discovers companies (Apollo + Apify fallback)
2. Pre-filters against the client's ICP (Claude scoring)
3. Crawls company websites for intelligence (Firecrawl — targeted pages only)
4. Finds decision-maker contacts (Apollo people search)
5. Qualifies and scores each lead (Claude)
6. Writes personalised outreach hooks (Claude)
7. Delivers results to Google Sheets + CSV in the client's Drive folder

## Who Uses It

- **Internal (TBOmedia):** Ravi runs keyword searches to find new agency clients
- **External clients:** Paid subscribers who want qualified B2B leads in their industry

Clients are managed manually via the "LeadGen Clients" Google Sheet. Ravi fills in
the ICP fields directly; the system auto-generates API keys for new rows.

---

## Architecture

```
POST /run
   │
   ▼
FastAPI (main.py)
   │  background task
   ▼
pipeline.py  ──────────────────────────────────────────────────────────
   │                                                                   │
   ├── Stage 1: Discovery                                              │
   │     Apollo mixed_companies/search (primary)                       │
   │     Apify Google Maps fallback (local/niche searches)             │
   │                                                                   │
   ├── Stage 2: Pre-filter                                             │
   │     Claude batch-scores companies 0–100 vs ICP                   │
   │     Drops scores < threshold (default 40)                         │
   │                                                                   │
   ├── Stage 3: Website Enrichment                                     │
   │     Firecrawl targeted crawl (about/team/services/contact/news)   │
   │     Claude analyses markdown → WebsiteAnalysis model             │
   │                                                                   │
   ├── Stage 4: Contact Enrichment                                     │
   │     Apollo mixed_people/search for decision-makers               │
   │     Qualified = has personal email  │  Partial = no email         │
   │     Loop until max_leads hit (cap: 2 extra batches of 20)         │
   │                                                                   │
   ├── Stage 5: Full Qualification                                     │
   │     Claude scores each lead: icp_match_score + lead_score         │
   │     Extracts pain_points, opportunities, qualification_notes      │
   │                                                                   │
   ├── Stage 6: Hook Generation                                        │
   │     Claude writes personalised outreach hook per lead             │
   │     Recommends first service to pitch                             │
   │                                                                   │
   └── Stage 7: Report                                                 │
         Writes CSV to data/clients/{client_id}/runs/                  │
         Creates Google Sheet in client's Drive folder                 │
         Marks all delivered domains in Sheets Dedup tab               │
```

---

## File Structure

```
Lead generation/
├── main.py                          FastAPI app, all endpoints, admin routes
├── agent/
│   ├── pipeline.py                  7-stage orchestrator
│   ├── models.py                    All Pydantic models (Lead, RunState, ICP, etc.)
│   ├── stages/
│   │   ├── discovery.py             Stage 1 — Apollo + Apify
│   │   ├── prefilter.py             Stage 2 — Claude batch scoring
│   │   ├── website.py               Stage 3 — Firecrawl + Claude analysis
│   │   ├── contacts.py              Stage 4 — Apollo people search
│   │   ├── qualify.py               Stage 5 — Claude lead scoring
│   │   ├── hooks.py                 Stage 6 — Claude outreach hooks
│   │   └── report.py                Stage 7 — CSV + Google Sheets writer
│   ├── integrations/
│   │   ├── apollo.py                Apollo REST API (companies + people)
│   │   ├── firecrawl.py             Firecrawl targeted crawl + scrape
│   │   ├── apify.py                 Apify Google Maps scraper
│   │   ├── sheets.py                Google Sheets reader/writer + Dedup tab
│   │   ├── drive.py                 Google Drive folder management
│   │   └── llm.py                   Claude (primary) → OpenAI → Gemini fallback
│   └── utils/
│       ├── auth.py                  API key auth (reads Clients sheet, 5-min cache)
│       ├── deduplication.py         Domain-based dedup via Sheets Dedup tab
│       ├── run_tracker.py           In-memory run state tracker
│       └── logger.py                Per-run structured logger
├── data/
│   └── clients/{client_id}/
│       ├── runs/                    Run logs (.log), CSVs, summary JSONs
│       ├── feedback_log.json        Lead outcome feedback (local — needs migration)
│       └── icp_profile.json         ICP threshold tuning state (local — needs migration)
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
| Website crawling | Firecrawl `/crawl` (targeted) + `/scrape` fallback |
| Client management | Google Sheets (gspread) |
| Output storage | Google Drive + local CSV |
| Auth | API key per client (header: `X-API-Key`) |
| Models | Pydantic v2 |
| Retries | Tenacity |

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Railway health check |
| `POST` | `/run` | Client API key | Start a pipeline run (async) |
| `GET` | `/runs/{run_id}` | Client API key | Poll run status + get results |
| `POST` | `/feedback` | Client API key | Submit lead outcome for feedback loop |
| `POST` | `/admin/clients` | Admin secret | Create a new client (writes to Clients sheet) |
| `GET` | `/admin/clients/{id}/dedup` | Admin secret | How many unique companies delivered |
| `DELETE` | `/admin/clients/{id}/dedup` | Admin secret | Reset dedup registry for a client |

---

## Client Management

Clients are rows in the **"LeadGen Clients"** Google Sheet (`CLIENTS_SHEET_ID`).

| Column | Notes |
|--------|-------|
| `client_id` | Slug, e.g. `tbomedia` |
| `name` | Display name |
| `api_key` | Auto-generated if left blank (written back to the sheet) |
| `icp_industry` | e.g. `marketing and advertising` |
| `icp_location` | e.g. `Madrid` |
| `icp_size_min` | Min employees |
| `icp_size_max` | Max employees |
| `icp_job_titles` | Comma-separated, e.g. `CEO, Founder, Marketing Director` |
| `client_email` | Gets viewer access to their Drive folder |
| `active` | `TRUE` / `FALSE` |

**Workflow:** Ravi adds a complete row → leaves `api_key` blank → on first server auth
request, the key is generated and written back → Ravi copies key from sheet → gives to client.

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

# Google — use FILE for local dev, JSON string for Railway
GOOGLE_SERVICE_ACCOUNT_FILE=leads-generator-497313-b14b9118507e.json
GOOGLE_SERVICE_ACCOUNT={"type":"service_account",...}   # Railway only

CLIENTS_SHEET_ID=14vvUZRZwz8i0D90alhXFv_4coUgxDXQ_c7Cx7gMrAuw
DRIVE_ROOT_FOLDER_ID=1bTO79a7Co5O7THFji4xPj5LcSXQL2dOF

# Agent
ADMIN_SECRET=
AGENT_ENV=development

# Firecrawl credit protection
FIRECRAWL_MAX_PAGES_PER_RUN=25
```

---

## Key Design Decisions

**Apollo-first pipeline (Option C)**
Claude pre-filters company lists cheaply before Firecrawl runs expensive crawls.
This avoids wasting Firecrawl credits on companies that would never pass ICP scoring.

**Qualified vs Partial leads**
A "qualified" lead requires a verified personal email for a decision-maker.
"Partial" leads have everything else but no personal email — still useful for outreach
via LinkedIn or generic company email. Apollo Basic plan produces mostly partial leads.

**Targeted website crawl**
Firecrawl only fetches signal-rich pages: about, team, services, contact, news, case studies.
Avoids crawling pagination, tag archives, individual blog posts. English + Spanish path patterns
for Madrid-area agencies. `max_pages=8` per site, `FIRECRAWL_MAX_PAGES_PER_RUN=25` hard cap.

**Cloud-persistent deduplication**
Domain registry lives in a "Dedup" tab on the Clients Google Sheet — survives Railway redeploys.
Format: `client_id | domain | company_name | date_delivered`.

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
  -H "X-API-Key: lgk-..." \
  -d '{"mode":"keyword","keyword":"digital marketing agency Madrid","max_leads":3}'
```
