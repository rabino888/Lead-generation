# Lead Generation Agent — Roadmap

## Current Status: End-to-End Pipeline Working, Pre-Production

The full 7-stage pipeline runs successfully. All core integrations are connected.
The system is not yet deployed to Railway — currently running locally for testing.

---

## What Is Built and Working

### Core Pipeline (Stages 1–7)
- **Stage 1 — Discovery:** Apollo search with industry filter + employee cap + progressive
  fallback (full → no-industry → no-location). Apify Google Maps fallback for local searches.
  Keyword/location splitting from natural language. Deduplication vs previous runs.
- **Stage 2 — Pre-filter:** Claude batch scoring (10 companies/call, 2000 tokens). Configurable
  threshold per client (default 40/100). Reliably drops non-ICP companies before Firecrawl.
- **Stage 3 — Website Enrichment:** Firecrawl targeted crawl — only fetches about, team,
  services, contact, news pages (English + Spanish path patterns). Per-run credit cap.
  Falls back to single-page scrape. Page URLs labelled in output for LLM context.
- **Stage 4 — Contact Enrichment:** Apollo people search by job title. Qualified vs partial
  classification. Loop with max 2 extra batches of 20 companies to hit `max_leads` quota.
- **Stage 5 — Qualification:** Claude scores each lead (icp_match_score + lead_score).
  Extracts pain points, opportunities, qualification notes. Leads sorted by score.
- **Stage 6 — Hook Generation:** Claude writes personalised first-line outreach hook per lead
  referencing specific website intelligence. Recommends first service to pitch.
- **Stage 7 — Report:** CSV saved locally. New Google Sheet created in client's Drive folder
  per run. Qualified leads at top, partial leads below. AI recommendations section appended.

### Infrastructure
- FastAPI async server with `POST /run`, `GET /runs/{id}`, `POST /feedback`, `GET /health`
- Admin endpoints: create client, check/reset dedup registry
- Per-client API key auth with 5-minute cache (reads from Google Sheets)
- API key auto-generation: if a client row has no key, one is written back to the sheet
- Structured per-run logging to `data/clients/{client_id}/runs/{run_id}.log`
- Apollo + Firecrawl + LLM token usage tracked per run
- LLM fallback chain: Claude → OpenAI → Gemini (any provider failure is silent)

### Persistence (Cloud-Ready)
- Dedup registry: Google Sheets "Dedup" tab — survives Railway redeploys ✅
- Client management: Google Sheets "LeadGen Clients" — source of truth for ICP + keys ✅

---

## Immediate Next Steps

### 1. Verify Targeted Crawl in Production
The `includePaths` targeted crawl was just implemented. Needs a complete clean run to confirm:
- Firecrawl respects the path filters and returns only relevant pages
- The LLM receives enough signal from 3–8 targeted pages vs 10 random pages
- Credit consumption is within budget for a 3-lead run

### 2. Diagnose Apollo Rate Throttling
During testing, Apollo contact search stalled for 87 minutes between companies 9 and 10.
Root cause: too many `mixed_people/search` calls in quick succession on Apollo Basic.
Fix options:
- Add a configurable inter-request delay (e.g. 2–3 seconds between contact searches)
- Batch contact searches where possible
- Log the HTTP status codes from Apollo to confirm it is rate-limiting vs other error

### 3. Migrate Local Files to Google Sheets
Two files still live locally and are wiped on Railway redeploy:
- `feedback_log.json` → new "Feedback" tab in Clients spreadsheet
- `icp_profile.json` → new column or "Thresholds" tab in Clients spreadsheet

### 4. Deploy to Railway
Required steps:
- Set all env vars in Railway dashboard (use `GOOGLE_SERVICE_ACCOUNT` JSON string)
- Push repo to GitHub (Fenix-create936 bot account)
- `railway up` from the project directory
- Verify `/health` passes Railway health check
- Run one smoke test from Railway URL

---

## Short-Term Improvements (Next 1–2 Weeks)

### Better Discovery Quality
- The pre-filter currently drops good agencies with low Apollo data (low score = unknown, not bad)
- Consider treating `score < threshold AND data_completeness < 30%` as "unknown, crawl anyway"
  rather than "bad, drop"
- Add Hunter.io as an email enrichment fallback when Apollo returns no personal email

### Apollo Contact Search Hardening
- Current: one Apollo people search per company, one result used
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
