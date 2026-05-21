# Lead Generation Agent — Claude Context

## What this agent does
A fully qualified B2B lead generation agent. Given a keyword, structured ICP, or uploaded company list,
it discovers companies, enriches them with Apollo + Firecrawl, qualifies leads with Claude,
writes personalised outreach hooks, and delivers results to CSV + Google Sheets.

## Architecture
- **Framework:** FastAPI + uvicorn on Railway
- **Pipeline:** 7-stage async pipeline (discover → prefilter → website → contacts → qualify → hooks → report)
- **Primary LLM:** Claude (Anthropic) — falls back to OpenAI then Gemini
- **Discovery:** Apollo.io (primary), Apify (fallback for local/niche)
- **Website enrichment:** Firecrawl
- **Contact enrichment:** Apollo.io
- **Client management:** Google Sheets ("LeadGen Clients" sheet)
- **Output:** CSV + Google Sheets tab per run

## Key design decisions
- Apollo-first (Option C): Apollo discovers, Claude pre-filters cheaply before Firecrawl runs
- Runs are async: POST /run returns run_id immediately, poll /runs/{run_id} for status
- Qualified lead = must have decision-maker name + direct email
- Partial leads (no decision-maker) go to end of report with company generic email
- max_leads quota counts QUALIFIED leads only
- Per-client isolation: each client has their own data folder, feedback log, ICP profile
- Apollo basic plan (5,000 credits/month): intent signals / technographics are premium features,
  wrapped in graceful fallbacks — pipeline works without them, uses them if available

## Input modes
1. keyword — "marketing agencies Madrid"
2. icp — structured {industry, location, size, job_titles, keywords}
3. company_list — CSV of company names ± URLs (Apify finds missing websites)

## Project structure
```
main.py                    FastAPI app + endpoints
agent/
  pipeline.py              Orchestrates all 7 stages
  models.py                Pydantic models
  stages/                  One file per pipeline stage
  integrations/            Apollo, Firecrawl, Apify, Sheets, LLM clients
  utils/                   Auth, logger, run tracker
data/clients/{client_id}/  Per-client data (runs, feedback, ICP profile)
outputs/                   Generated CSVs
```

## Endpoints
- POST /run         — start a run (async, returns run_id)
- GET  /runs/{id}   — poll run status + results
- POST /feedback    — submit lead outcomes for feedback loop
- GET  /health      — Railway health check

## Environment variables
See .env.example for full list.
Key: ANTHROPIC_API_KEY, APOLLO_API_KEY, FIRECRAWL_API_KEY, APIFY_TOKEN,
     GOOGLE_SERVICE_ACCOUNT, CLIENTS_SHEET_ID, OUTPUTS_SHEET_ID

## Reference pattern
Follow meeting-processor agent at C:\Users\ravi_\TBO-agents\meeting-processor\ for
Railway deployment, Google auth, and FastAPI patterns.
